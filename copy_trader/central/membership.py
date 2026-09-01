"""會員系統 — 帳號、等級、期限、單一裝置 session。

只用標準庫。這個模組會被打進 Hub 的 Docker 映像檔，而那個映像檔刻意
只複製 copy_trader/central 且不裝任何第三方套件（見 Dockerfile），所以
這裡不能出現 bcrypt / passlib / sqlalchemy 之類的東西。

資料落在 Hub 的持久化磁碟 (/data/members.db)，跟訊號紀錄同一顆 volume。

權限的執行點分兩層，要清楚知道差別：

  * allowed_sources — **伺服器端**強制。Hub 在 /signals 就把該會員無權
    存取的來源濾掉，資料根本不會離開伺服器。這是真正的收費閘門。

  * max_lot / martingale / dynamic_lot / partial_close / breakeven / schedule —
    **用戶端**自律。Hub 只在登入時把
    這些額度告訴會員端，由會員端自己套用。會員如果反編譯改掉，是擋不住的。
    之所以能接受：這些只影響他自己的帳戶風險，不影響我們的訊號值錢與否。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 等級定義 ────────────────────────────────────────────────────────────────
# 刻意寫在程式碼裡而不是資料庫: 改等級內容是「產品決策」, 應該走版控與部署,
# 而不是在後台被誤點一下就整批會員權限跑掉。改完重新部署 Hub 即可生效。
#
# sources 用的是訊號來源的「顯示名稱」, 必須跟中央機發布時的 source 欄位一致。
HIGH_FREQ = "焦點利潤(yuyu)"      # 對外稱「高頻交易」
MID_FREQ = "黃金報單🈲言群"        # 對外稱「中頻交易」
ULTRA_HIGH_FREQ = "超高頻交易"     # 市場資料模型；不是 LINE 聊天室
# 低頻交易: 訊號源還沒接上, 但等級表與會員端面板已經把它列為旗艦版的權益,
# 所以這裡先把名字定下來 —— 會員端會顯示這一列(旗艦版亮、其餘鎖住),
# 收到第一筆訊號之前它就是一個「已授權但還沒有訊號」的來源, 不會下單。
LOW_FREQ = "低頻交易"

TIERS: Dict[str, Dict[str, Any]] = {
    "trial": {
        "label": "體驗版",
        "sources": [MID_FREQ],
        "max_lot": 0.01,
        "martingale": False,
        "dynamic_lot": False,
        "partial_close": False,
        "breakeven": False,
        "mobile_notify": False,
        "schedule": False,
        "time_pause": False,
        "default_days": 7,
    },
    "basic": {
        "label": "基礎版",
        "sources": [MID_FREQ],
        "max_lot": 0.10,
        "martingale": False,
        "dynamic_lot": False,
        "partial_close": False,
        "breakeven": False,
        "mobile_notify": False,
        "schedule": False,
        "time_pause": False,
        "default_days": 30,
    },
    "advanced": {
        "label": "進階版",
        "sources": [MID_FREQ, HIGH_FREQ],
        "max_lot": None,          # None = 不限
        "martingale": True,       # 對齊官網/會員權益表:進階版(PRO)含馬丁與分批平倉
        "dynamic_lot": False,
        "partial_close": True,
        # 保本移損跟分批平倉是同一組「多 TP 處理」的權益, 一起從進階版開始給。
        # 體驗/基礎版只剩「單一點位」(照訊號的第一個止盈掛上去就不再管)。
        "breakeven": True,
        "mobile_notify": True,    # 手機跟單通知: 進階版(PRO)以上才有
        "schedule": True,         # 自動排程: 有或沒有, 不再分單一/多組/進階
        # 用量計時: 進階版(PRO)以上才有「非開盤/停止跟單自動暫停計時」。
        # 方案時間變成一份「使用額度」, 只有黃金開盤且正在跟單時才會扣。
        "time_pause": True,
        "default_days": 30,
    },
    "flagship": {
        "label": "旗艦版",
        "sources": [MID_FREQ, HIGH_FREQ, ULTRA_HIGH_FREQ, LOW_FREQ],
        "max_lot": None,
        "martingale": True,
        # 本金比例動態手數是旗艦版專屬。會員端會把選項畫給所有人看，
        # 但非旗艦版的 option 是 disabled，後端也會把偽造設定壓回均注。
        "dynamic_lot": True,
        "partial_close": True,
        "breakeven": True,
        "mobile_notify": True,
        "schedule": True,
        "time_pause": True,
        "default_days": 30,
    },
}

TIER_ORDER = ["trial", "basic", "advanced", "flagship"]

# 自動排程最多幾組。這不是等級差異(有這功能的等級都一樣多), 純粹是設定面板的
# 上限 —— 排程表是手動維護的清單, 超過十來組就沒人管得動了。
SCHEDULE_LIMIT = 10

# session 多久沒動就失效 (秒)。會員端每秒輪詢, 正常使用不會碰到;
# 這是為了讓「電腦直接關機、沒有登出」的 session 不要卡住帳號一輩子。
SESSION_IDLE_TIMEOUT = 24 * 3600

# last_seen_at 最短寫入間隔 (秒)。見 resolve_session 裡的說明 —— 這是把
# 「每次輪詢都寫磁碟」降成「每 30 秒才寫一次」的節流閥。
LAST_SEEN_WRITE_INTERVAL = 30.0

# 密碼雜湊參數。PBKDF2-HMAC-SHA256, 迭代次數取 OWASP 2023 對此演算法的建議值。
_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16

# 會員自己改密碼時的最低長度。系統發的初始密碼是 10 碼隨機, 這裡只擋
# 「改成更弱」—— 8 碼是 NIST SP 800-63B 對使用者自選密碼的下限。
MIN_PASSWORD_LENGTH = 8


# ── 密碼 ────────────────────────────────────────────────────────────────────
def hash_password(password: str, *, iterations: int = _PBKDF2_ITERATIONS) -> str:
    """回傳 `pbkdf2_sha256$<迭代>$<salt_b64>$<hash_b64>`。"""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    """用 compare_digest 比對，避免計時側通道洩漏。"""
    try:
        algo, iters, salt_b64, hash_b64 = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", (password or "").encode("utf-8"),
            base64.b64decode(salt_b64), int(iters),
        )
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


def generate_password(length: int = 10) -> str:
    """給管理員建帳號時用的初始密碼。

    刻意避開 0/O/1/l/I 這些看起來一樣的字元 —— 這組密碼多半是用 LINE
    貼給會員、或是會員自己照著打, 長得像的字元只會製造客服。
    """
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ── 等級 ────────────────────────────────────────────────────────────────────
def tier_entitlements(tier: str) -> Dict[str, Any]:
    """把等級展開成會員端看得懂的額度。未知等級一律降到最低權限。"""
    spec = TIERS.get(tier)
    if spec is None:
        logger.warning("unknown tier %r — falling back to no access", tier)
        return {"label": "未知", "sources": [], "max_lot": 0.01,
                "martingale": False, "dynamic_lot": False,
                "partial_close": False, "breakeven": False,
                "mobile_notify": False, "schedule": False, "plan_days": 30}
    return {
        "label": spec["label"],
        "sources": list(spec["sources"]),
        "max_lot": spec["max_lot"],
        "martingale": spec["martingale"],
        "dynamic_lot": bool(spec.get("dynamic_lot", False)),
        "partial_close": spec["partial_close"],
        "breakeven": bool(spec.get("breakeven", False)),
        "mobile_notify": bool(spec.get("mobile_notify", False)),
        "schedule": bool(spec.get("schedule", False)),
        "time_pause": bool(spec.get("time_pause", False)),
        # 一期是幾天。會員端的到期進度條拿它當滿格基準 —— 體驗版 7 天就該
        # 用 7 天當分母, 用 30 天算的話新開的體驗版帳號一進來只有 23% 滿。
        "plan_days": int(spec.get("default_days", 30)),
    }


def tier_catalog() -> List[Dict[str, Any]]:
    """給後台下拉選單用的等級清單。"""
    return [
        {"key": k, "label": TIERS[k]["label"], "sources": list(TIERS[k]["sources"]),
         "max_lot": TIERS[k]["max_lot"], "martingale": TIERS[k]["martingale"],
         "dynamic_lot": bool(TIERS[k].get("dynamic_lot", False)),
         "partial_close": TIERS[k]["partial_close"],
         "breakeven": bool(TIERS[k].get("breakeven", False)),
         "mobile_notify": bool(TIERS[k].get("mobile_notify", False)),
         "schedule": bool(TIERS[k].get("schedule", False)),
         "time_pause": bool(TIERS[k].get("time_pause", False)),
         "default_days": TIERS[k].get("default_days", 30)}
        for k in TIER_ORDER
    ]


def tier_has_time_pause(tier: str) -> bool:
    """這個等級是否採「用量計時」(非開盤/停止跟單暫停)。進階版以上為 True。"""
    return bool((TIERS.get(tier) or {}).get("time_pause", False))


# ── 黃金開盤時段 ────────────────────────────────────────────────────────────
# 用量計時只在「黃金有開盤」時才扣。這裡用 UTC 週末視窗判斷,刻意取「一定關盤」
# 的保守區間 (週五 21:00 UTC 收 ~ 週日 22:00 UTC 開),誤差一律偏向會員 (少扣一點)。
# 現貨黃金實際收在週五 21:00~22:00 UTC(依 DST)、週日 21:00~22:00 UTC 開,
# 取較早的收、較晚的開,落在灰色地帶的一兩小時算暫停,不會多扣會員的時間。
# 每日 1 小時的券商維護休息刻意不算(各券商不一)——寧可少扣。
def gold_market_open(now: Optional[float] = None) -> bool:
    t = time.gmtime(time.time() if now is None else now)
    wd = t.tm_wday        # 週一=0 … 週六=5, 週日=6
    h = t.tm_hour
    if wd == 5:                       # 週六整天關盤
        return False
    if wd == 6 and h < 22:            # 週日 22:00 UTC 前
        return False
    if wd == 4 and h >= 21:           # 週五 21:00 UTC 起
        return False
    return True


# 用量扣款的兩個閥值。會員端每 ~1 秒輪詢一次 /signals, 且「只在跟單時」才輪詢。
#
# USAGE_WRITE_INTERVAL: 兩次「實際扣款並寫入」的最短間隔。沒到間隔的輪詢純讀不寫,
#   把 per-second fsync 降成每 ~10 秒一次(呼應 last_seen_at 的節流考量)。
# USAGE_CONSUME_CAP_SECONDS: 單次扣款上限。作用有二 ——
#   (1) 停止跟單造成的長空窗, 恢復後第一筆最多只扣這麼多 → 等於「停止跟單=暫停」;
#   (2) 擋掉網路卡頓/系統睡眠造成的大跳。
#   取 30 秒: 大於寫入間隔(正常跟單每拍扣 ~10 秒, 不會被削到), 又遠小於任何真正的
#   空窗(停止跟單/關機動輒數分鐘以上), 讓空窗的殘值可忽略。
USAGE_WRITE_INTERVAL = 10.0
USAGE_CONSUME_CAP_SECONDS = 30.0


# ── 儲存 ────────────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    username           TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash      TEXT    NOT NULL,
    tier               TEXT    NOT NULL,
    expires_at         REAL,                      -- NULL = 永久
    status             TEXT    NOT NULL DEFAULT 'active',   -- active | suspended
    note               TEXT    NOT NULL DEFAULT '',
    created_at         REAL    NOT NULL,
    -- 單一裝置: 一個帳號同時只認一組 session, 新登入直接覆蓋舊的
    session_token      TEXT,
    session_device     TEXT    NOT NULL DEFAULT '',
    session_started_at REAL,
    last_seen_at       REAL
);
CREATE INDEX IF NOT EXISTS idx_members_session ON members(session_token);

CREATE TABLE IF NOT EXISTS login_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    username  TEXT    NOT NULL,
    at        REAL    NOT NULL,
    ok        INTEGER NOT NULL,
    device    TEXT    NOT NULL DEFAULT '',
    ip        TEXT    NOT NULL DEFAULT '',
    detail    TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_login_events_at ON login_events(at);
"""

