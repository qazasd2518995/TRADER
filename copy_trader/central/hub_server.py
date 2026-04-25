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

logger = logging.getLogger(__name__)


class SignalStore:
    """Append-only JSONL store for hub signals."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._records: List[Dict[str, Any]] = []
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
                    self._latest_seq = max(self._latest_seq, seq)
        except OSError as e:
            logger.warning("failed to load signal store %s: %s", self.path, e)

    def publish(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        record = dict(payload)
        with self._lock:
            self._latest_seq += 1
            record["seq"] = self._latest_seq
            record.setdefault("id", f"sig_{self._latest_seq}_{uuid.uuid4().hex[:8]}")
            record.setdefault("type", "trade_signal")
            record.setdefault("published_at", now)
            self._records.append(record)
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

    def _authorized(self) -> bool:
        if not self.token:
            return True
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        query_token = (qs.get("token") or [""])[0]
        header_token = self.headers.get("X-Hub-Token", "")
        auth = self.headers.get("Authorization", "")
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        return self.token in {query_token, header_token, bearer}

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
            })
            return

        if parsed.path == "/":
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            self._send_html(200, self._dashboard_html())
            return

        if parsed.path == "/signals":
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            qs = parse_qs(parsed.query)
            after = int((qs.get("after") or ["0"])[0] or 0)
            limit = int((qs.get("limit") or ["100"])[0] or 100)
            records = self.store.list_after(after=after, limit=limit)
            self._send_json(200, {
                "ok": True,
                "latest_seq": self.store.latest_seq,
                "signals": records,
            })
            return

        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/signals":
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
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
    def __init__(self, server_address: tuple, handler_class: type, store: SignalStore, token: str):
        super().__init__(server_address, handler_class)
        self.store = store
        self.token = token


def run_server(host: str, port: int, store_path: Path, token: str = "") -> None:
    store = SignalStore(store_path)
    httpd = HubHTTPServer((host, port), HubRequestHandler, store, token)
    logger.info("signal hub listening on http://%s:%s (store=%s)", host, port, store_path)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("signal hub stopped")
    finally:
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the central copy-trader signal hub.")
    parser.add_argument("--host", default=os.environ.get("COPY_TRADER_HUB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("COPY_TRADER_HUB_PORT", "8765")))
    parser.add_argument("--store", default=os.environ.get("COPY_TRADER_HUB_STORE", str(DATA_DIR / "central_hub_signals.jsonl")))
    parser.add_argument("--token", default=os.environ.get("COPY_TRADER_HUB_TOKEN", ""))
    parser.add_argument("--log-level", default=os.environ.get("COPY_TRADER_LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_server(args.host, args.port, Path(args.store), args.token)


if __name__ == "__main__":
    main()
