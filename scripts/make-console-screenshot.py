#!/usr/bin/env python3
"""用真實的會員端前端產生官網要用的介面截圖。

為什麼不直接畫一張漂亮的圖：官網上放的「產品畫面」如果不是產品真的長的樣子，
使用者裝完會發現不一樣，那是最傷信任的一種落差。所以這支腳本走的是真路徑 ——
餵一組合成的 MT5 資料檔進去，讓 `copy_trader.central.stats.build_stats()`
真的跑一遍，再把 `webui.py` 的樣板渲染出來。畫面上每一個數字都是產品自己算的。

用法：
    python3 scripts/make-console-screenshot.py            # 起 server 在 :8199
    python3 scripts/make-console-screenshot.py --port 9000

起來之後用瀏覽器截圖。資料是固定種子產生的，每次跑都一樣。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SEED = 20260824
SYMBOL = "XAUUSD"
COPY_MAGIC = "990001"          # 本系統的魔術編號
EA_MAGIC = "20260503"          # 另一顆 EA（趨勢線策略）

# 直接引用後端常數，不要另外抄一份字串 —— 抄了就會像先前那樣對不起來。
from copy_trader.central.membership import MID_FREQ, HIGH_FREQ

SOURCES = {
    "mid": MID_FREQ,
    "high": HIGH_FREQ,
}


def build_fake_mt5_dir(root: Path) -> None:
    """造出 EA 平常會寫的那幾個檔案。欄位名稱依 stats.build_stats() 的讀法。"""
    rnd = random.Random(SEED)
    now = int(time.time())

    trades = []
    sources_map = {}
    journal_lines = []
    price = 2_310.0

    # 兩週、42 筆已平倉。勝率抓 ~76%，跟馬丁的形狀吻合（多小賺、偶爾一次較大虧損）
    # 前 20 筆散在「今天」（前端預設期間是 today，不然一開畫面是空的），
    # 其餘 22 筆往前鋪兩週，讓本週／本月／全部都有東西可看。
    day_start = now - (now % 86_400)
    for i in range(42):
        if i >= 22:
            closed_at = day_start + int((i - 22) / 20 * (now - day_start - 600)) + rnd.randint(0, 300)
        else:
            closed_at = now - (22 - i) * 43_200 - rnd.randint(0, 9_000)
        opened_at = closed_at - rnd.randint(900, 9_000)
        src_key = "mid" if i % 3 else "high"
        source = SOURCES[src_key]
        sig = f"sig{i:03d}"

        win = rnd.random() < 0.76
        side = "buy" if rnd.random() < 0.55 else "sell"
        volume = rnd.choice([0.01, 0.01, 0.02, 0.02, 0.04, 0.08])
        move = rnd.uniform(2.2, 9.5) if win else rnd.uniform(3.0, 14.0)
        profit = round(move * volume * 100 * (1 if win else -1), 2)

        entry = round(price + rnd.uniform(-6, 6), 2)
        exit_ = round(entry + (move if (side == "buy") == win else -move), 2)
        price += rnd.uniform(-4, 6)

        # closed_trades 的 type 是「平倉成交」的方向，跟持倉方向相反
        close_type = "sell" if side == "buy" else "buy"

        trades.append({
            "ticket": 500_000 + i,
            "position_id": 400_000 + i,
            "symbol": SYMBOL,
            "type": close_type,
            "magic": COPY_MAGIC,
            "comment": f"copy_{sig}",
            "volume": volume,
            "entry_price": entry,
            "exit_price": exit_,
            "sl": round(entry - 12 if side == "buy" else entry + 12, 2),
            "tp": round(entry + 8 if side == "buy" else entry - 8, 2),
            "profit": profit,
            "change_percent": round(move / entry * 100, 3),
            "open_timestamp": opened_at,
            "close_timestamp": closed_at,
            "close_time": time.strftime("%Y.%m.%d %H:%M", time.gmtime(closed_at)),
        })
        sources_map[f"copy_{sig}"] = source   # build_stats 用整個 comment 當 key
        journal_lines.append(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(opened_at))}] 送出訂單\n"
            f"  訊號={sig} | 來源={source} | 方向={side} | 手數={volume}\n"
        )

    # 另一顆 EA（趨勢線策略）的單，讓報表顯示混合來源
    for j in range(6):
        closed_at = now - (6 - j) * 21_600
        profit = round(rnd.uniform(-40, 95), 2)
        trades.append({
            "ticket": 700_000 + j,
            "position_id": 600_000 + j,
            "symbol": SYMBOL,
            "type": "sell",
            "magic": EA_MAGIC,
            "comment": f"HLL_{j}",
            "volume": 0.02,
            "entry_price": round(price - 4, 2),
            "exit_price": round(price + 2, 2),
            "sl": 0.0, "tp": 0.0,
            "profit": profit,
            "change_percent": 0.12,
            "open_timestamp": closed_at - 14_400,
            "close_timestamp": closed_at,
            "close_time": time.strftime("%Y.%m.%d %H:%M", time.gmtime(closed_at)),
        })

    positions = [
        {"ticket": 801_001, "symbol": SYMBOL, "type": "buy", "magic": COPY_MAGIC,
         "comment": "copy_sig101", "volume": 0.04,
         "entry_price": 2_336.20, "current_price": 2_341.85,
         "sl": 2_326.00, "tp": 2_352.00, "profit": 22.60,
         "open_timestamp": now - 5_400},
        {"ticket": 801_002, "symbol": SYMBOL, "type": "buy", "magic": COPY_MAGIC,
         "comment": "copy_sig102", "volume": 0.02,
         "entry_price": 2_339.05, "current_price": 2_341.85,
         "sl": 2_330.00, "tp": 2_355.00, "profit": 5.60,
         "open_timestamp": now - 2_100},
        {"ticket": 801_003, "symbol": SYMBOL, "type": "sell", "magic": EA_MAGIC,
         "comment": "HLL_live", "volume": 0.02,
         "entry_price": 2_344.10, "current_price": 2_341.85,
         "sl": 2_352.00, "tp": 2_330.00, "profit": 4.50,
         "open_timestamp": now - 9_600},
    ]
    sources_map["copy_sig201"] = SOURCES["mid"]
    sources_map["copy_sig202"] = SOURCES["high"]
    sources_map["copy_sig101"] = SOURCES["mid"]
    sources_map["copy_sig102"] = SOURCES["high"]

    (root / "account_info.json").write_text(json.dumps({
        "login": 51234567, "name": "示範帳戶", "server": "Demo-Server",
        "currency": "USD", "balance": 32_850.75, "equity": 32_883.45,
        "margin": 486.20, "free_margin": 32_397.25, "profit": 32.70,
        "leverage": 500, "terminal_connected": True,
        "server_time": time.strftime("%Y.%m.%d %H:%M:%S", time.gmtime(time.time())),
        "timestamp": int(time.time()), "gmt_offset": 0,
    }, ensure_ascii=False), encoding="utf-8")

    (root / "closed_trades.json").write_text(
        json.dumps({"trades": trades}, ensure_ascii=False), encoding="utf-8")
    (root / "positions.json").write_text(
        json.dumps({"positions": positions}, ensure_ascii=False), encoding="utf-8")
    (root / "martingale_state.json").write_text(
        json.dumps({"level": 2}, ensure_ascii=False), encoding="utf-8")
    (root / "signal_sources.json").write_text(
        json.dumps(sources_map, ensure_ascii=False), encoding="utf-8")
    (root / "trade_journal.txt").write_text("".join(journal_lines), encoding="utf-8")


SETTINGS = {
    "mt5_files_dir": "",          # 在 main() 填成臨時目錄
    "interval": "1.0",
    "default_lot_size": "0.01",
    "use_martingale": True,
    "martingale_multiplier": "2.0",
    "martingale_max_level": "5",
    "martingale_lots": "",
    "partial_close_ratios": "0.5,0.3,0.2",
    "cancel_pending_after_seconds": "10800",
    "cancel_if_price_beyond_percent": "0",
    "ea_sources": json.dumps({EA_MAGIC: "趨勢線策略"}, ensure_ascii=False),
    "source_profiles": json.dumps({
        SOURCES["mid"]:  {"mode": "martingale", "lot": "0.01"},
        SOURCES["high"]: {"mode": "flat", "lot": "0.02"},
    }, ensure_ascii=False),
    "auto_start": True,
}



# ---------------------------------------------------------------------------
# 假的 TradeManager。
#
# stats.pending_orders() 有兩條路：trade_manager 有值時走「已追蹤」，
# 沒值時只能從 orders.json 撈出「未追蹤的孤兒單」，畫面上會標成需要重新認領 ——
# 那是異常狀態，不適合當產品截圖。所以這裡用鴨子型別餵一個最小的 manager，
# 讓掛單走正常路徑。欄位名稱照 stats._tracked_pending 讀的來。
# ---------------------------------------------------------------------------
class _Sig:
    def __init__(self, direction, entry, sl, tps):
        self.direction = direction
        self.symbol = SYMBOL
        self.entry_price = entry
        self.stop_loss = sl
        self.take_profit = tps


class _Order:
    def __init__(self, status, sid, ticket, sig, source, age, limit, closest, gap):
        self.status = status
        self.signal_id = sid
        self.ticket = ticket
        self.signal = sig
        self.source_window = source
        self.created_at = time.time() - age
        self.cancel_after_seconds = limit
        self.cancel_if_price_beyond = None
        self.closest_price = closest
        self.closest_gap = gap


class FakeTradeManager:
    """只實作 stats.py 會呼叫到的兩個方法。"""

    CURRENT_PRICE = 2_341.85

    def __init__(self):
        from copy_trader.trade_manager.manager import OrderStatus
        self._orders = [
            _Order(OrderStatus.PENDING, "copy_sig201", 802_001,
                   _Sig("buy", 2_332.50, 2_322.00, [2_348.0, 2_356.0]),
                   SOURCES["mid"], age=1_260, limit=10_800,
                   closest=2_335.10, gap=2.60),
            _Order(OrderStatus.PENDING, "copy_sig202", 802_002,
                   _Sig("sell", 2_352.80, 2_362.00, [2_338.0]),
                   SOURCES["high"], age=420, limit=10_800,
                   closest=2_348.40, gap=4.40),
        ]

    def _get_current_price(self):
        return self.CURRENT_PRICE

    def get_all_orders(self):
        return self._orders


class State:
    """webui.render() 只需要 role / title / auth 三個屬性。"""
    role = "client"
    title = "黃金跟單會員端"
    auth = {"username": "demo"}


def _touch_account(mt5_dir: Path) -> None:
    """把 account_info.json 的時間戳更新成現在，模擬 EA 持續回報。"""
    path = mt5_dir / "account_info.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    now = time.time()
    data["timestamp"] = int(now)
    data["server_time"] = time.strftime("%Y.%m.%d %H:%M:%S", time.gmtime(now))
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def make_handler(mt5_dir: Path, tier: str = "flagship", role: str = "client"):
    from copy_trader.central import webui
    from copy_trader.central.membership import tier_entitlements
    from copy_trader.central.stats import build_stats

    settings = dict(SETTINGS, mt5_files_dir=str(mt5_dir))
    st = State()
    st.role = role
    if role == "central":
        st.title = "黃金訊號中心"
        st.auth = None            # 訊號中心沒有會員登入
    page = webui.render(st)
    tm = FakeTradeManager()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # 不要洗版
            pass

        def _json(self, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/status"):
                self._json({
                    "ok": True, "role": role, "title": State.title,
                    "settings": settings, "hub_configured": True,
                    "status": "運行中", "running": True,
                    "logs": [
                        "[18:42:11] 已連上訊號 Hub",
                        "[18:42:11] MT5 檔案橋接就緒",
                        "[18:44:03] 收到訊號 sig101（中頻訊號）→ 已送出 0.04 手",
                        "[19:07:55] 收到訊號 sig102（高頻訊號）→ 已送出 0.02 手",
                    ],
                    "lan_ip": "192.168.0.24", "cloudflare_url": "",
                    # 訊號中心的面板要顯示監控中的視窗；會員端用不到
                    "capture_windows": list(SOURCES.values()) if role == "central" else [],
                    "uptime_seconds": 27_540,
                    "auth": None if role == "central" else {
                        "logged_in": True, "username": "demo",
                        "tier": tier, "tier_label": tier_entitlements(tier).get("label", tier),
                        "expires_at": time.time() + 86_400 * 128,
                        # 用真的 tier_entitlements()，避免手抄的欄位跟後端走鐘
                        "entitlements": tier_entitlements(tier),
                        "error": "",
                    },
                })
                return
            if self.path.startswith("/api/stats"):
                # EA 平常每幾秒就會覆寫一次這個檔；這裡模擬那個行為，
                # 不然時間戳一過期畫面就變成「MT5 資料未更新」。
                _touch_account(mt5_dir)
                self._json({"ok": True, "stats": build_stats(settings, trade_manager=tm)})
                return
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self._json({"ok": True})

    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8199)
    ap.add_argument("--role", default="client", choices=["client", "central"],
                    help="要預覽哪一端：會員端或訊號中心")
    ap.add_argument("--tier", default="flagship",
                    choices=["trial", "basic", "advanced", "flagship"],
                    help="要模擬的會員等級，用來驗證側欄的鎖定狀態")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="console-shot-"))
    build_fake_mt5_dir(tmp)
    # journal_path() 找不到 DATA_DIR/trade_journal.txt 時會退回 ./trade_journal.txt
    os.chdir(tmp)

    handler = make_handler(tmp, args.tier, args.role)
    print(f"資料目錄：{tmp}")
    print(f"模擬角色：{args.role}　等級：{args.tier}")
    print(f"會員端介面：http://127.0.0.1:{args.port}/")
    HTTPServer(("127.0.0.1", args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
