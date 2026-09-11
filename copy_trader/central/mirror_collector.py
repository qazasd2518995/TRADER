"""把一個「別人在操作的 MT5 帳戶」的部位，即時鏡像成超高頻訊號。

來源是代理商在伺服器端操作的帳戶（Lirunex 502009168）。我們**只讀**它：
橋接 EA 回報的是帳戶實際狀態，不管那些單是誰下的，所以完全不需要在那台
安裝任何東西，也永遠不對它下單。

為什麼是「部位鏡像」而不是「發訊號」
  那個策略沒有停損也沒有止盈（186 筆歷史成交全是 SL=0 TP=0），進場出場
  都由它自己決定。所以能抄的只有兩件事：它開倉時我們開、它平倉時我們平。
  中間不設 SL/TP —— 設了反而會在它還沒想出場時把我們掃掉。

為什麼進場一定是市價
  它是市價進場。我們慢 2~4 秒，如果發限價單，價格早就走過去了 ——
  而且 mt5_client_agent 有一道「本地已穿價就略過」的檢查，那種單會被整批
  丟掉，根本不會成交。市價單才抄得到。

偵測方式
  比對兩輪之間的 positions.json：
    出現新的 ticket  → 開倉事件
    ticket 消失      → 平倉事件
  ticket 是 MT5 的部位編號，同一個帳戶內唯一且不重用，拿它當身分最可靠 ——
  比對「筆數」或「手數總和」在網格策略下會錯得很離譜（同時開好幾腳）。

刻意不做的事
  **不碰來源帳戶。** 這支只讀檔案，沒有任何寫入路徑。那個帳戶是實倉，
  而且不是我們的錢。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)

# 橋接檔的編碼不保證是 UTF-8（帳號名帶券商的 ANSI 碼頁）。latin-1 墊底：
# 它永遠不會失敗，最壞只是名字變亂碼，但數字欄位一個都不會少。
_ENCODINGS = ("utf-8-sig", "utf-8", "cp950", "latin-1")

# 部位消失要連續看到這麼多輪才當成平倉。EA 寫檔與我們讀檔沒有同步，
# 讀到寫到一半的空陣列就誤判成「全部平倉」，會把所有鏡像部位一次砍掉。
VANISH_CONFIRM_ROUNDS = 2

# 來源那邊開倉超過這麼久才被我們看到，就不要追了。訊號中心重啟或斷線之後
# 會一次看到一堆「新」部位，那些其實是舊的 —— 追進去等於用現在的價格去接
# 一個幾十分鐘前的進場，跟原單完全不是同一回事。
MAX_ENTRY_AGE_SECONDS = 90.0


def _read_json(path: Path) -> Optional[Any]:
    """EA 隨時可能正在覆寫，讀到半截就回 None 讓呼叫端跳過這一輪。"""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    for encoding in _ENCODINGS:
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


@dataclass
class MirrorState:
    """上一輪看到的部位。只留鏡像需要的欄位。"""

    known: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    vanished: Dict[int, int] = field(default_factory=dict)   # ticket -> 連續消失輪數
    started_at: float = field(default_factory=time.time)
    primed: bool = False


class MirrorCollector:
    """盯著來源帳戶的 positions.json，把開平倉轉成 Hub 事件。

    publish 是外部注入的（訊號中心傳 HubPublisher.publish 進來），所以這支
    本身不知道 Hub 的存在，測試時塞一個 list.append 就能驗。
    """

    def __init__(self, files_dir: str | Path, publish: Callable[[Dict[str, Any]], Any],
                 *, source: str, symbol: str = "XAUUSD",
                 comment_filter: str = "", magic_filter: Optional[int] = None,
                 state: Optional[MirrorState] = None):
        self.files_dir = Path(files_dir)
        self.publish = publish
        self.source = source
        self.symbol = symbol
        # 只抄符合條件的單。來源帳戶如果同時有人工單，這裡可以把它們濾掉。
        self.comment_filter = comment_filter
        self.magic_filter = magic_filter
        self.state = state or MirrorState()

    # ---------- 讀取 ----------

    def _positions(self) -> Optional[Dict[int, Dict[str, Any]]]:
        data = _read_json(self.files_dir / "positions.json")
        if not isinstance(data, dict):
            return None
        rows = data.get("positions")
        if not isinstance(rows, list):
            return None
        out: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                ticket = int(row.get("ticket") or 0)
            except (TypeError, ValueError):
                continue
            if ticket <= 0 or not self._wanted(row):
                continue
            out[ticket] = row
        return out

    def _wanted(self, row: Dict[str, Any]) -> bool:
        if self.comment_filter and self.comment_filter not in str(row.get("comment") or ""):
            return False
        if self.magic_filter is not None:
            try:
                if int(row.get("magic") or 0) != int(self.magic_filter):
                    return False
            except (TypeError, ValueError):
                return False
        return True

    # ---------- 一輪 ----------

    def run_cycle(self) -> int:
        """比對一次，發布開平倉事件。回傳這輪發了幾個事件。"""
        current = self._positions()
        if current is None:
            return 0            # 讀不到或讀到半截：什麼都不做，下一輪再說

        if not self.state.primed:
            # 第一輪只記錄不發布。啟動當下已經開著的部位是「過去式」——
            # 現在才用市價追進去，跟原單的進場價差可能很大。
            self.state.known = dict(current)
            self.state.primed = True
            if current:
                logger.info("鏡像來源啟動時已有 %s 個部位，記錄但不跟進", len(current))
            return 0

        published = 0
        for ticket, row in current.items():
            if ticket not in self.state.known:
                if self._publish_open(ticket, row):
                    published += 1
            self.state.vanished.pop(ticket, None)

        for ticket in list(self.state.known):
            if ticket in current:
                continue
            rounds = self.state.vanished.get(ticket, 0) + 1
            self.state.vanished[ticket] = rounds
            if rounds < VANISH_CONFIRM_ROUNDS:
                continue        # 可能只是讀到寫到一半的檔案，再看一輪
            if self._publish_close(ticket, self.state.known[ticket]):
                published += 1
            self.state.vanished.pop(ticket, None)
            self.state.known.pop(ticket, None)

        for ticket, row in current.items():
            self.state.known[ticket] = row
        return published

    # ---------- 事件 ----------

    def execution_id(self, ticket: int) -> str:
        """來源部位編號決定的 ID，同一個部位永遠算出同一個值。

        重啟之後重算得到一樣的 ID，所以平倉事件對得回當初那張單 —— 不需要
        另外存一份對照表，也不怕那份表掉了。
        """
        return f"mirror-{self.source_key()}-{ticket}"

    def source_key(self) -> str:
        return "ultra"

    def _publish_open(self, ticket: int, row: Dict[str, Any]) -> bool:
        age = self._age(row)
        if age is not None and age > MAX_ENTRY_AGE_SECONDS:
            logger.info("來源部位 %s 已開倉 %.0f 秒才被看到，超過 %.0f 秒不追",
                        ticket, age, MAX_ENTRY_AGE_SECONDS)
            return False
        direction = str(row.get("type") or "").lower()
        if direction not in ("buy", "sell"):
            return False
        exec_id = self.execution_id(ticket)
        payload = {
            "event_id": f"{exec_id}-open",
            "type": "trade_signal",
            "execution_id": exec_id,
            "source": self.source,
            "source_name": "mirror",
            "sender": "mirror",
            "line_chat_id": "mirror",
            "line_message_id": exec_id,
            "line_rowid": 0,
            "line_revision": 1,
            "message_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "signal_index": 0,
            # **出場由來源驅動**。會員端看到這個旗標才會接受沒有 SL/TP 的訊號；
            # 一般 LINE 訊號沒有這個旗標，缺停損一律照舊擋下來。
            "managed_exit": True,
            "mirror": {"ticket": ticket, "source_volume": row.get("volume"),
                       "source_price": row.get("price_open")},
            "signal": {
                "symbol": self.symbol,
                "direction": direction,
                # 市價進場：來源是市價進的，我們慢幾秒，掛限價會被「已穿價」
                # 檢查整批丟掉，根本進不了場。
                "entry_price": None,
                "is_market_order": True,
                "stop_loss": None,
                "take_profit": [],
                "lot_size": None,
                "parse_status": "ok",
                "parse_method": "mirror",
                "raw_text_summary": f"鏡像 {direction} 來源部位 {ticket}",
                "error": None,
            },
        }
        self.publish(payload)
        logger.info("鏡像開倉：來源 ticket=%s %s %s 手 @ %s",
                    ticket, direction, row.get("volume"), row.get("price_open"))
        return True

    def _publish_close(self, ticket: int, row: Dict[str, Any]) -> bool:
        exec_id = self.execution_id(ticket)
        self.publish({
            "event_id": f"{exec_id}-close",
            "type": "close_signal",
            "source": self.source,
            "source_name": "mirror",
            "line_chat_id": "mirror",
            "line_message_id": f"{exec_id}-close",
            "target_execution_ids": [exec_id],
            "close_reason": "mirror_source_closed",
            "line_revision": 2,
            "message_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "mirror": {"ticket": ticket, "source_price": row.get("price_open")},
        })
        logger.info("鏡像平倉：來源 ticket=%s 已消失", ticket)
        return True

    def _server_offset(self) -> Optional[float]:
        """伺服器時間跟真實時間差幾秒。算不出來回 None。

        不能寫死時區：自家幾台券商就有 +0 和 +3 兩種，而且同一家不同帳戶
        類型還會不一樣。account_info 裡的 timestamp 是 TimeCurrent()（伺服器
        時間），檔案的 mtime 是這台電腦寫檔的真實時間，兩者相減就是偏移。
        """
        path = self.files_dir / "account_info.json"
        data = _read_json(path)
        if not isinstance(data, dict):
            return None
        try:
            server_now = float(data.get("timestamp") or 0)
            written_at = path.stat().st_mtime
        except (TypeError, ValueError, OSError):
            return None
        if server_now <= 0:
            return None
        return server_now - written_at

    def _age(self, row: Dict[str, Any]) -> Optional[float]:
        """這個部位開了多久（秒）。算不出來回 None —— 那就不做年齡判斷。

        寧可多追一筆，也不要因為時區算錯而把每一筆都誤判成過期而全部不追。
        """
        try:
            opened = float(row.get("time_open_timestamp") or 0)
        except (TypeError, ValueError):
            return None
        if opened <= 0:
            return None
        offset = self._server_offset()
        if offset is None:
            return None
        return time.time() - (opened - offset)
