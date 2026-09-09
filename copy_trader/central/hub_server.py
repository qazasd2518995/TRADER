"""
Central signal hub.

The hub is intentionally small and dependency-free: one always-on signal
computer posts normalized trading signals, and each client agent polls them.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

try:
    from copy_trader.config import DATA_DIR
except Exception:
    DATA_DIR = Path.cwd()

from copy_trader.central import console_page, membership

logger = logging.getLogger(__name__)

# Hub 下發報單的硬上限（秒）。會員端可能「在線上但卡住」——例如 MT5 沒開，
# 撤單一直等不到確認而不推進游標；這時 poll tracker 看他每秒都在輪詢，判斷
# 不出他其實沒在執行。只有訊號自己的年齡擋得住他恢復後補掛一整批過期單。
SIGNAL_MAX_AGE_SECONDS = float(os.environ.get("COPY_TRADER_SIGNAL_MAX_AGE", "600"))


class SignalStore:
    """Append-only JSONL store for hub signals."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._records: List[Dict[str, Any]] = []
        self._event_index: Dict[str, Dict[str, Any]] = {}
        self._latest_seq = 0
        self._load()

    @property
    def latest_seq(self) -> int:
        with self._lock:
            return self._latest_seq

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._records)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    seq = int(record.get("seq") or 0)
                    if seq <= 0:
                        continue
                    self._records.append(record)
                    event_id = str(record.get("event_id") or "")
                    if event_id:
                        self._event_index.setdefault(event_id, record)
                    self._latest_seq = max(self._latest_seq, seq)
        except OSError as e:
            logger.warning("failed to load signal store %s: %s", self.path, e)

    def publish(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        record = dict(payload)
        with self._lock:
            # LINE DB events have a stable identity. A retry after a lost HTTP
            # response must return the original record instead of appending a
            # second order. This is transport idempotency, not heuristic signal
            # deduplication.
            event_id = str(record.get("event_id") or "")
            if event_id and event_id in self._event_index:
                existing = dict(self._event_index[event_id])
                existing["already_published"] = True
                return existing
            self._latest_seq += 1
            record["seq"] = self._latest_seq
            record.setdefault("id", f"sig_{self._latest_seq}_{uuid.uuid4().hex[:8]}")
            record.setdefault("type", "trade_signal")
            record.setdefault("published_at", now)
            self._records.append(record)
            if event_id:
                self._event_index[event_id] = record
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return record

    def list_after(self, after: int, limit: int = 100) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 500))
        after = max(0, int(after or 0))
        with self._lock:
            return [r for r in self._records if int(r.get("seq") or 0) > after][:limit]


class MemberStatusStore:
    """會員端上報的 MT5 帳戶／持倉即時快照，一位會員一筆最新值。

    刻意只放記憶體、不落地：這是每 ~10 秒就被覆寫的即時值，重啟後會員端
    很快又報一次。寫進磁碟只是徒增 IO 與隱私外洩面。「多久沒回報」由前端
    依 reported_at 自己判斷，Hub 不主動刪。
    """

    # 單筆持倉列表最多存這麼多，擋住異常大的 payload 撐爆記憶體。
    MAX_POSITIONS = 60

    def __init__(self):
        self._lock = threading.Lock()
        self._by_user: Dict[str, Dict[str, Any]] = {}

    def update(self, username: str, payload: Dict[str, Any]) -> None:
        if not username:
            return
        positions = payload.get("positions")
        positions = positions[: self.MAX_POSITIONS] if isinstance(positions, list) else []
        record = {
            "username": username,
            "account": payload.get("account") if isinstance(payload.get("account"), dict) else {},
            "positions": positions,
            "positions_count": int(payload.get("positions_count") or len(positions)),
            "orders_count": int(payload.get("orders_count") or 0),
            "device": str(payload.get("device") or ""),
            "mt5_stale": bool(payload.get("mt5_stale")),
            # 掛機端「目前實際套用」的設定。手機頁面拿它跟期望設定比對, 才能
            # 誠實顯示「套用中」還是「已套用」—— 手機控制的是會員家裡那台電腦,
            # 不是雲端服務, 按下去不等於生效。
            "settings_applied": (payload.get("settings_applied")
                                 if isinstance(payload.get("settings_applied"), dict)
                                 else {}),
            # 待成交掛單與績效統計：手機控制台要顯示的。跟持倉一樣有筆數上限，
            # 擋住異常大的 payload 撐爆這台 256MB 的機器。
            "orders": (payload.get("orders") or [])[: self.MAX_POSITIONS]
                      if isinstance(payload.get("orders"), list) else [],
            "stats": payload.get("stats") if isinstance(payload.get("stats"), dict) else {},
            # 各來源目前的馬丁層級（第幾關、連虧幾筆）。手機的策略卡要顯示
            # 「現在押到哪」，只給設定的話會員看不出這一關手數是多少。
            "source_state": ({k: v for k, v in list(payload["source_state"].items())[:20]
                              if isinstance(v, dict)}
                             if isinstance(payload.get("source_state"), dict) else {}),
            # 綁定關係：哪個實例、指向哪個 MT5 資料夾。audit() 靠它找出
            # 「兩個會員端指到同一台 MT5」這種會重複下單的錯配。
            "instance": str(payload.get("instance") or "")[:16],
            "mt5_files_dir": str(payload.get("mt5_files_dir") or "")[:260],
            "reported_at": time.time(),
        }
        with self._lock:
            # MT5 帳號換掉了要留痕跡。換帳號是正常操作，但「換了而沒人發現」
            # 就是訊號被送去非預期帳戶的那條路 —— 至少要看得見。
            previous = self._by_user.get(username) or {}
            was = (previous.get("account") or {}).get("login")
            now = (record.get("account") or {}).get("login")
            if was and now and str(was) != str(now):
                record["mt5_login_changed_from"] = was
                record["mt5_login_changed_at"] = record["reported_at"]
            else:
                # 旗標一旦升起就留著（直到 Hub 重啟），否則下一次上報就沖掉了，
                # 後台永遠看不到那一瞬間。
                for key in ("mt5_login_changed_from", "mt5_login_changed_at"):
                    if key in previous:
                        record[key] = previous[key]
            self._by_user[username] = record

    # ── 綁定體檢 ────────────────────────────────────────────────────────
    # 錯配的代價很直接：兩個會員端指到同一台 MT5 = 同一個帳戶被下兩次單。
    # 這種事用眼睛比對五台機器的設定檔是遲早會漏的，所以由伺服器自己算。
    ISSUE_TEXT = {
        "duplicate_mt5_dir": "與其他會員指向同一個 MT5 資料夾（會重複下單）",
        "duplicate_mt5_login": "與其他會員使用同一個 MT5 帳號（會重複下單）",
        "mt5_login_changed": "MT5 帳號被換過",
        "no_mt5_bridge": "接不上 MT5（路徑錯誤或沒掛 EA）",
        "mt5_not_running": "MT5 沒開著",
        "agent_offline": "掛機端已離線（顯示的是最後一次回報）",
        "never_reported": "掛機端從未回報",
    }

    # 多久沒回報就當成那個掛機端已經走了。正常是每 10 秒一次，兩分鐘是很寬鬆
    # 的門檻（一次網路抖動不會誤判），但又短到「換帳號」這種操作不會卡太久。
    AUDIT_FRESH_SEC = 120.0

    def audit(self, usernames: Optional[List[str]] = None) -> Dict[str, List[str]]:
        """回傳 {會員: [問題代碼]}。沒問題的不會出現在結果裡。

        **重複偵測只看還在回報的紀錄。** 這個 store 不會過期，所以一個會員換
        帳號或停掉掛機端之後，他換之前的最後一筆快照會一直留著。拿它去比對
        就會出現「兩個會員指到同一台 MT5」的假警報 —— 2026-09-09 把
        instance 4 從 ops4 轉給 trial04 時就這樣誤報過。
        早就不在跑的東西不可能正在跟誰重複下單。
        """
        snap = self.snapshot()
        now = time.time()
        fresh = {u: r for u, r in snap.items()
                 if now - float(r.get("reported_at") or 0) <= self.AUDIT_FRESH_SEC}

        by_dir: Dict[str, List[str]] = {}
        by_login: Dict[str, List[str]] = {}
        for user, rec in fresh.items():
            folder = (rec.get("mt5_files_dir") or "").strip().lower()
            if folder:
                by_dir.setdefault(folder, []).append(user)
            login = str((rec.get("account") or {}).get("login") or "")
            if login:
                by_login.setdefault(login, []).append(user)

        out: Dict[str, List[str]] = {}
        for user, rec in snap.items():
            if user not in fresh:
                # 已經離線的：只標離線。再報「接不上 MT5」是誤導 —— 那是
                # 它離線前的狀態，現在根本沒有東西在跑。
                out[user] = ["agent_offline"]
                continue
            issues: List[str] = []
            folder = (rec.get("mt5_files_dir") or "").strip().lower()
            if folder and len(by_dir.get(folder, [])) > 1:
                issues.append("duplicate_mt5_dir")
            login = str((rec.get("account") or {}).get("login") or "")
            if login and len(by_login.get(login, [])) > 1:
                issues.append("duplicate_mt5_login")
            if rec.get("mt5_login_changed_from"):
                issues.append("mt5_login_changed")
            if not login:
                issues.append("no_mt5_bridge")
            elif rec.get("mt5_stale"):
                issues.append("mt5_not_running")
            if issues:
                out[user] = issues
        for user in (usernames or []):
            if user not in snap:
                out.setdefault(user, []).append("never_reported")
        return out

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {user: dict(record) for user, record in self._by_user.items()}


