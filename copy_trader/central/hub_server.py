"""
Central signal hub.

The hub is intentionally small and dependency-free: one always-on signal
computer posts normalized trading signals, and each client agent polls them.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
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

    def _current_member(self) -> Optional[Dict[str, Any]]:
        """把 session token 換成會員；不是會員就回 None。

        每次都重新查 —— 等級/期限/停權在後台一改, 下一次輪詢就生效。
        """
        store = self.members
        if store is None:
            return None
        for tok in self._presented_tokens():
            if tok == self.token:
                continue        # 那是管理 token, 不是會員 session
            member, _err = store.resolve_session(tok)
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
                    "signals": records,
                })
                return

            # 會員 session = 只拿得到自己等級授權的來源。
            #
            # 這裡是整套收費機制唯一真正的閘門: 沒買的來源, 資料根本不會離開
            # 伺服器。會員端怎麼改都拿不到。
            member = self._current_member()
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

        if parsed.path in ("/auth/login", "/auth/logout", "/auth/change-password"):
            self._handle_auth_post(parsed)
            return
        if parsed.path.startswith("/admin/"):
            self._handle_admin_post(parsed)
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
      <thead><tr><th>Seq</th><th>來源</th><th>方向</th><th>Entry</th><th>SL</th><th>TP</th><th>時間</th></tr></thead>
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
        const sig = item.signal || {};
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${esc(item.seq)}</td><td>${esc(item.source)}</td><td><span class="pill">${esc(sig.direction)}</span></td><td>${esc(sig.entry_price ?? "market")}</td><td>${esc(sig.stop_loss)}</td><td>${esc((sig.take_profit || []).join(", "))}</td><td class="muted">${esc(new Date((item.published_at || 0) * 1000).toLocaleString())}</td>`;
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
                 token: str, members: Optional["membership.MemberStore"] = None):
        super().__init__(server_address, handler_class)
        self.store = store
        self.token = token
        self.members = members


def run_server(host: str, port: int, store_path: Path, token: str = "",
               members_path: Optional[Path] = None) -> None:
    store = SignalStore(store_path)

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

    httpd = HubHTTPServer((host, port), HubRequestHandler, store, token, members)
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
