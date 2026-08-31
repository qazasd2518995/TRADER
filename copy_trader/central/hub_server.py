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
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

try:
    from copy_trader.config import DATA_DIR
except Exception:
    DATA_DIR = Path.cwd()

from copy_trader.central import membership

logger = logging.getLogger(__name__)


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
            "reported_at": time.time(),
        }
        with self._lock:
            self._by_user[username] = record

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {user: dict(record) for user, record in self._by_user.items()}


class LineNotifyState:
    """LINE 群組通知：登記 Bot 所在群組 + 推播封裝。狀態存磁碟(跨重啟)。

    token / secret 從環境變數讀(fly secret)。沒有 token 就整個停用(push 變
    no-op)，Hub 其他功能完全不受影響 —— 通知是加值旁路，永遠不能拖垮訊號流。
    """

    def __init__(self, state_path: Path, token: str = "", secret: str = ""):
        self.state_path = Path(state_path)
        self.token = token or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        self.secret = secret or os.environ.get("LINE_CHANNEL_SECRET", "")
        self._lock = threading.Lock()
        self._groups: Dict[str, Dict[str, Any]] = {}
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
            return True
        except Exception as exc:                        # noqa: BLE001
            logger.warning("LINE push 失敗：%s", exc)
            return False


def format_signal_notice(record: Dict[str, Any]) -> Optional[str]:
    """把 Hub 訊號 record 轉成給會員看的 LINE 通知文字。None = 不通知。"""
    when = str(record.get("message_time") or "").strip()
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

    sig = record.get("signal") if isinstance(record.get("signal"), dict) else {}
    direction = str(sig.get("direction") or "").upper()
    dir_zh = {"BUY": "買進 BUY", "SELL": "賣出 SELL"}.get(direction, direction or "—")
    symbol = str(sig.get("symbol") or "XAUUSD")
    entry = sig.get("entry_price")
    if entry is None:
        return None      # 沒有進場價的不是可掛單訊號，不通知
    sl = sig.get("stop_loss")
    tps = sig.get("take_profit") or []
    tp_str = "／".join(str(t) for t in tps) if tps else "—"
    return "\n".join([
        f"📌 新訊號{f' · {when}' if when else ''}",
        f"{symbol} {dir_zh}",
        f"進場 {entry}｜止損 {sl if sl is not None else '—'}｜止盈 {tp_str}",
        "✅ 已發送掛單",
        "※ 訊號來源為第三方，僅供參考，請自負盈虧",
    ])


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
    def line(self) -> Optional["LineNotifyState"]:
        return getattr(self.server, "line", None)

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

    def _current_member(self, *, consume: bool = False) -> Optional[Dict[str, Any]]:
        """把 session token 換成會員；不是會員就回 None。

        每次都重新查 —— 等級/期限/停權在後台一改, 下一次輪詢就生效。

        consume=True 只在 /signals 輪詢時傳(會員端只在跟單時才輪詢 /signals),
        用量制會員會依此扣掉開盤時的跟單時間。其他呼叫一律不扣。
        """
        store = self.members
        if store is None:
            return None
        for tok in self._presented_tokens():
            if tok == self.token:
                continue        # 那是管理 token, 不是會員 session
            member, _err = store.resolve_session(tok, consume=consume)
            if member is not None:
                return member
        return None

    def _member_auth_error(self) -> str:
        """會員 token 解不開時的原因，用來給前端顯示人看得懂的訊息。"""
        store = self.members
        if store is None:
            return "membership_unavailable"
        worst = "session_invalid"
        for tok in self._presented_tokens():
            if tok == self.token:
                continue
            _m, err = store.resolve_session(tok)
            if err in ("expired", "suspended"):
                return err      # 這兩個要明確告訴會員, 否則他不知道要續費
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
            member = self._current_member(consume=True)
            if member is None:
                self._send_json(401, {"ok": False, "error": self._member_auth_error()})
                return
            allowed = member["entitlements"]["sources"]
            records = self.store.list_after(after=after, limit=limit)
            visible = membership.filter_signals_for(records, allowed)
            self._send_json(200, {
                "ok": True,
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

        if parsed.path.startswith("/admin/"):
            self._handle_admin_get(parsed)
            return

        self._send_json(404, {"ok": False, "error": "not_found"})

    # ── 會員後台 (管理 token) ───────────────────────────────────────────
    def _handle_admin_get(self, parsed) -> None:
        if not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        if parsed.path == "/admin/line/status":
            line = self.line
            self._send_json(200, {"ok": True,
                                  "enabled": bool(line and line.enabled),
                                  "has_secret": bool(line and line.secret),
                                  "groups": line.target_groups() if line else []})
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
            self._send_json(200, {"ok": True, "statuses": statuses})
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

        member, err = store.login(
            str(data.get("username") or ""),
            str(data.get("password") or ""),
            device=str(data.get("device") or ""),
            ip=self._client_ip(),
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
        if parsed.path.startswith("/admin/"):
            self._handle_admin_post(parsed)
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
            member = self._current_member()
            if member is None:
                self._send_json(401, {"ok": False, "error": self._member_auth_error()})
                return
            status_store.update(str(member.get("username") or ""), data)
            self._send_json(200, {"ok": True})
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
                    threading.Thread(target=line.push_text, args=(text,),
                                     daemon=True).start()

        self._send_json(200, {
            "ok": True,
            "published": published,
            "latest_seq": self.store.latest_seq,
        })

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
        const sig = cancelled ? ((item.target_signals || [])[0] || {}) : (item.signal || {});
        const eventLabel = cancelled
          ? (item.cancel_reason === "line_unsent" ? "訊息收回" : "引用撤單")
          : "新報單";
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
                 line: Optional["LineNotifyState"] = None):
        super().__init__(server_address, handler_class)
        self.store = store
        self.token = token
        self.members = members
        self.member_status = member_status
        self.line = line


def run_server(host: str, port: int, store_path: Path, token: str = "",
               members_path: Optional[Path] = None) -> None:
    store = SignalStore(store_path)
    member_status = MemberStatusStore()
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

    httpd = HubHTTPServer((host, port), HubRequestHandler, store, token, members, member_status, line)
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