class CentralHeartbeat:
    """訊號端自報的健康狀態，只有一台，所以只存一筆。

    為什麼需要
      訊號端和管理端分開兩台之後，管理端就看不到訊號端的日誌了。沒有心跳的話，
      分機器反而降低可觀測性 —— 那台死了你在另一台完全不會知道，只會覺得
      「今天怎麼都沒訊號」。

    跟 MemberStatusStore 一樣只放記憶體：這是每分鐘覆寫的即時值，Hub 重啟後
    訊號端很快又報一次。「多久沒回報」由前端依 reported_at 自己判斷。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._record: Dict[str, Any] = {}

    def update(self, payload: Dict[str, Any]) -> None:
        record = {
            "device": str(payload.get("device") or ""),
            "status": str(payload.get("status") or ""),
            "started_at": _as_float(payload.get("started_at")),
            "last_publish_at": _as_float(payload.get("last_publish_at")),
            "published_today": int(_as_float(payload.get("published_today")) or 0),
            "line_ok": bool(payload.get("line_ok")),
            "line_detail": str(payload.get("line_detail") or "")[:200],
            "line_cursor": str(payload.get("line_cursor") or "")[:80],
            "ultra_enabled": bool(payload.get("ultra_enabled")),
            "shadow": payload.get("shadow") if isinstance(payload.get("shadow"), dict) else {},
            "version": str(payload.get("version") or "")[:40],
            "reported_at": time.time(),
        }
        with self._lock:
            self._record = record

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._record)


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class MemberPollTracker:
    """記每位會員上次輪詢 /signals 的時間（純記憶體，不落地）。

    用來判斷「這一次輪詢是不是剛上線／剛從中斷恢復」。會員端正常每秒輪詢一次，
    超過 RESUME_GAP 沒來過就代表他當時不在線上；那段期間發布的報單一律不補發
    —— 錯過就錯過。否則會員一開機就會把幾小時前、早該進場甚至早該結束的單
    全部掛出去（實際踩過：一台離線的會員端累積了 9 筆待補）。

    Hub 自己重啟時這份記錄會清空，所有會員的下一次輪詢都會被當成「剛上線」。
    方向是保守的：寧可漏掛，不要補掛。
    """

    # 會員端每秒輪詢，隔這麼久沒來就當作他離線過。
    RESUME_GAP = 30.0

    def __init__(self, resume_gap: float = RESUME_GAP):
        self.resume_gap = float(resume_gap)
        self._lock = threading.Lock()
        self._last: Dict[str, float] = {}

    def touch(self, key: str) -> bool:
        """更新輪詢時間；回傳 True 代表這次是「剛上線」。"""
        if not key:
            return False
        now = time.time()
        with self._lock:
            previous = self._last.get(key)
            self._last[key] = now
        return previous is None or (now - previous) > self.resume_gap


def _line_error_zh(code: int, detail: str) -> str:
    """把 LINE 的 HTTP 錯誤翻成後台看得懂的一句話。

    429 是最容易中的：官方帳號的月額度按**送達人數**扣，不是按訊息則數。
    """
    if code == 429:
        return "LINE 這個月的推播額度已用完（免費方案 200 則，且按群組人數計）"
    if code == 401:
        return "LINE 金鑰失效或已重發，請重新設定 LINE_CHANNEL_ACCESS_TOKEN"
    if code == 403:
        return "這個 LINE 官方帳號沒有推播權限（Messaging API 未啟用或被停權）"
    if code == 400:
        return f"LINE 拒絕了這則訊息（可能群組已解散或 Bot 被踢出）：{detail}"
    return f"LINE 回應 HTTP {code}：{detail}"


class LineNotifyState:
    """LINE 群組通知：登記 Bot 所在群組 + 推播封裝。狀態存磁碟(跨重啟)。

    token / secret 從環境變數讀(fly secret)。沒有 token 就整個停用(push 變
    no-op)，Hub 其他功能完全不受影響 —— 通知是加值旁路，永遠不能拖垮訊號流。
    """

    #: 額度查詢的快取秒數。後台每次重整都打 LINE API 沒必要。
    QUOTA_TTL = 300.0

    def __init__(self, state_path: Path, token: str = "", secret: str = ""):
        self.state_path = Path(state_path)
        self.token = token or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        self.secret = secret or os.environ.get("LINE_CHANNEL_SECRET", "")
        self._lock = threading.Lock()
        self._groups: Dict[str, Dict[str, Any]] = {}
        # push 是背景 thread 裡跑的旁路，失敗只會寫一行 log，而 Hub 的 log 被
        # 每秒好幾次的 /signals 輪詢洗得很快 —— 等於沒人看得到。把最後一次
        # 結果留在記憶體裡，後台才問得出「為什麼沒發通知」。
        self.last_error: str = ""
        self.last_error_at: float = 0.0
        self.last_ok_at: float = 0.0
        self._quota: Dict[str, Any] = {}
        self._quota_at: float = 0.0
        self._load()

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _load(self) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("groups"), dict):
                self._groups = data["groups"]
        except (OSError, json.JSONDecodeError):
            self._groups = {}

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"groups": self._groups}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(self.state_path)
        except OSError as exc:
            logger.warning("save LINE state failed: %s", exc)

    def remember_group(self, group_id: str, name: str = "") -> None:
        if not group_id:
            return
        with self._lock:
            if group_id not in self._groups:
                self._groups[group_id] = {"added_at": time.time(), "name": name}
                self._save()
                logger.info("LINE 群組已登記：%s", group_id)

    def forget_group(self, group_id: str) -> None:
        with self._lock:
            if self._groups.pop(group_id, None) is not None:
                self._save()
                logger.info("LINE 群組已移除（Bot 被踢出）：%s", group_id)

    def target_groups(self) -> List[str]:
        with self._lock:
            return list(self._groups.keys())

    def verify_signature(self, body: bytes, signature: str) -> Optional[bool]:
        """驗 X-Line-Signature。未設 secret 回 None(呼叫端決定是否放行)。"""
        if not self.secret:
            return None
        mac = hmac.new(self.secret.encode("utf-8"), body, hashlib.sha256).digest()
        return hmac.compare_digest(base64.b64encode(mac).decode("utf-8"), signature or "")

    def push_text(self, text: str, to: Optional[str] = None) -> int:
        """推一則純文字。to=None 推給所有已登記群組。回成功數。整段吞例外。"""
        if not self.enabled or not text:
            return 0
        targets = [to] if to else self.target_groups()
        sent = 0
        for group_id in targets:
            if group_id and self._push_one(group_id, text):
                sent += 1
        return sent

    def _push_one(self, group_id: str, text: str) -> bool:
        try:
            body = json.dumps({"to": group_id,
                               "messages": [{"type": "text", "text": text[:4900]}]}).encode("utf-8")
            req = urllib.request.Request(
                "https://api.line.me/v2/bot/message/push", data=body, method="POST",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.token}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            self.last_ok_at = time.time()
            return True
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:                           # noqa: BLE001
                pass
            self._note_error(_line_error_zh(exc.code, detail))
            logger.warning("LINE push 失敗：HTTP %s %s", exc.code, detail)
            return False
        except Exception as exc:                        # noqa: BLE001
            self._note_error(f"連不到 LINE：{exc}")
            logger.warning("LINE push 失敗：%s", exc)
            return False

    def _note_error(self, message: str) -> None:
        self.last_error = message
        self.last_error_at = time.time()
        # 額度用完是最常見的失敗，而剩餘額度就是判斷依據 —— 讓下一次查詢
        # 重新去問，不要拿舊快取騙人。
        self._quota_at = 0.0

    def _api_get(self, path: str) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        try:
            req = urllib.request.Request(
                "https://api.line.me" + path,
                headers={"Authorization": f"Bearer {self.token}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:                        # noqa: BLE001
            logger.warning("LINE API %s 失敗：%s", path, exc)
            return None

    def quota(self, *, force: bool = False) -> Dict[str, Any]:
        """這個月的推播額度。空 dict = 問不到。

        LINE 官方帳號**按送達人數計費**：推一則到 4 個人的群組就扣 4 則。
        免費方案每月 200 則，等於 4 人群組一個月只發得了 50 次訊號。用完之後
        push 一律 429，訊號通知就整個靜靜停掉 —— 所以這個數字要擺在後台。
        """
        now = time.time()
        if not force and self._quota and now - self._quota_at < self.QUOTA_TTL:
            return dict(self._quota)
        limit = self._api_get("/v2/bot/message/quota") or {}
        used = self._api_get("/v2/bot/message/quota/consumption") or {}
        if not limit and not used:
            return dict(self._quota)                    # 問不到就沿用舊的
        out: Dict[str, Any] = {"type": limit.get("type") or ""}
        if limit.get("value") is not None:
            out["limit"] = int(limit["value"])
        if used.get("totalUsage") is not None:
            out["used"] = int(used["totalUsage"])
        if "limit" in out and "used" in out:
            out["remaining"] = max(0, out["limit"] - out["used"])
        # 一則訊號實際扣幾則額度 = 所有登記群組的人數總和。
        cost = 0
        for group_id in self.target_groups():
            info = self._api_get(f"/v2/bot/group/{group_id}/members/count") or {}
            if info.get("count") is not None:
                cost += int(info["count"])
        if cost:
            out["cost_per_signal"] = cost
            if "remaining" in out:
                out["signals_left"] = out["remaining"] // cost
        self._quota, self._quota_at = out, now
        return dict(out)


_TW_TZ = timezone(timedelta(hours=8))


def _fmt_time(raw: Any) -> str:
    """把訊號時間戳格式化成台灣易讀時間(月/日 時:分)。ISO 8601(可能帶時區
    與微秒)會轉成 +08:00 顯示;已是短格式或解析不了就原樣返回，不擋通知。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return text
    parsed = parsed.astimezone(_TW_TZ) if parsed.tzinfo else parsed.replace(tzinfo=_TW_TZ)
    return parsed.strftime("%m/%d %H:%M")