# 登入紀錄保留上限。會員端不會頻繁登入, 這個量足夠追「我沒有共用啊」這種爭議,
# 又不會讓 256MB 的機器上的 db 無限長大。
_LOGIN_EVENT_CAP = 5000


class MemberStore:
    """會員資料存取。執行緒安全 —— Hub 是 ThreadingHTTPServer。"""

    def __init__(self, path: str):
        self.path = str(path)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            # WAL: 讀寫不互相阻塞。單機單程序, 這樣最省事也最不容易卡。
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    def _migrate(self) -> None:
        """加欄位式遷移。舊 db 沒有的欄位補上, 有的就跳過 —— 純附加, 不動既有資料。

        usage_seconds_left: 進階版以上的「使用額度」(秒)。NULL = 尚未初始化,
          第一次登入/輪詢時會從 expires_at 換算灌入。非進階版一律 NULL, 走舊的
          expires_at 日曆制。
        usage_seconds_total: 這個帳號「當初拿到多少額度」(秒), 給會員端的進度條當
          分母。沒有它的話只能拿等級的預設天數當滿格 —— 一個買 7 天試用的進階版
          帳號第一天就會顯示 7/30 = 23%, 看起來像快到期了。
        last_active_at: 上次「有在跟單且開盤」而扣時間的時間點, 用來算兩次輪詢的
          間隔。與 last_seen_at(閒置斷線/最後上線顯示)分開, 語意才不會打架。
        """
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(members)")}
        if "usage_seconds_left" not in cols:
            self._conn.execute("ALTER TABLE members ADD COLUMN usage_seconds_left REAL")
        if "last_active_at" not in cols:
            self._conn.execute("ALTER TABLE members ADD COLUMN last_active_at REAL")
        if "usage_seconds_total" not in cols:
            self._conn.execute("ALTER TABLE members ADD COLUMN usage_seconds_total REAL")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── 內部 ────────────────────────────────────────────────────────────
    @staticmethod
    def _row_to_public(row: sqlite3.Row, *, include_session: bool = False) -> Dict[str, Any]:
        """轉成可以送出去的 dict。password_hash 永遠不出現在回傳值裡。"""
        time_pause = tier_has_time_pause(row["tier"])
        d = {
            "username": row["username"],
            "tier": row["tier"],
            "tier_label": TIERS.get(row["tier"], {}).get("label", row["tier"]),
            "expires_at": row["expires_at"],
            "status": row["status"],
            "note": row["note"],
            "created_at": row["created_at"],
            "last_seen_at": row["last_seen_at"],
            "session_device": row["session_device"],
            "session_started_at": row["session_started_at"],
            "online": bool(row["session_token"]),
            "expired": _row_expired(row),
            # 用量計時: 進階版以上, 方案時間是一份「使用額度」(秒), 只有開盤+跟單才扣。
            "time_pause": time_pause,
            "usage_seconds_left": row["usage_seconds_left"] if time_pause else None,
            "usage_seconds_total": row["usage_seconds_total"] if time_pause else None,
        }
        if include_session:
            d["session_token"] = row["session_token"]
        return d

    def _get_row(self, username: str) -> Optional[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM members WHERE username = ? COLLATE NOCASE", (username,))
        return cur.fetchone()

    def _ensure_usage_locked(self, row: sqlite3.Row, now: float) -> Optional[float]:
        """回傳 time_pause 會員目前的使用額度(秒); 需要時就地初始化並寫回。

        必須在 self._lock 內呼叫。
          * 非 time_pause 等級 → 回 None(不適用)。
          * 已有額度 → 直接回。
          * 額度為 NULL 但有 expires_at(舊帳號從日曆制轉來)→ 用剩餘日曆時間灌入。
          * 額度為 NULL 且無 expires_at(永久帳號)→ 維持 None(永久, 不扣)。
        """
        if not tier_has_time_pause(row["tier"]):
            return None
        left = row["usage_seconds_left"]
        if left is not None:
            # 舊帳號沒有 total(這個欄位是後來加的)。用目前剩餘額度回填 ——
            # 猜不出當初發了多少, 但「現在就是滿的」至少不會讓進度條一開始
            # 就顯示成快到期; 之後正常遞減。
            if row["usage_seconds_total"] is None:
                self._conn.execute(
                    "UPDATE members SET usage_seconds_total = ? WHERE id = ?",
                    (float(left), row["id"]))
                self._conn.commit()
            return float(left)
        exp = row["expires_at"]
        if exp is None:
            return None      # 永久帳號: 不設額度
        seeded = max(0.0, float(exp) - now)
        self._conn.execute(
            "UPDATE members SET usage_seconds_left = ?, usage_seconds_total = ?,"
            " expires_at = NULL WHERE id = ?",
            (seeded, seeded, row["id"]))
        self._conn.commit()
        return seeded

    def _usage_total_locked(self, row: sqlite3.Row, left: Optional[float]) -> Optional[float]:
        """進度條的分母:當初發了多少額度。至少不小於目前剩餘, 免得超過 100%。"""
        if left is None:
            return None
        total = row["usage_seconds_total"]
        return max(float(total), float(left)) if total is not None else float(left)

    def _log_event(self, username: str, ok: bool, device: str, ip: str, detail: str) -> None:
        self._conn.execute(
            "INSERT INTO login_events (username, at, ok, device, ip, detail)"
            " VALUES (?,?,?,?,?,?)",
            (username, time.time(), 1 if ok else 0, device[:120], ip[:64], detail[:200]))
        self._conn.execute(
            "DELETE FROM login_events WHERE id NOT IN"
            " (SELECT id FROM login_events ORDER BY id DESC LIMIT ?)", (_LOGIN_EVENT_CAP,))

    # ── 管理端 ──────────────────────────────────────────────────────────
    def create_member(self, username: str, tier: str, *, password: str = "",
                      expires_at: Optional[float] = None, note: str = "",
                      days: Optional[int] = None) -> Dict[str, Any]:
        """建立會員。回傳含明文密碼（只有這一次拿得到，之後只剩雜湊）。"""
        username = (username or "").strip()
        if not username:
            raise ValueError("帳號不可空白")
        if len(username) > 64:
            raise ValueError("帳號太長")
        if tier not in TIERS:
            raise ValueError(f"未知等級: {tier}")

        plain = password or generate_password()
        if days is None and expires_at is None:
            days = TIERS[tier].get("default_days", 30)

        # 進階版以上是「用量制」: 方案天數變成一份使用額度(秒), 只在開盤+跟單時扣,
        # 不設日曆到期日。其他等級沿用日曆 expires_at。
        usage_seconds_left: Optional[float] = None
        if tier_has_time_pause(tier):
            if days is not None:
                usage_seconds_left = float(int(days) * 86400)
            elif expires_at is not None:
                usage_seconds_left = max(0.0, float(expires_at) - time.time())
            expires_at = None      # 用量制不看日曆
        elif expires_at is None and days is not None:
            expires_at = time.time() + int(days) * 86400
        # 進度條的分母 = 當初發的額度, 不是等級的預設天數。開一個 7 天試用的
        # 進階版帳號, 第一天就該是滿格, 而不是 7/30 = 23%。
        usage_seconds_total = usage_seconds_left

        with self._lock:
            if self._get_row(username) is not None:
                raise ValueError(f"帳號已存在: {username}")
            self._conn.execute(
                "INSERT INTO members (username, password_hash, tier, expires_at,"
                " usage_seconds_left, usage_seconds_total, status, note, created_at)"
                " VALUES (?,?,?,?,?,?,'active',?,?)",
                (username, hash_password(plain), tier, expires_at, usage_seconds_left,
                 usage_seconds_total, note or "", time.time()))
            self._conn.commit()
            row = self._get_row(username)

        out = self._row_to_public(row)
        out["password"] = plain          # 只在建立當下回傳
        return out

    def list_members(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM members ORDER BY created_at DESC").fetchall()
        return [self._row_to_public(r) for r in rows]

    def get_member(self, username: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._get_row(username)
        return self._row_to_public(row) if row else None

    def update_member(self, username: str, **fields: Any) -> Dict[str, Any]:
        """可改 tier / expires_at / status / note。改動立即對現有 session 生效。"""
        allowed = {"tier", "expires_at", "status", "note"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "tier" and v not in TIERS:
                raise ValueError(f"未知等級: {v}")
            if k == "status" and v not in ("active", "suspended"):
                raise ValueError(f"未知狀態: {v}")
            sets.append(f"{k} = ?")
            vals.append(v)
        if not sets:
            raise ValueError("沒有可更新的欄位")

        with self._lock:
            if self._get_row(username) is None:
                raise ValueError(f"查無帳號: {username}")
            vals.append(username)
            self._conn.execute(
                f"UPDATE members SET {', '.join(sets)} WHERE username = ? COLLATE NOCASE",
                vals)
            # 停權「不」清 session_token。resolve_session 每次都會重查 status，
            # 所以人一樣是立刻被擋下 —— 但 token 留著，錯誤碼才能回 "suspended"
            # 而不是 "session_invalid"。差別在會員端顯示的是「帳號已停權，請聯繫
            # 管理員」還是「已在其他裝置登入」，後者會讓他到處找不存在的第二台。
            # 解除停權後原本那台也能直接接回去，不必重新登入。
            self._conn.commit()
            row = self._get_row(username)
        return self._row_to_public(row)

    def extend(self, username: str, days: int) -> Dict[str, Any]:
        """續期。

        用量制(進階版以上): 直接把天數加進使用額度(秒), 不看日曆。額度尚未初始化
          的舊帳號先從剩餘日曆時間灌入再加。
        日曆制(其他等級): 從「現在」和「原到期日」取較晚者往後加, 避免早續期反而虧天數。
        """
        now = time.time()
        with self._lock:
            row = self._get_row(username)
            if row is None:
                raise ValueError(f"查無帳號: {username}")
            if tier_has_time_pause(row["tier"]):
                current = self._ensure_usage_locked(row, now) or 0.0
                new_left = current + int(days) * 86400
                # 續期後把分母一起拉到新的額度 —— 否則續了 30 天, 進度條還在拿
                # 舊的 7 天當滿格, 會爆到 400%(前端夾在 100% 就變成永遠滿格)。
                self._conn.execute(
                    "UPDATE members SET usage_seconds_left = ?, usage_seconds_total = ?,"
                    " expires_at = NULL WHERE username = ? COLLATE NOCASE",
                    (new_left, new_left, username))
            else:
                base = max(now, float(row["expires_at"] or 0))
                new_exp = base + int(days) * 86400
                self._conn.execute(
                    "UPDATE members SET expires_at = ? WHERE username = ? COLLATE NOCASE",
                    (new_exp, username))
            self._conn.commit()
            row = self._get_row(username)
        return self._row_to_public(row)

    def reset_password(self, username: str, new_password: str = "") -> Dict[str, Any]:
        plain = new_password or generate_password()
        with self._lock:
            if self._get_row(username) is None:
                raise ValueError(f"查無帳號: {username}")
            # 改密碼等於強制登出所有裝置
            self._conn.execute(
                "UPDATE members SET password_hash = ?, session_token = NULL"
                " WHERE username = ? COLLATE NOCASE", (hash_password(plain), username))
            self._conn.commit()
            row = self._get_row(username)
        out = self._row_to_public(row)
        out["password"] = plain
        return out

    def delete_member(self, username: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM members WHERE username = ? COLLATE NOCASE", (username,))
            self._conn.commit()
        return cur.rowcount > 0

    def kick(self, username: str) -> bool:
        """把該帳號目前的 session 作廢（不改密碼）。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE members SET session_token = NULL WHERE username = ? COLLATE NOCASE",
                (username,))
            self._conn.commit()
        return cur.rowcount > 0

    def recent_logins(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT username, at, ok, device, ip, detail FROM login_events"
                " ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    # ── 會員端 ──────────────────────────────────────────────────────────
    def login(self, username: str, password: str, *, device: str = "",
              ip: str = "") -> Tuple[Optional[Dict[str, Any]], str]:
        """回傳 (結果, 錯誤碼)。成功時結果含 session_token 與額度。

        錯誤碼刻意不區分「帳號不存在」與「密碼錯誤」—— 都回 bad_credentials,
        免得變成帳號列舉的工具。
        """
        username = (username or "").strip()
        with self._lock:
            row = self._get_row(username)

            if row is None or not verify_password(password, row["password_hash"]):
                # 帳號不存在時也要花掉差不多的時間, 否則回應快慢會洩漏帳號存在與否
                if row is None:
                    hash_password(password or "x")
                self._log_event(username or "(空白)", False, device, ip, "bad_credentials")
                self._conn.commit()
                return None, "bad_credentials"

            if row["status"] != "active":
                self._log_event(username, False, device, ip, "suspended")
                self._conn.commit()
                return None, "suspended"

            if _row_expired(row):
                self._log_event(username, False, device, ip, "expired")
                self._conn.commit()
                return None, "expired"

            token = secrets.token_urlsafe(32)
            now = time.time()
            kicked = bool(row["session_token"])
            self._conn.execute(
                "UPDATE members SET session_token = ?, session_device = ?,"
                " session_started_at = ?, last_seen_at = ? WHERE id = ?",
                (token, device[:120], now, now, row["id"]))
            self._log_event(username, True, device, ip,
                            "kicked_previous" if kicked else "ok")
            self._conn.commit()
            row = self._get_row(username)
            # 登入即初始化使用額度(舊帳號從剩餘日曆時間換算)。登入不扣時間。
            usage_left = self._ensure_usage_locked(row, now)
            row = self._get_row(row["username"])

        out = self._row_to_public(row)
        out["session_token"] = token
        out["entitlements"] = tier_entitlements(row["tier"])
        out["kicked_previous"] = kicked
        if tier_has_time_pause(row["tier"]):
            out["usage"] = {"time_pause": True, "seconds_left": usage_left,
                            "seconds_total": self._usage_total_locked(row, usage_left),
                            "market_open": gold_market_open(now), "consuming": False}
        return out, ""

    def resolve_session(self, token: str, *, consume: bool = False
                        ) -> Tuple[Optional[Dict[str, Any]], str]:
        """把 session token 換成會員。每次呼叫都重新檢查期限與狀態。

        等級/期限/停權在後台一改，下一次輪詢就生效，不必等會員重新登入。

        consume=True: 這一次呼叫代表「會員正在跟單」(只有 /signals 輪詢會傳, 而
          會員端只在跟單時才輪詢 /signals)。對用量制會員, 會依「開盤與否」扣掉自上次
          扣款以來的時間。非跟單的呼叫(/auth/me 續期)一律 consume=False, 只讀不扣。
        """
        if not token:
            return None, "no_token"
        now = time.time()
        usage_block: Optional[Dict[str, Any]] = None
        fresh_left: Optional[float] = None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM members WHERE session_token = ?", (token,)).fetchone()
            if row is None:
                # 找不到 = 要嘛沒登入過, 要嘛被新裝置踢掉了
                return None, "session_invalid"
            if row["status"] != "active":
                return None, "suspended"
            if _row_expired(row):
                return None, "expired"
            last = float(row["last_seen_at"] or 0)
            if last and now - last > SESSION_IDLE_TIMEOUT:
                self._conn.execute(
                    "UPDATE members SET session_token = NULL WHERE id = ?", (row["id"],))
                self._conn.commit()
                return None, "session_expired"

            # ── 用量計時(進階版以上)──────────────────────────────────────
            if tier_has_time_pause(row["tier"]):
                fresh_left = self._ensure_usage_locked(row, now)   # 可能寫入(初始化)
                market = gold_market_open(now)
                consuming = False
                # fresh_left is None = 永久帳號, 不扣
                if consume and fresh_left is not None:
                    last_active = float(row["last_active_at"] or 0)
                    if last_active <= 0:
                        # 這個跟單時段的第一拍: 只記時間點, 不扣
                        self._conn.execute(
                            "UPDATE members SET last_active_at = ? WHERE id = ?",
                            (now, row["id"]))
                        self._conn.commit()
                        consuming = market
                    else:
                        gap = now - last_active
                        # 節流: 沒到寫入間隔就純讀不寫(省 fsync)
                        if gap >= USAGE_WRITE_INTERVAL:
                            billed = min(gap, USAGE_CONSUME_CAP_SECONDS) if market else 0.0
                            fresh_left = max(0.0, fresh_left - billed)
                            self._conn.execute(
                                "UPDATE members SET usage_seconds_left = ?,"
                                " last_active_at = ? WHERE id = ?",
                                (fresh_left, now, row["id"]))
                            self._conn.commit()
                            consuming = market and billed > 0
                            if fresh_left <= 0:
                                # 額度用盡 → 立刻作廢 session, 會員端會被登出並提示續費
                                self._conn.execute(
                                    "UPDATE members SET session_token = NULL WHERE id = ?",
                                    (row["id"],))
                                self._conn.commit()
                                return None, "expired"
                        else:
                            consuming = market
                usage_block = {"time_pause": True, "seconds_left": fresh_left,
                               "seconds_total": self._usage_total_locked(row, fresh_left),
                               "market_open": market, "consuming": consuming}

            # last_seen_at 節流。會員端每秒輪詢一次, 每次都寫就是每秒一次
            # fsync —— 實測單次 resolve 要 4ms, 百人上線就會把 Hub 那台
            # shared-cpu/網路磁碟的機器吃滿。節流之後 99% 的輪詢是純讀。
            #
            # 精度夠用: 這個欄位只服務兩件事 —— 閒置 24 小時斷線判定, 以及
            # 後台「最後上線」顯示。兩者都不在乎 30 秒的誤差。
            if now - last >= LAST_SEEN_WRITE_INTERVAL:
                self._conn.execute(
                    "UPDATE members SET last_seen_at = ? WHERE id = ?", (now, row["id"]))
                self._conn.commit()

        member = self._row_to_public(row)
        member["entitlements"] = tier_entitlements(row["tier"])
        if usage_block is not None:
            member["usage"] = usage_block
            member["usage_seconds_left"] = usage_block["seconds_left"]
        return member, ""

    def change_password(self, token: str, old_password: str,
                        new_password: str) -> Tuple[bool, str]:
        """會員自己改密碼。回傳 (是否成功, 錯誤碼)。

        跟管理員的 reset_password 有兩個關鍵差異：

        1. 要驗舊密碼。否則有人趁會員電腦沒鎖就能把帳號整個接管過去
           —— session 已經在那台機器上了，不驗舊密碼等於零門檻。
        2. **保留呼叫者當下的 session**。單一裝置模式下就只有這一個
           session，改完密碼還把自己踢掉會很莫名其妙。其他裝置本來就
           不可能有 session（新登入會覆蓋），所以不必額外清。
        """
        if not token:
            return False, "no_token"
        new_password = new_password or ""
        if len(new_password) < MIN_PASSWORD_LENGTH:
            return False, "too_short"

        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM members WHERE session_token = ?", (token,)).fetchone()
            if row is None:
                return False, "session_invalid"
            if row["status"] != "active":
                return False, "suspended"
            if _row_expired(row):
                return False, "expired"
            if not verify_password(old_password, row["password_hash"]):
                self._log_event(row["username"], False, row["session_device"], "",
                                "change_pw_bad_old")
                self._conn.commit()
                return False, "bad_old_password"
            if verify_password(new_password, row["password_hash"]):
                return False, "same_as_old"

            self._conn.execute(
                "UPDATE members SET password_hash = ? WHERE id = ?",
                (hash_password(new_password), row["id"]))
            self._log_event(row["username"], True, row["session_device"], "",
                            "password_changed")
            self._conn.commit()
        return True, ""

    def logout(self, token: str) -> bool:
        if not token:
            return False
        with self._lock:
            cur = self._conn.execute(
                "UPDATE members SET session_token = NULL WHERE session_token = ?", (token,))
            self._conn.commit()
        return cur.rowcount > 0


def _is_expired(expires_at: Optional[float]) -> bool:
    """None = 永久有效。"""
    if expires_at is None:
        return False
    try:
        return time.time() > float(expires_at)
    except (TypeError, ValueError):
        return True     # 壞掉的值當成過期, 寧可擋下也不要放行


def _row_expired(row: sqlite3.Row) -> bool:
    """依等級判斷是否到期。

    進階版以上(用量制): 額度 usage_seconds_left 用完(<=0)才算到期。額度尚未
    初始化(NULL)時暫以 expires_at 判斷 —— 登入/輪詢時會把它從 expires_at 補灌,
    補灌前用日曆制擋一下,不會誤放行。
    其他等級(日曆制): 沿用 expires_at。
    """
    if tier_has_time_pause(row["tier"]):
        left = row["usage_seconds_left"]
        if left is None:
            return _is_expired(row["expires_at"])
        try:
            return float(left) <= 0
        except (TypeError, ValueError):
            return True
    return _is_expired(row["expires_at"])


def filter_signals_for(records: List[Dict[str, Any]],
                       allowed_sources: List[str]) -> List[Dict[str, Any]]:
    """依會員可存取的來源過濾訊號。這是伺服器端的收費閘門。

    比對用中央機發布時的 source / source_name 欄位。兩個都讀是因為
    cancel_signal 跟 trade_signal 兩種 payload 的欄位習慣不完全一致。

    找不到來源標記的紀錄一律**不給** —— 寧可漏送也不要把付費訊號送給
    沒買那個來源的人。
    """
    allow = set(allowed_sources or [])
    if not allow:
        return []
    out = []
    for r in records:
        src = r.get("source") or r.get("source_name")
        if src and src in allow:
            out.append(r)
    return out
