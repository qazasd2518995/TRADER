"""Exness Partnership API 的唯讀查詢封裝。

用途：把「跟單會員」對上「Exness IB 客戶」，讓訊號中心的會員管理看得到每位
會員的交易量與產生的佣金。對應鍵是會員端上報的 MT5 login —— 那就是 Exness
的 client_account，不需要另外手動綁定。

刻意只呼叫報表端點。同一組憑證在 Exness 那邊也能操作返佣群組與付款
(/api/v2/autorebates/approve、/payments/retry 等)，這個模組完全不碰那些。

沒設憑證就整個停用(查詢回空)，Hub 其他功能不受任何影響 —— 這是加值資訊，
永遠不能拖垮訊號流。

⚠️ 這份 Swagger spec 沒有定義 response schema(definitions 是空的)，所以
report 內每一列的實際欄位名要等真實資料才能確定。下面用一組候選鍵去猜，
並且**原封不動保留整列原始資料**，之後看到真實回應就能對準。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://my.exnessaffiliates.com"

# 這份報表每列可能用哪些欄位名表示「客戶帳號 / 交易量 / 佣金」。
# spec 沒定義 schema，先用候選鍵比對；真實資料進來後可以收斂成確定的那個。
_ACCOUNT_KEYS = ("client_account", "account", "login", "client_login",
                 "trading_account", "account_number")
_VOLUME_KEYS = ("volume_lots", "volume", "lots", "trade_volume", "lot")
_REWARD_KEYS = ("reward", "reward_usd", "reward_amount", "commission",
                "amount", "partner_reward")


def _first_value(row: Dict[str, Any], keys) -> Optional[Any]:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class ExnessPartnerClient:
    """認證 + 報表查詢 + 快取。執行緒安全，所有失敗都吞掉回空。"""

    # JWT 實際有效期未知，保守地每小時重新認證一次。
    TOKEN_TTL = 3600.0
    # 報表快取。Exness 端可能有速率限制，而這份資料本來就不需要即時。
    CACHE_TTL = 900.0

    def __init__(self, login: str = "", password: str = "", timeout: float = 20.0):
        self.login = login or os.environ.get("EXNESS_PARTNER_LOGIN", "")
        self.password = password or os.environ.get("EXNESS_PARTNER_PASSWORD", "")
        self.timeout = timeout
        self._lock = threading.Lock()
        self._token = ""
        self._token_at = 0.0
        self._cache: Dict[str, Any] = {}
        self._cache_at = 0.0
        self.last_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.login and self.password)

    # ── HTTP ────────────────────────────────────────────────────────────
    def _request(self, path: str, *, method: str = "GET",
                 payload: Optional[Dict[str, Any]] = None,
                 token: str = "") -> Optional[Dict[str, Any]]:
        url = f"{BASE_URL}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json",
                                              "Accept": "application/json"})
        if token:
            req.add_header("Authorization", f"JWT {token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:200]
            except Exception:                       # noqa: BLE001
                pass
            self.last_error = f"http_{exc.code}"
            logger.warning("Exness API %s %s → HTTP %s %s", method, path, exc.code, detail)
        except Exception as exc:                    # noqa: BLE001
            self.last_error = "network"
            logger.warning("Exness API %s %s 失敗：%s", method, path, exc)
        return None

    def _ensure_token(self) -> str:
        """取得(必要時重新申請) JWT。回空字串代表拿不到。"""
        now = time.time()
        with self._lock:
            if self._token and now - self._token_at < self.TOKEN_TTL:
                return self._token
        if not self.enabled:
            return ""
        # 認證欄位名沒有明確文件；官方說明是「Partner 後台的 email 與密碼」，
        # 兩種常見寫法都試一次。
        for field in ("login", "email"):
            body = self._request("/api/v2/auth/", method="POST",
                                 payload={field: self.login, "password": self.password})
            token = str((body or {}).get("token") or "")
            if token:
                with self._lock:
                    self._token, self._token_at = token, time.time()
                self.last_error = ""
                logger.info("Exness Partnership API 認證成功")
                return token
        logger.warning("Exness Partnership API 認證失敗：%s", self.last_error or "unknown")
        return ""

    # ── 報表 ────────────────────────────────────────────────────────────
    def _fetch_rows(self, path: str, params: Dict[str, Any], token: str) -> List[Dict[str, Any]]:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        body = self._request(f"{path}?{query}", token=token)
        if not isinstance(body, dict):
            return []
        data = body.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        results = body.get("results")
        return [row for row in results if isinstance(row, dict)] if isinstance(results, list) else []

    def summary_by_account(self, *, date_from: str = "", date_to: str = "",
                           force: bool = False) -> Dict[str, Any]:
        """以 client_account 為鍵，彙總每個帳號的交易量與佣金。

        回傳 {"enabled":bool, "accounts":{account: {...}}, "fetched_at":ts,
              "error":str, "raw_sample":[...]}。raw_sample 保留前幾列原始資料，
        方便第一次接上真實客戶時對照欄位名。
        """
        if not self.enabled:
            return {"enabled": False, "accounts": {}, "error": "not_configured",
                    "fetched_at": 0.0, "raw_sample": []}

        now = time.time()
        with self._lock:
            if not force and self._cache and now - self._cache_at < self.CACHE_TTL:
                return dict(self._cache)

        token = self._ensure_token()
        if not token:
            out = {"enabled": True, "accounts": {}, "error": self.last_error or "auth_failed",
                   "fetched_at": now, "raw_sample": []}
            return out

        params = {"limit": 1000}
        if date_from:
            params["reward_date_from"] = date_from
        if date_to:
            params["reward_date_to"] = date_to
        rows = self._fetch_rows("/api/reports/rewards/", params, token)

        accounts: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            account = _first_value(row, _ACCOUNT_KEYS)
            if account in (None, ""):
                continue
            key = str(account).strip()
            bucket = accounts.setdefault(key, {"account": key, "volume_lots": 0.0,
                                               "reward": 0.0, "rows": 0})
            bucket["volume_lots"] += _as_float(_first_value(row, _VOLUME_KEYS))
            bucket["reward"] += _as_float(_first_value(row, _REWARD_KEYS))
            bucket["rows"] += 1

        out = {
            "enabled": True,
            "accounts": accounts,
            "error": "" if rows or not self.last_error else self.last_error,
            "fetched_at": now,
            # 只留前 3 列、且只留鍵名與值，用來對準真實欄位名
            "raw_sample": rows[:3],
        }
        with self._lock:
            self._cache, self._cache_at = dict(out), now
        return out