def _fmt_point(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _repair_notice(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    label = {
        "entry_price": "進場",
        "stop_loss": "止損",
        "take_profit": "止盈",
    }.get(str(value.get("field") or ""), "點位")
    original = value.get("original") if isinstance(value.get("original"), list) else []
    corrected = value.get("corrected") if isinstance(value.get("corrected"), list) else []
    if not original or not corrected:
        return ""
    before = "／".join(_fmt_point(item) for item in original)
    after = "／".join(_fmt_point(item) for item in corrected)
    return f"🛠️ 已自動修正{label}：{before} → {after}"


def _rejection_copy(record: Dict[str, Any], signal: Dict[str, Any]) -> tuple[str, str, str]:
    """Return a public label, explanation and next action for a skipped signal."""
    status = str(record.get("parse_status") or "")
    reason = str(record.get("rejection_reason") or "")
    direction = str(signal.get("direction") or "").casefold()

    if status == "rejected_invalid_geometry" or reason == "sl_tp_geometry":
        geometry = {
            "buy": "買單必須符合「止損 < 進場 < 止盈」",
            "sell": "賣單必須符合「止盈 < 進場 < 止損」",
        }.get(direction, "進場、止損與止盈的方向關係必須一致")
        return "點位關係錯誤", geometry, "請等待訊號來源更正後重新發布"
    if status == "rejected_missing_entry" or reason == "entry_not_found":
        return "找不到進場價", "訊息有方向與風控點位，但沒有可辨識的進場價", "請等待訊號來源更正後重新發布"
    if reason == "missing_sl_or_tp":
        return "訊號資料不完整", "缺少可辨識的止損或止盈", "請等待訊號來源補齊後重新發布"
    if status == "manual_review" or reason == "multiple_entries":
        return "出現多個進場點", "同一則訊息有多個進場方向或價位，系統不會猜測", "需要人工確認或等待訊號來源重發"
    if status == "rejected_stale_backlog" or reason == "stale_backlog":
        age = float(record.get("age_seconds") or 0)
        minutes = max(1, round(age / 60)) if age else 0
        detail = f"訊號進入系統時已超過時效{f'（約 {minutes} 分鐘）' if minutes else ''}"
        return "訊號已過期", detail, "這是斷線補讀的舊訊號，系統不會補下歷史單"
    return "格式無法安全判定", "訊號格式不符合目前來源的執行規則", "系統未猜測下單，請人工確認"


def format_signal_notice(record: Dict[str, Any]) -> Optional[str]:
    """把 Hub 訊號 record 轉成給會員看的 LINE 通知文字。None = 不通知。"""
    when = _fmt_time(record.get("message_time"))
    source = str(record.get("source") or "訊號").strip()
    if record.get("type") == "cancel_signal":
        reason = record.get("cancel_reason")
        label = "訊息收回" if reason == "line_unsent" else "引用撤單"
        target = (record.get("target_signals") or [{}])
        sig = target[0] if target and isinstance(target[0], dict) else {}
        entry = sig.get("entry_price")
        head = f"⚠️ 撤單通知{f' · {when}' if when else ''}"
        body = f"{source}｜{label}"
        return head + "\n" + body + (f"\n原掛單進場 {entry}" if entry else "")

    if record.get("type") == "signal_rejected":
        sig = record.get("signal") if isinstance(record.get("signal"), dict) else {}
        label, detail, action = _rejection_copy(record, sig)
        direction = str(sig.get("direction") or "").upper()
        dir_zh = {"BUY": "買進 BUY", "SELL": "賣出 SELL"}.get(direction, direction)
        symbol = str(sig.get("symbol") or "XAUUSD")
        entry = sig.get("entry_price")
        sl = sig.get("stop_loss")
        tps = sig.get("take_profit") or []
        lines = [
            f"⚠️ 訊號未掛單{f' · {when}' if when else ''}",
            f"{source}｜{label}",
        ]
        if entry is not None or sl is not None or tps:
            if dir_zh:
                lines.append(f"{symbol} {dir_zh}")
            tp_str = "／".join(_fmt_point(value) for value in tps) if tps else "—"
            lines.append(
                f"進場 {_fmt_point(entry)}｜止損 {_fmt_point(sl)}｜止盈 {tp_str}"
            )
        else:
            preview = str(record.get("message_preview") or "").strip()
            if preview:
                lines.append(f"訊息摘要：{preview}")
        lines.extend([
            "❌ 系統未發送掛單",
            f"原因：{detail}",
            action,
        ])
        return "\n".join(lines)

    sig = record.get("signal") if isinstance(record.get("signal"), dict) else {}
    direction = str(sig.get("direction") or "").upper()
    dir_zh = {"BUY": "買進 BUY", "SELL": "賣出 SELL"}.get(direction, direction or "—")
    symbol = str(sig.get("symbol") or "XAUUSD")
    entry = sig.get("entry_price")
    if entry is None:
        return None      # 沒有進場價的不是可掛單訊號，不通知
    sl = sig.get("stop_loss")
    tps = sig.get("take_profit") or []
    tp_str = "／".join(_fmt_point(value) for value in tps) if tps else "—"
    lines = [
        f"📌 新訊號{f' · {when}' if when else ''}",
        f"{symbol} {dir_zh}",
        f"進場 {_fmt_point(entry)}｜止損 {_fmt_point(sl)}｜止盈 {tp_str}",
    ]
    repair_line = _repair_notice(sig.get("repair"))
    if repair_line:
        lines.append(repair_line)
    lines.extend([
        "✅ 已發送掛單",
        "※ 訊號來源為第三方，僅供參考，請自負盈虧",
    ])
    return "\n".join(lines)


def _tier_needed(source: str) -> str:
    """要到哪個等級才跟得到這個來源 —— 由低往高找第一個含它的等級。
    哪個等級都沒有（例如還在建置的低頻）就標最高等級，跟電腦版一致。"""
    for tier in membership.TIER_ORDER:
        if source in (membership.TIERS.get(tier) or {}).get("sources", ()):
            return tier
    return membership.TIER_ORDER[-1]


class HubRequestHandler(BaseHTTPRequestHandler):
    server_version = "CopyTraderHub/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    @property
    def store(self) -> SignalStore:
        return self.server.store  # type: ignore[attr-defined]

    @property
    def token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    @property
    def members(self) -> Optional["membership.MemberStore"]:
        return getattr(self.server, "members", None)

    @property
    def member_status(self) -> Optional["MemberStatusStore"]:
        return getattr(self.server, "member_status", None)

    @property
    def heartbeat(self) -> Optional["CentralHeartbeat"]:
        return getattr(self.server, "heartbeat", None)

    @property
    def line(self) -> Optional["LineNotifyState"]:
        return getattr(self.server, "line", None)

    @property
    def poll_tracker(self) -> Optional["MemberPollTracker"]:
        return getattr(self.server, "poll_tracker", None)

    @property
    def exness(self):
        return getattr(self.server, "exness", None)

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Hub-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _presented_tokens(self) -> set:
        """呼叫端可能把 token 放在 query / X-Hub-Token / Bearer 三個地方。"""
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        auth = self.headers.get("Authorization", "")
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        return {
            (qs.get("token") or [""])[0],
            self.headers.get("X-Hub-Token", ""),
            bearer,
        } - {""}

    def _authorized(self) -> bool:
        """管理權限 — 中央機發布訊號、後台管理會員都走這把。

        這就是原本的共用 token (COPY_TRADER_HUB_TOKEN)。會員改用帳密之後,
        這把應該只留給訊號中心自己, 並且輪替一次。
        """
        if not self.token:
            return True
        return self.token in self._presented_tokens()

    def _current_member(self, *, consume: bool = False,
                        scope: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """把 session token 換成會員；不是會員就回 None。

        每次都重新查 —— 等級/期限/停權在後台一改, 下一次輪詢就生效。

        consume=True 只在 /signals 輪詢時傳(會員端只在跟單時才輪詢 /signals),
        用量制會員會依此扣掉開盤時的跟單時間。其他呼叫一律不扣。

        scope 指定這個端點只接受哪一種連線。**/signals 一定要傳 SCOPE_AGENT** ——
        控制台(手機)拿得到訊號的話, 帳號分享就有利可圖, 付費閘門等於破了。
        不指定則兩種都收(例如 /auth/me)。
        """
        store = self.members
        if store is None:
            return None
        for tok in self._presented_tokens():
            if tok == self.token:
                continue        # 那是管理 token, 不是會員 session
            member, _err = store.resolve_session(tok, consume=consume)
            if member is None:
                continue
            if scope is not None and member.get("session_scope") != scope:
                continue
            return member
        return None

    def _console_view(self, member: Dict[str, Any]) -> Dict[str, Any]:
        """手機頁面要的一整包：期望設定、掛機端實際套用的、以及它還活著嗎。

        三樣缺一不可。只給期望設定的話，會員按了暫停就以為安全了 —— 但他控制的
        是自己家裡那台電腦，電腦關機時按什麼都不會發生。所以一定要把
        「掛機端多久前回報過」跟「它目前實際是什麼設定」一起送出去，
        介面才有辦法誠實顯示「套用中」還是「已套用」。
        """
        store = self.members
        username = str(member.get("username") or "")
        meta = store.settings_meta(username) if store is not None else {
            "settings": {}, "updated_at": None, "updated_by": ""}

        status_store = self.member_status
        snap = (status_store.snapshot().get(username) or {}) if status_store else {}
        reported_at = snap.get("reported_at")

        return {
            "ok": True,
            "username": username,
            "tier": member.get("tier"),
            "tier_label": member.get("tier_label"),
            "entitlements": member.get("entitlements") or {},
            "desired": meta["settings"],
            "desired_updated_at": meta["updated_at"],
            "desired_updated_by": meta["updated_by"],
            "applied": snap.get("settings_applied") or {},
            # 掛機端的存活跡象。前端據此顯示「電腦離線，改的東西還沒生效」。
            "agent_online": bool(member.get("online")),
            "agent_reported_at": reported_at,
            "agent_device": member.get("session_device") or snap.get("device") or "",
            "mt5_stale": bool(snap.get("mt5_stale")) if snap else None,
            "account": snap.get("account") or {},
            "positions": snap.get("positions") or [],
            "orders": snap.get("orders") or [],
            "positions_count": snap.get("positions_count"),
            "orders_count": snap.get("orders_count"),
            "stats": snap.get("stats") or {},
            "source_state": snap.get("source_state") or {},
            "usage": member.get("usage"),
            "expires_at": member.get("expires_at"),
            "status": member.get("status"),
            # 全部來源都送，沒授權的在手機上顯示成鎖住而不是整個消失 ——
            # 會員看得到自己「還沒買到什麼」，比憑空少一張卡片清楚。
            # 順序跟電腦版一樣：低頻、中頻、高頻、超高頻。
            #
            # **手機上只准出現 label。** name 是 LINE 聊天室的名字，會員端
            # 拿它當設定的 key、Hub 拿它過濾訊號，但它不該被看到 —— 對外
            # 一律叫交易頻率，不出現提供者的群組名或暱稱。
            "all_sources": [
                {"name": name, "label": label, "need": _tier_needed(name)}
                for name, label in (
                    (membership.LOW_FREQ, "低頻交易"),
                    (membership.MID_FREQ, "中頻交易"),
                    (membership.HIGH_FREQ, "高頻交易"),
                    (membership.ULTRA_HIGH_FREQ, "超高頻交易"),
                )
            ],
            "tier_labels": {k: membership.TIERS[k]["label"] for k in membership.TIER_ORDER},
            "min_password_length": membership.MIN_PASSWORD_LENGTH,
        }

    def _member_auth_error(self) -> str:
        """會員 token 解不開時的原因，用來給前端顯示人看得懂的訊息。"""
        store = self.members
        if store is None:
            return "membership_unavailable"
        worst = "session_invalid"
        for tok in self._presented_tokens():
            if tok == self.token:
                continue
            member, err = store.resolve_session(tok)
            if err in ("expired", "suspended"):
                return err      # 這兩個要明確告訴會員, 否則他不知道要續費
            if member is not None and member.get("session_scope") == membership.SCOPE_CONSOLE:
                # token 本身是好的, 只是用錯地方 —— 拿控制台的連線去要訊號。
                # 講清楚, 免得有人對著「session_invalid」找一整天登入問題。
                return "console_session_not_allowed"
            worst = err or worst
        return worst

    def _read_body(self) -> Optional[Dict[str, Any]]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "invalid_json"})
            return None
        if not isinstance(data, dict):
            self._send_json(400, {"ok": False, "error": "body_must_be_object"})
            return None
        return data

    def do_OPTIONS(self) -> None:
        self._send_json(200, {"ok": True})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, {
                "ok": True,
                "latest_seq": self.store.latest_seq,
                "count": self.store.count,
                "auth_required": bool(self.token),
                # 會員端用這個判斷 Hub 是否已經支援帳密登入 —— 舊版 Hub 沒有
                # 這個欄位, 會員端就退回共用 token 模式, 不會整個連不上。
                "membership": self.members is not None,
            })
            return

        if parsed.path == "/":
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            self._send_html(200, self._dashboard_html())
            return

        if parsed.path == "/signals":
            qs = parse_qs(parsed.query)
            after = int((qs.get("after") or ["0"])[0] or 0)
            limit = int((qs.get("limit") or ["100"])[0] or 100)

            # 管理 token = 完整存取 (訊號中心自己、以及還沒轉成帳號的舊會員端)
            if self._authorized():
                records = self.store.list_after(after=after, limit=limit)
                self._send_json(200, {
                    "ok": True,
                    "latest_seq": self.store.latest_seq,
                    "cursor": max(
                        [int(record.get("seq") or 0) for record in records],
                        default=after,
                    ),
                    "signals": records,
                })
                return

            # 會員 session = 只拿得到自己等級授權的來源。
            #
            # 這裡是整套收費機制唯一真正的閘門: 沒買的來源, 資料根本不會離開
            # 伺服器。會員端怎麼改都拿不到。
            # consume=True: 會員端只在「正在跟單」時才輪詢 /signals, 所以這一次
            # 呼叫本身就代表跟單中。用量制會員在這裡依開盤與否扣使用額度。
            #
            # scope=agent: 只有會員的電腦拿得到訊號。手機那條控制台連線在這裡
            # 一律被擋 —— 否則只要用手機登入就能把訊號抄走, 付費閘門形同虛設。
            member = self._current_member(
                consume=True, scope=membership.SCOPE_AGENT)
            if member is None:
                self._send_json(401, {"ok": False, "error": self._member_auth_error()})
                return
            allowed = member["entitlements"]["sources"]
            records = self.store.list_after(after=after, limit=limit)
            visible = membership.filter_signals_for(records, allowed)

            # 會員不在線上時發布的報單，上線後一律不補掛 —— 錯過就錯過。
            # 撤單是例外：他離線前掛的單可能還在 MT5，期間發的撤單要補上去把
            # 風險收掉，方向和「不補掛」一致。
            tracker = self.poll_tracker
            resumed = tracker.touch(str(member.get("username") or "")) if tracker else False
            skipped_stale = 0
            if visible:
                now_ts = time.time()
                kept: List[Dict[str, Any]] = []
                for record in visible:
                    # 撤單一律放行：他離線前掛的單可能還在 MT5，這筆撤單是去
                    # 收風險的，方向和「不補掛」一致。
                    if record.get("type") == "cancel_signal":
                        kept.append(record)
                        continue
                    if resumed:
                        continue        # 不在線上時發布的報單：錯過就錯過
                    published = 0.0
                    try:
                        published = float(record.get("published_at") or 0)
                    except (TypeError, ValueError):
                        published = 0.0
                    if published and now_ts - published > SIGNAL_MAX_AGE_SECONDS:
                        continue        # 太舊：可能早就進場甚至結束了
                    kept.append(record)
                skipped_stale = len(visible) - len(kept)
                if skipped_stale:
                    logger.info("會員 %s：略過 %d 筆不該補掛的報單（resumed=%s）",
                                member.get("username"), skipped_stale, resumed)
                visible = kept

            self._send_json(200, {
                "ok": True,
                "resumed": resumed,
                "skipped_stale": skipped_stale,
                # latest_seq 要回「這一批的原始上界」而不是過濾後的最大 seq,
                # 否則會員端的游標會卡在最後一筆有權限的訊號, 之後每輪都重掃
                # 同一段區間。過濾掉的 seq 對這個會員來說就是不存在。
                "latest_seq": self.store.latest_seq,
                "cursor": max([int(r.get("seq") or 0) for r in records], default=after),
                "signals": visible,
                "filtered": len(records) - len(visible),
                # 用量制會員: 剩餘使用額度 + 目前是否開盤(給會員端顯示倒數/暫停)。
                "usage": member.get("usage"),
            })
            return

        if parsed.path == "/auth/me":
            member = self._current_member()
            if member is None:
                self._send_json(401, {"ok": False, "error": self._member_auth_error()})
                return
            self._send_json(200, {"ok": True, "member": member})
            return

        if parsed.path in ("/console", "/console/"):
            # 這一頁本身不需要驗證 —— 它就是登入畫面。真正的門在
            # /console/settings，那裡才要 console session。
            self._send_html(200, console_page.render())
            return

        if parsed.path == "/console/settings":
            member = self._current_member(scope=membership.SCOPE_CONSOLE)
            if member is None:
                self._send_json(401, {"ok": False, "error": self._member_auth_error()})
                return
            self._send_json(200, self._console_view(member))
            return

        if parsed.path.startswith("/admin/"):
            self._handle_admin_get(parsed)
            return

        self._send_json(404, {"ok": False, "error": "not_found"})

    # ── 會員後台 (管理 token) ───────────────────────────────────────────
    def _handle_admin_get(self, parsed) -> None:
        if not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        if parsed.path == "/admin/exness/clients":
            client = self.exness
            if client is None:
                self._send_json(200, {"ok": True, "enabled": False,
                                      "accounts": {}, "error": "not_configured"})
                return
            qs = parse_qs(parsed.query)
            force = bool((qs.get("force") or [""])[0])
            summary = client.summary_by_account(
                date_from=(qs.get("from") or [""])[0],
                date_to=(qs.get("to") or [""])[0],
                force=force,
            )
            # 客戶總覽：不依賴「會員 ↔ client_account」對應，註冊了還沒交易的
            # 客戶也看得到，那才是需要跟進的名單。
            overview = client.clients_overview(force=force)
            self._send_json(200, {"ok": True, **summary,
                                  "clients": overview.get("clients") or [],
                                  "totals": overview.get("totals") or {}})
            return

        if parsed.path == "/admin/central/status":
            beat = self.heartbeat
            record = beat.snapshot() if beat is not None else {}
            # 沒回報過就是空的 —— 讓前端自己說「從未回報」，Hub 不編一個假的
            # 「正常」出來。
            self._send_json(200, {"ok": True, "central": record,
                                  "now": time.time()})
            return

        if parsed.path == "/admin/line/status":
            line = self.line
            payload: Dict[str, Any] = {"ok": True,
                                       "enabled": bool(line and line.enabled),
                                       "has_secret": bool(line and line.secret),
                                       "groups": line.target_groups() if line else []}
            if line is not None and line.enabled:
                force = (parse_qs(parsed.query).get("refresh") or ["0"])[0] == "1"
                payload["quota"] = line.quota(force=force)
                payload["last_error"] = line.last_error
                payload["last_error_at"] = line.last_error_at
                payload["last_ok_at"] = line.last_ok_at
            self._send_json(200, payload)
            return

        store = self.members
        if store is None:
            self._send_json(503, {"ok": False, "error": "membership_unavailable"})
            return

        if parsed.path == "/admin/tiers":
            self._send_json(200, {"ok": True, "tiers": membership.tier_catalog()})
            return
        if parsed.path == "/admin/members":
            self._send_json(200, {"ok": True, "members": store.list_members()})
            return
        if parsed.path == "/admin/logins":
            qs = parse_qs(parsed.query)
            limit = int((qs.get("limit") or ["100"])[0] or 100)
            self._send_json(200, {"ok": True, "logins": store.recent_logins(limit)})
            return
        if parsed.path == "/admin/members/status":
            status_store = self.member_status
            statuses = status_store.snapshot() if status_store is not None else {}
            # 綁定體檢一起回，後台不必自己比對五台機器的設定 —— 眼睛比對
            # 遲早會漏，而漏掉的下場是同一個 MT5 帳戶被下兩次單。
            issues: Dict[str, Any] = {}
            if status_store is not None:
                store = self.members
                known = [m["username"] for m in store.list_members()] if store else None
                issues = status_store.audit(known)
            self._send_json(200, {"ok": True, "statuses": statuses,
                                  "issues": issues,
                                  "issue_text": MemberStatusStore.ISSUE_TEXT})
            return
        self._send_json(404, {"ok": False, "error": "not_found"})

    def _client_ip(self) -> str:
        # Fly.io 在前面擋一層代理, 真實來源在 Fly-Client-IP / X-Forwarded-For
        return (self.headers.get("Fly-Client-IP")
                or (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
                or self.address_string())

    def _handle_auth_post(self, parsed) -> None:
        store = self.members
        if store is None:
            self._send_json(503, {"ok": False, "error": "membership_unavailable"})
            return
        data = self._read_body()
        if data is None:
            return

        if parsed.path == "/auth/logout":
            for tok in self._presented_tokens():
                store.logout(tok)
            self._send_json(200, {"ok": True})
            return

        if parsed.path == "/auth/change-password":
            old = str(data.get("old_password") or "")
            new = str(data.get("new_password") or "")
            for tok in self._presented_tokens():
                if tok == self.token:
                    continue        # 管理 token 沒有「自己的密碼」可改
                ok, err = store.change_password(tok, old, new)
                if ok:
                    self._send_json(200, {"ok": True})
                    return
                # 401 給憑證問題 (舊密碼錯 / session 失效), 400 給輸入問題
                status = 400 if err in ("too_short", "same_as_old") else 401
                self._send_json(status, {"ok": False, "error": err})
                return
            self._send_json(401, {"ok": False, "error": "no_token"})
            return

        # scope: agent = 會員的電腦(跟單), console = 手機／瀏覽器(只看與設定)。
        # 舊版會員端不會帶這個欄位, normalize 之後就是 agent, 行為完全照舊 ——
        # 這點是硬要求, 已經發出去的會員端不能因為 Hub 升級就登不進來。
        member, err = store.login(
            str(data.get("username") or ""),
            str(data.get("password") or ""),
            device=str(data.get("device") or ""),
            ip=self._client_ip(),
            scope=membership.normalize_scope(data.get("scope")),
        )
        if member is None:
            # 401 給憑證問題, 403 給「帳號沒問題但目前不能用」——
            # 會員端要據此顯示不同訊息 (改密碼 vs 找管理員續費)
            status = 401 if err == "bad_credentials" else 403
            self._send_json(status, {"ok": False, "error": err})
            return
        self._send_json(200, {"ok": True, "member": member})

    def _handle_admin_post(self, parsed) -> None:
        if not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        if parsed.path == "/admin/line/test":
            data = self._read_body()
            if data is None:
                return
            line = self.line
            if line is None or not line.enabled:
                self._send_json(400, {"ok": False, "error": "line_disabled"})
                return
            if not line.target_groups():
                self._send_json(400, {"ok": False, "error": "no_group_registered"})
                return
            text = str(data.get("text") or "🔔 測試：黃金跟單通知已連線")
            sent = line.push_text(text)
            self._send_json(200, {"ok": True, "sent": sent,
                                  "groups": len(line.target_groups())})
            return
        store = self.members
        if store is None:
            self._send_json(503, {"ok": False, "error": "membership_unavailable"})
            return
        data = self._read_body()
        if data is None:
            return
        user = str(data.get("username") or "")

        try:
            if parsed.path == "/admin/members":
                out = store.create_member(
                    user,
                    str(data.get("tier") or "trial"),
                    password=str(data.get("password") or ""),
                    expires_at=data.get("expires_at"),
                    days=data.get("days"),
                    note=str(data.get("note") or ""),
                )
            elif parsed.path == "/admin/members/update":
                fields = {k: data[k] for k in ("tier", "expires_at", "status", "note")
                          if k in data}
                out = store.update_member(user, **fields)
            elif parsed.path == "/admin/members/extend":
                out = store.extend(user, int(data.get("days") or 30))
            elif parsed.path == "/admin/members/renew":
                out = store.renew(user, int(data.get("days") or 7))
            elif parsed.path == "/admin/members/reset-password":
                out = store.reset_password(user, str(data.get("password") or ""))
            elif parsed.path == "/admin/members/kick":
                out = {"kicked": store.kick(user)}
            elif parsed.path == "/admin/members/delete":
                out = {"deleted": store.delete_member(user)}
            else:
                self._send_json(404, {"ok": False, "error": "not_found"})
                return
        except ValueError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
            return
        except Exception as e:                      # noqa: BLE001
            logger.exception("admin op failed: %s", e)
            self._send_json(500, {"ok": False, "error": str(e)})
            return

        self._send_json(200, {"ok": True, "result": out})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/line/webhook":
            # LINE 平台的 webhook（公開，不需管理 token）。主要用途：Bot 被加進
            # 群組時自動登記 group id，之後廣播訊號就推得到。必須回 200。
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            state = self.line
            if state is not None and state.secret:
                if state.verify_signature(raw, self.headers.get("X-Line-Signature", "")) is False:
                    self._send_json(401, {"ok": False, "error": "bad_signature"})
                    return
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                payload = {}
            if state is not None:
                for event in payload.get("events", []):
                    if not isinstance(event, dict):
                        continue
                    gid = (event.get("source") or {}).get("groupId")
                    if not gid:
                        continue
                    if event.get("type") == "leave":
                        state.forget_group(gid)
                    else:
                        state.remember_group(gid)
            self._send_json(200, {"ok": True})
            return

        if parsed.path in ("/auth/login", "/auth/logout", "/auth/change-password"):
            self._handle_auth_post(parsed)
            return

        if parsed.path == "/console/settings":
            store = self.members
            if store is None:
                self._send_json(503, {"ok": False, "error": "membership_unavailable"})
                return
            # 先讀 body 再驗證，否則被拒時 body 沒消化，用戶端會收到
            # connection abort 而不是乾淨的 401（會員上報那條踩過同樣的坑）。
            data = self._read_body()
            if data is None:
                return
            member = self._current_member(scope=membership.SCOPE_CONSOLE)
            if member is None:
                self._send_json(401, {"ok": False, "error": self._member_auth_error()})
                return
            username = str(member.get("username") or "")
            # 只吃 settings 這個 key，不吃整包 body —— 免得哪天 body 多了別的
            # 欄位就被當成設定寫進去。
            patch = data.get("settings")
            _merged, rejected = store.update_settings(
                username, patch if isinstance(patch, dict) else {}, source="console")
            # 一律回完整檢視：手數被等級夾住時，前端要立刻顯示真正生效的值，
            # 不能讓畫面停在使用者輸入的那個數字。
            view = self._console_view(member)
            view["rejected"] = rejected
            self._send_json(200, view)
            return
        if parsed.path.startswith("/admin/"):
            self._handle_admin_post(parsed)
            return

        if parsed.path == "/report/central":
            # 訊號端自報健康狀態。用管理 token 認身分 —— 只有訊號端持有它，
            # 會員的 session token 不能冒充。
            beat = self.heartbeat
            # 先把 body 讀掉再驗證，否則被拒時 body 沒消化，Windows 端會收到
            # connection abort 而不是乾淨的 401（會員上報那條踩過同樣的坑）。
            data = self._read_body()
            if data is None:
                return
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            if beat is None:
                self._send_json(503, {"ok": False, "error": "heartbeat_unavailable"})
                return
            beat.update(data)
            self._send_json(200, {"ok": True})
            return

        if parsed.path == "/report/status":
            # 會員端自報 MT5 帳戶／持倉。用會員自己的 session token 認身分,
            # 不是管理 token —— 只能報自己那份, 報不到別人的。
            status_store = self.member_status
            if status_store is None:
                self._send_json(503, {"ok": False, "error": "status_unavailable"})
                return
            # 先把 request body 讀掉再驗證: 否則被拒(401)時 body 沒消化,
            # Windows 的 client 會收到 connection abort 而不是乾淨的 401。
            data = self._read_body()
            if data is None:
                return
            # 只有跟單連線能回報 —— 這份快照是「我的 MT5 現在長這樣」,
            # 只有真的接著 MT5 的那台電腦講得出來。控制台是讀這份資料的人,
            # 不是寫的人, 讓它能寫等於允許偽造自己的持倉。
            member = self._current_member(scope=membership.SCOPE_AGENT)
            if member is None:
                self._send_json(401, {"ok": False, "error": self._member_auth_error()})
                return
            username = str(member.get("username") or "")
            status_store.update(username, data)

            # 掛機端主動把本機的變更推上來(會員在電腦上自己按了開始/停止)。
            # 沒有這條的話, 期望設定會一直是舊值, 下一輪又把他按的東西改回去。
            store = self.members
            push = data.get("settings_push")
            if store is not None and isinstance(push, dict) and push:
                store.update_settings(username, push, source="agent")

            # 回應夾帶期望設定 —— 這就是往下的通道。掛機端每 10 秒上報一次,
            # 順手就把「我應該變成什麼樣子」帶回去, 不必另外開一個輪詢端點。
            settings = store.get_settings(username) if store is not None else {}
            self._send_json(200, {"ok": True, "settings": settings})
            return

        if parsed.path != "/signals":
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
        # 發布訊號只有中央機做, 一律要管理 token。會員 session 不得發布。
        if not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return

        data = self._read_body()
        if data is None:
            return

        items = data.get("signals")
        if items is None:
            items = [data]
        if not isinstance(items, list):
            self._send_json(400, {"ok": False, "error": "signals_must_be_list"})
            return

        published = []
        for item in items:
            if not isinstance(item, dict):
                continue
            published.append(self.store.publish(item))

        # LINE 廣播（旁路）：丟背景 thread，push 失敗或慢都不能影響訊號發布回應。
        # already_published 的(retry 重送)不重推，避免同一訊號通知兩次。
        line = self.line
        if line is not None and line.enabled:
            for record in published:
                if record.get("already_published"):
                    continue
                try:
                    text = format_signal_notice(record)
                except Exception:                       # noqa: BLE001
                    text = None
                if text:
                    threading.Thread(target=self._push_line, args=(line, text),
                                     daemon=True).start()

        self._send_json(200, {
            "ok": True,
            "published": published,
            "latest_seq": self.store.latest_seq,
        })

    @staticmethod
    def _push_line(line: "LineNotifyState", text: str) -> None:
        """背景推播。一個群組都沒推成功就留下原因 —— 這裡不 raise，
        通知永遠不能影響訊號發布。"""
        try:
            sent = line.push_text(text)
        except Exception as exc:                        # noqa: BLE001
            logger.warning("LINE 通知失敗：%s", exc)
            return
        if sent == 0 and line.target_groups() and not line.last_error:
            line._note_error("推播沒有送達任何群組")   # noqa: SLF001

    def _dashboard_html(self) -> str:
        return """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Copy Trader Signal Hub</title>
  <style>
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f7f8; color: #17202a; }
    header { padding: 18px 24px; background: #ffffff; border-bottom: 1px solid #dfe3e6; display: flex; justify-content: space-between; align-items: center; gap: 16px; }
    h1 { margin: 0; font-size: 20px; font-weight: 650; }
    main { max-width: 1080px; margin: 0 auto; padding: 20px; }
    table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #dfe3e6; }
    th, td { padding: 10px 12px; border-bottom: 1px solid #edf0f2; text-align: left; vertical-align: top; font-size: 14px; }
    th { background: #fafbfc; color: #52616f; font-weight: 600; }
    .pill { display: inline-block; padding: 2px 7px; border-radius: 999px; background: #eef4ff; color: #1450a3; font-size: 12px; }
    .muted { color: #6b7785; }
  </style>
</head>
<body>
  <header>
    <h1>Copy Trader Signal Hub</h1>
    <span id="status" class="muted">loading</span>
  </header>
  <main>
    <table>
      <thead><tr><th>Seq</th><th>來源</th><th>事件</th><th>方向</th><th>Entry</th><th>SL</th><th>TP</th><th>訊息／偵測時間</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </main>
  <script>
    const params = new URLSearchParams(location.search);
    const token = params.get("token") || "";
    let after = 0;
    const rows = document.getElementById("rows");
    const status = document.getElementById("status");
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    async function poll() {
      const url = `/signals?after=${after}&limit=100${token ? `&token=${encodeURIComponent(token)}` : ""}`;
      const res = await fetch(url);
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "request_failed");
      status.textContent = `latest seq ${data.latest_seq}`;
      for (const item of data.signals || []) {
        after = Math.max(after, Number(item.seq || 0));
        const cancelled = item.type === "cancel_signal";
        const rejected = item.type === "signal_rejected";
        const sig = cancelled ? ((item.target_signals || [])[0] || {}) : (item.signal || {});
        const eventLabel = cancelled
          ? (item.cancel_reason === "line_unsent" ? "訊息收回" : "引用撤單")
          : (rejected ? "未掛單" : "新報單");
        const eventTime = item.recall_detected_at
          || (item.published_at ? new Date(item.published_at * 1000).toLocaleString() : "");
        const timeLabel = item.message_time
          ? `${item.message_time}${cancelled && eventTime ? ` / ${eventTime}` : ""}`
          : eventTime;
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${esc(item.seq)}</td><td>${esc(item.source)}</td><td><span class="pill">${esc(eventLabel)}</span></td><td>${esc(sig.direction || "—")}</td><td>${esc(sig.entry_price ?? "—")}</td><td>${esc(sig.stop_loss ?? "—")}</td><td>${esc((sig.take_profit || []).join(", ") || "—")}</td><td class="muted">${esc(timeLabel)}</td>`;
        rows.prepend(tr);
      }
    }
    poll().catch(err => status.textContent = err.message);
    setInterval(() => poll().catch(err => status.textContent = err.message), 2000);
  </script>
</body>
</html>"""


class HubHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple, handler_class: type, store: SignalStore,
                 token: str, members: Optional["membership.MemberStore"] = None,
                 member_status: Optional["MemberStatusStore"] = None,
                 line: Optional["LineNotifyState"] = None,
                 poll_tracker: Optional["MemberPollTracker"] = None,
                 exness: Optional[Any] = None,
                 heartbeat: Optional["CentralHeartbeat"] = None):
        super().__init__(server_address, handler_class)
        self.store = store
        self.token = token
        self.members = members
        self.member_status = member_status
        self.line = line
        self.poll_tracker = poll_tracker
        self.exness = exness
        self.heartbeat = heartbeat


