"""會員系統 — 帳號、等級、期限、單一裝置 session。

只用標準庫。這個模組會被打進 Hub 的 Docker 映像檔，而那個映像檔刻意
只複製 copy_trader/central 且不裝任何第三方套件（見 Dockerfile），所以
這裡不能出現 bcrypt / passlib / sqlalchemy 之類的東西。

資料落在 Hub 的持久化磁碟 (/data/members.db)，跟訊號紀錄同一顆 volume。

權限的執行點分兩層，要清楚知道差別：

  * allowed_sources — **伺服器端**強制。Hub 在 /signals 就把該會員無權
    存取的來源濾掉，資料根本不會離開伺服器。這是真正的收費閘門。

  * max_lot / martingale / partial — **用戶端**自律。Hub 只在登入時把
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

TIERS: Dict[str, Dict[str, Any]] = {
    "trial": {
        "label": "體驗版",
        "sources": [MID_FREQ],
        "max_lot": 0.01,
        "martingale": False,
        "partial_close": False,
        "default_days": 7,
    },
    "basic": {
        "label": "基礎版",
        "sources": [MID_FREQ],
        "max_lot": 0.10,
        "martingale": False,
        "partial_close": False,
        "default_days": 30,
    },
    "advanced": {
        "label": "進階版",
        "sources": [MID_FREQ, HIGH_FREQ],
        "max_lot": None,          # None = 不限
        "martingale": False,
        "partial_close": False,
        "default_days": 30,
    },
    "flagship": {
        "label": "旗艦版",
        "sources": [MID_FREQ, HIGH_FREQ],
        "max_lot": None,
        "martingale": True,
        "partial_close": True,
        "default_days": 30,
    },
}

TIER_ORDER = ["trial", "basic", "advanced", "flagship"]

# session 多久沒動就失效 (秒)。會員端每秒輪詢, 正常使用不會碰到;
# 這是為了讓「電腦直接關機、沒有登出」的 session 不要卡住帳號一輩子。
SESSION_IDLE_TIMEOUT = 24 * 3600

# 密碼雜湊參數。PBKDF2-HMAC-SHA256, 迭代次數取 OWASP 2023 對此演算法的建議值。
_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16


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
                "martingale": False, "partial_close": False}
    return {
        "label": spec["label"],
        "sources": list(spec["sources"]),
        "max_lot": spec["max_lot"],
        "martingale": spec["martingale"],
        "partial_close": spec["partial_close"],
    }


def tier_catalog() -> List[Dict[str, Any]]:
    """給後台下拉選單用的等級清單。"""
    return [
        {"key": k, "label": TIERS[k]["label"], "sources": list(TIERS[k]["sources"]),
         "max_lot": TIERS[k]["max_lot"], "martingale": TIERS[k]["martingale"],
         "partial_close": TIERS[k]["partial_close"],
         "default_days": TIERS[k].get("default_days", 30)}
        for k in TIER_ORDER
    ]


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
            # WAL: 讀寫不互相阻塞。單機單程序, 這樣最省事也最不容易卡。
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── 內部 ────────────────────────────────────────────────────────────
    @staticmethod
    def _row_to_public(row: sqlite3.Row, *, include_session: bool = False) -> Dict[str, Any]:
        """轉成可以送出去的 dict。password_hash 永遠不出現在回傳值裡。"""
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
            "expired": _is_expired(row["expires_at"]),
        }
        if include_session:
            d["session_token"] = row["session_token"]
        return d

    def _get_row(self, username: str) -> Optional[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM members WHERE username = ? COLLATE NOCASE", (username,))
        return cur.fetchone()

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
        if expires_at is None and days is None:
            days = TIERS[tier].get("default_days", 30)
        if expires_at is None and days is not None:
            expires_at = time.time() + int(days) * 86400

        with self._lock:
            if self._get_row(username) is not None:
                raise ValueError(f"帳號已存在: {username}")
            self._conn.execute(
                "INSERT INTO members (username, password_hash, tier, expires_at,"
                " status, note, created_at) VALUES (?,?,?,?,'active',?,?)",
                (username, hash_password(plain), tier, expires_at, note or "", time.time()))
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
        """續期。從「現在」和「原到期日」取較晚者往後加，避免早續期反而虧天數。"""
        with self._lock:
            row = self._get_row(username)
            if row is None:
                raise ValueError(f"查無帳號: {username}")
            base = max(time.time(), float(row["expires_at"] or 0))
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

            if _is_expired(row["expires_at"]):
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

        out = self._row_to_public(row)
        out["session_token"] = token
        out["entitlements"] = tier_entitlements(row["tier"])
        out["kicked_previous"] = kicked
        return out, ""

    def resolve_session(self, token: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """把 session token 換成會員。每次呼叫都重新檢查期限與狀態。

        等級/期限/停權在後台一改，下一次輪詢就生效，不必等會員重新登入。
        """
        if not token:
            return None, "no_token"
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM members WHERE session_token = ?", (token,)).fetchone()
            if row is None:
                # 找不到 = 要嘛沒登入過, 要嘛被新裝置踢掉了
                return None, "session_invalid"
            if row["status"] != "active":
                return None, "suspended"
            if _is_expired(row["expires_at"]):
                return None, "expired"
            last = float(row["last_seen_at"] or 0)
            if last and now - last > SESSION_IDLE_TIMEOUT:
                self._conn.execute(
                    "UPDATE members SET session_token = NULL WHERE id = ?", (row["id"],))
                self._conn.commit()
                return None, "session_expired"
            self._conn.execute(
                "UPDATE members SET last_seen_at = ? WHERE id = ?", (now, row["id"]))
            self._conn.commit()

        member = self._row_to_public(row)
        member["entitlements"] = tier_entitlements(row["tier"])
        return member, ""

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