def run_server(host: str, port: int, store_path: Path, token: str = "",
               members_path: Optional[Path] = None) -> None:
    store = SignalStore(store_path)
    member_status = MemberStatusStore()
    poll_tracker = MemberPollTracker()
    from copy_trader.central.exness_partner import ExnessPartnerClient
    exness = ExnessPartnerClient()
    logger.info("Exness Partnership API：%s",
                "已設定" if exness.enabled else "未設定(未填 EXNESS_PARTNER_LOGIN/PASSWORD)")
    heartbeat = CentralHeartbeat()
    line = LineNotifyState(store_path.parent / "line_notify_state.json")
    if line.enabled:
        logger.info("LINE 通知已啟用（已登記 %d 個群組）", len(line.target_groups()))
    else:
        logger.info("LINE 通知未啟用（未設 LINE_CHANNEL_ACCESS_TOKEN）")

    # 會員資料庫壞掉不該讓整個 Hub 起不來 —— 訊號流是核心, 會員系統是加值。
    # 起不來就退回「只認管理 token」的舊行為, 並把錯誤大聲印出來。
    members: Optional[membership.MemberStore] = None
    if members_path is not None:
        try:
            members = membership.MemberStore(str(members_path))
            logger.info("membership store ready: %s (%d 位會員)",
                        members_path, len(members.list_members()))
        except Exception as e:                       # noqa: BLE001
            logger.error("membership store FAILED to open (%s): %s — "
                         "會員登入將不可用, Hub 僅接受管理 token", members_path, e)

    httpd = HubHTTPServer((host, port), HubRequestHandler, store, token, members,
                          member_status, line, poll_tracker, exness, heartbeat)
    logger.info("signal hub listening on http://%s:%s (store=%s)", host, port, store_path)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("signal hub stopped")
    finally:
        httpd.server_close()
        if members is not None:
            members.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the central copy-trader signal hub.")
    parser.add_argument("--host", default=os.environ.get("COPY_TRADER_HUB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("COPY_TRADER_HUB_PORT", "8765")))
    parser.add_argument("--store", default=os.environ.get("COPY_TRADER_HUB_STORE", str(DATA_DIR / "central_hub_signals.jsonl")))
    parser.add_argument("--token", default=os.environ.get("COPY_TRADER_HUB_TOKEN", ""))
    parser.add_argument("--members", default=os.environ.get("COPY_TRADER_HUB_MEMBERS", ""),
                        help="會員資料庫路徑；留空則放在訊號檔旁邊的 members.db")
    parser.add_argument("--log-level", default=os.environ.get("COPY_TRADER_LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    store_path = Path(args.store)
    # 預設跟訊號檔同一顆持久化磁碟, 不必另外設環境變數就能跨重啟保存
    members_path = Path(args.members) if args.members else store_path.parent / "members.db"
    run_server(args.host, args.port, store_path, args.token, members_path)


if __name__ == "__main__":
    main()
