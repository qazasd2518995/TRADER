"""
Browser-based one-click launcher.

The app starts a localhost control panel in the user's browser. This avoids
requiring Tk/PySide on member machines while still giving non-technical users a
Start/Stop interface.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from copy_trader.config import DATA_DIR, load_config

logger = logging.getLogger(__name__)

_CLOUDFLARED_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "啟用"}


def _infer_role(default_role: Optional[str] = None) -> str:
    if default_role in {"central", "client"}:
        return default_role
    if "--role" in sys.argv:
        try:
            role = sys.argv[sys.argv.index("--role") + 1].strip().lower()
            if role in {"central", "client"}:
                return role
        except Exception:
            pass
    name = Path(sys.argv[0]).stem.lower()
    if any(token in name for token in ("central", "signal", "hub", "訊號")):
        return "central"
    return "client"


def _lan_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.2)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


class QueueLogHandler(logging.Handler):
    def __init__(self, log_queue: "queue.Queue[str]"):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.log_queue.put(self.format(record))
        except Exception:
            pass


class LauncherState:
    def __init__(self, role: str):
        self.role = role
        self.title = "黃金訊號中心" if role == "central" else "黃金跟單會員端"
        self.settings_path = DATA_DIR / f"{role}_web_launcher_settings.json"
        self.settings = self._load_settings()
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.logs = []
        self.lock = threading.Lock()
        self.worker: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.httpd = None
        self.client_agent = None
        self.cloudflared_process: Optional[subprocess.Popen] = None
        self.cloudflare_url = ""
        self.status = "尚未啟動"
        self.service_started_at: Optional[float] = None
        self.should_exit = False
        self.control_server: Optional[ThreadingHTTPServer] = None

    def defaults(self) -> Dict[str, Any]:
        if self.role == "central":
            return {
                "host": "0.0.0.0",
                "port": "8765",
                "token": secrets.token_urlsafe(24),
                "copy_mode": "all",
                "interval": "1.0",
                "cloudflare_tunnel": "true",
                "cloudflared_path": "",
                "auto_start": "false",
            }
        return {
            "hub_url": "http://中央電腦IP:8765",
            "token": "",
            "mt5_files_dir": "",
            "interval": "1.0",
            "auto_start": "false",
        }

    def _load_settings(self) -> Dict[str, Any]:
        data = self.defaults()
        try:
            if self.settings_path.exists():
                with self.settings_path.open("r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data.update(loaded)
        except Exception:
            pass
        return data

    def save_settings(self, data: Dict[str, Any]) -> Dict[str, Any]:
        merged = self.defaults()
        merged.update({k: v for k, v in data.items() if k in merged})
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        with self.settings_path.open("w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        self.settings = merged
        logger.info("設定已儲存：%s", self.settings_path)
        return merged

    def is_running(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    def start_service(self) -> None:
        if self.is_running():
            return
        self.stop_event.clear()
        target = self._run_central if self.role == "central" else self._run_client
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()
        self.status = "啟動中"

    def stop_service(self) -> None:
        self.stop_event.set()
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
            except Exception:
                pass
        self._stop_cloudflare_tunnel()
        self.status = "停止中"

    def _cloudflared_candidates(self) -> list[Path]:
        exe_name = "cloudflared.exe" if sys.platform == "win32" else "cloudflared"
        configured = str(self.settings.get("cloudflared_path") or "").strip().strip('"')
        candidates = []
        if configured:
            candidates.append(Path(configured))

        candidates.append(DATA_DIR / exe_name)
        candidates.append(Path(sys.executable).with_name(exe_name))
        candidates.append(Path.cwd() / exe_name)

        found = shutil.which("cloudflared") or shutil.which("cloudflared.exe")
        if found:
            candidates.append(Path(found))

        return candidates

    def _find_cloudflared(self) -> Optional[Path]:
        for path in self._cloudflared_candidates():
            try:
                if path.is_file():
                    return path
            except OSError:
                continue
        return None

    def _start_cloudflare_tunnel(self, port: int) -> None:
        if not _truthy(self.settings.get("cloudflare_tunnel")):
            self.cloudflare_url = ""
            return

        exe = self._find_cloudflared()
        if exe is None:
            logger.warning("找不到 cloudflared，Cloudflare Tunnel 未啟動")
            logger.warning("Windows 請先執行同資料夾的 install_cloudflared_windows.bat，或安裝 Cloudflare cloudflared")
            return

        target = f"http://127.0.0.1:{port}"
        cmd = [str(exe), "tunnel", "--url", target]
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        self.cloudflare_url = ""
        logger.info("Cloudflare Quick Tunnel 啟動中：%s", target)
        self.cloudflared_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        threading.Thread(target=self._read_cloudflared_logs, daemon=True).start()

    def _read_cloudflared_logs(self) -> None:
        process = self.cloudflared_process
        if process is None or process.stdout is None:
            return

        try:
            for line in process.stdout:
                text = line.strip()
                if not text:
                    continue
                match = _CLOUDFLARED_URL_RE.search(text)
                if match:
                    self.cloudflare_url = match.group(0)
                    logger.info("Cloudflare 公開 Hub URL：%s", self.cloudflare_url)
                    logger.info("會員端請填 Hub URL：%s", self.cloudflare_url)
                elif "error" in text.lower() or "failed" in text.lower():
                    logger.warning("cloudflared: %s", text)
        except Exception as exc:
            logger.warning("讀取 cloudflared 紀錄失敗：%s", exc)
        finally:
            if process.poll() is not None and not self.stop_event.is_set():
                logger.warning("Cloudflare Tunnel 已停止，exit_code=%s", process.returncode)

    def _stop_cloudflare_tunnel(self) -> None:
        process = self.cloudflared_process
        self.cloudflared_process = None
        self.cloudflare_url = ""
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _run_central(self) -> None:
        try:
            from copy_trader.central.hub_server import HubHTTPServer, HubRequestHandler, SignalStore
            from copy_trader.central.signal_collector import CentralSignalCollector, HubPublisher

            host = str(self.settings.get("host") or "0.0.0.0")
            port = int(self.settings.get("port") or 8765)
            token = str(self.settings.get("token") or "")
            interval = max(0.2, float(self.settings.get("interval") or 1.0))
            copy_mode = str(self.settings.get("copy_mode") or "all")

            self.httpd = HubHTTPServer((host, port), HubRequestHandler, SignalStore(DATA_DIR / "central_hub_signals.jsonl"), token)
            server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            server_thread.start()

            local_url = f"http://127.0.0.1:{port}"
            lan_url = f"http://{_lan_ip()}:{port}"
            loopback_only = host in ("127.0.0.1", "localhost", "::1")
            if loopback_only:
                logger.info("Hub 已啟動（僅本機 %s）", local_url)
                if not _truthy(self.settings.get("cloudflare_tunnel")):
                    logger.warning("Hub 目前只監聽本機，區網會員無法連線；請把「Hub 監聽 IP」改成 0.0.0.0，或勾選 Cloudflare Tunnel")
            else:
                logger.info("Hub 已啟動：%s", lan_url)
                logger.info("區網會員端請填 Hub URL：%s（若連不上請檢查 Windows 防火牆是否放行）", lan_url)
            logger.info("Hub 管理頁面：%s/?token=%s", local_url, token)
            self._start_cloudflare_tunnel(port)

            collector = CentralSignalCollector(load_config(), HubPublisher(local_url, token), copy_mode=copy_mode)
            self.status = "運行中"
            self.service_started_at = time.time()

            while not self.stop_event.is_set():
                try:
                    published = collector.run_cycle()
                    if published:
                        logger.info("本輪發布 %s 筆訊號", published)
                except Exception as exc:
                    logger.exception("中央擷取錯誤：%s", exc)
                self.stop_event.wait(interval)
        except Exception as exc:
            logger.exception("中央訊號中心啟動失敗：%s", exc)
            self.status = "啟動失敗"
        finally:
            if self.httpd is not None:
                try:
                    self.httpd.server_close()
                except Exception:
                    pass
                self.httpd = None
            self._stop_cloudflare_tunnel()
            self.status = "已停止"
            self.service_started_at = None

    def _run_client(self) -> None:
        try:
            from copy_trader.central.mt5_client_agent import HubClient, MT5ClientAgent

            hub_url = str(self.settings.get("hub_url") or "").rstrip("/")
            token = str(self.settings.get("token") or "")
            mt5_dir = str(self.settings.get("mt5_files_dir") or "")
            interval = max(0.5, float(self.settings.get("interval") or 1.0))

            # Hub 可能晚於會員端開機；連不上就每 10 秒重試，不直接放棄。
            while not self.stop_event.is_set():
                try:
                    self.client_agent = MT5ClientAgent(
                        HubClient(hub_url, token),
                        DATA_DIR / "central_client_state.json",
                        mt5_files_dir=mt5_dir,
                        replay=False,
                    )
                    break
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    self.status = "等待 Hub 連線"
                    logger.warning("無法連線 Hub（%s），10 秒後重試：%s", hub_url, exc)
                    self.stop_event.wait(10)
            if self.client_agent is None:
                return

            self.client_agent.trade_manager.start()
            logger.info("會員端已啟動，Hub=%s，last_seq=%s", hub_url, self.client_agent.last_seq)
            self.status = "運行中"
            self.service_started_at = time.time()

            while not self.stop_event.is_set():
                try:
                    count = self.client_agent.run_cycle()
                    if count:
                        logger.info("本輪送出 %s 筆 MT5 指令", count)
                except urllib.error.HTTPError as exc:
                    if exc.code == 401:
                        logger.error("Hub 密碼錯誤（401），請檢查「Hub 密碼」設定")
                    else:
                        logger.warning("Hub 連線失敗：%s", exc)
                except (urllib.error.URLError, TimeoutError) as exc:
                    logger.warning("Hub 連線失敗：%s", exc)
                except Exception as exc:
                    logger.exception("會員端執行錯誤：%s", exc)
                self.stop_event.wait(interval)
        except Exception as exc:
            logger.exception("會員端啟動失敗：%s", exc)
            self.status = "啟動失敗"
        finally:
            if self.client_agent is not None:
                try:
                    self.client_agent.trade_manager.stop()
                except Exception:
                    pass
                self.client_agent = None
            self.status = "已停止"
            self.service_started_at = None

    def drain_logs(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.logs.append(line)
            if len(self.logs) > 500:
                self.logs = self.logs[-500:]

    def snapshot(self) -> Dict[str, Any]:
        self.drain_logs()
        return {
            "role": self.role,
            "title": self.title,
            "settings": self.settings,
            "status": self.status,
            "running": self.is_running(),
            "logs": self.logs[-200:],
            "lan_ip": _lan_ip(),
            "cloudflare_url": self.cloudflare_url,
            "uptime_seconds": int(time.time() - self.service_started_at) if self.service_started_at else 0,
        }


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def make_handler(state: LauncherState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            logger.debug(fmt, *args)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                _html_response(self, _page_html(state))
                return
            if parsed.path == "/api/status":
                _json_response(self, 200, {"ok": True, **state.snapshot()})
                return
            _json_response(self, 404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            try:
                if parsed.path == "/api/settings":
                    settings = state.save_settings(_read_json(self))
                    _json_response(self, 200, {"ok": True, "settings": settings})
                    return
                if parsed.path == "/api/start":
                    data = _read_json(self)
                    if data:
                        state.save_settings(data)
                    state.start_service()
                    _json_response(self, 200, {"ok": True})
                    return
                if parsed.path == "/api/stop":
                    state.stop_service()
                    _json_response(self, 200, {"ok": True})
                    return
                if parsed.path == "/api/test-hub":
                    from copy_trader.central.mt5_client_agent import HubClient

                    settings = state.save_settings(_read_json(self))
                    health = HubClient(str(settings.get("hub_url") or ""), str(settings.get("token") or "")).health()
                    _json_response(self, 200, {"ok": True, "health": health})
                    return
                if parsed.path == "/api/quit":
                    state.stop_service()
                    state.should_exit = True
                    _json_response(self, 200, {"ok": True})
                    threading.Thread(target=state.control_server.shutdown, daemon=True).start()
                    return
            except Exception as exc:
                logger.exception("request failed: %s", exc)
                _json_response(self, 500, {"ok": False, "error": str(exc)})
                return
            _json_response(self, 404, {"ok": False, "error": "not_found"})

    return Handler


def _page_html(state: LauncherState) -> str:
    is_central = state.role == "central"
    fields = """
      <label>Hub 監聽 IP<input id="host" /></label>
      <label>Hub Port<input id="port" /></label>
      <label>Hub 密碼<input id="token" type="password" /></label>
      <label>複製模式<select id="copy_mode"><option value="all">全選複製</option><option value="tail">底部幾屏</option></select></label>
      <label>輪詢秒數<input id="interval" /></label>
      <label>Cloudflare Tunnel<input id="cloudflare_tunnel" type="checkbox" /></label>
      <label>cloudflared 路徑<input id="cloudflared_path" placeholder="可留空自動搜尋" /></label>
      <label>開啟程式後自動開始<input id="auto_start" type="checkbox" /></label>
    """ if is_central else """
      <label>中央 Hub URL<input id="hub_url" placeholder="http://中央電腦IP:8765" /></label>
      <label>Hub 密碼<input id="token" type="password" /></label>
      <label>MT5 Files 路徑<input id="mt5_files_dir" placeholder="可留空自動偵測" /></label>
      <label>輪詢秒數<input id="interval" /></label>
      <label>開啟程式後自動開始<input id="auto_start" type="checkbox" /></label>
    """
    extra_button = "<button id=\"openHub\">開啟 Hub 頁面</button>" if is_central else "<button id=\"testHub\">測試 Hub</button>"
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{state.title}</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f5f7f8; color: #17202a; }}
    header {{ padding: 18px 24px; background: #fff; border-bottom: 1px solid #dfe4e8; display: flex; justify-content: space-between; align-items: center; }}
    h1 {{ margin: 0; font-size: 21px; }}
    main {{ max-width: 920px; margin: 0 auto; padding: 22px; }}
    section {{ background: #fff; border: 1px solid #dfe4e8; border-radius: 8px; padding: 18px; margin-bottom: 14px; }}
    label {{ display: grid; grid-template-columns: 160px 1fr; align-items: center; gap: 12px; margin: 10px 0; color: #52616f; }}
    input, select {{ font: inherit; padding: 9px 10px; border: 1px solid #cad2d8; border-radius: 6px; }}
    button {{ font: inherit; padding: 9px 14px; border: 1px solid #9aa7b2; border-radius: 6px; background: #fff; cursor: pointer; margin-right: 8px; }}
    button.primary {{ background: #1450a3; color: #fff; border-color: #1450a3; }}
    button.danger {{ color: #a12a2a; border-color: #d7a4a4; }}
    #logs {{ height: 240px; overflow: auto; white-space: pre-wrap; background: #111820; color: #d7e3ee; padding: 12px; border-radius: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }}
    .muted {{ color: #6b7785; }}
  </style>
</head>
<body>
  <header><h1>{state.title}</h1><strong id="status">載入中</strong></header>
  <main>
    <section>
      <h2>設定</h2>
      {fields}
      <p class="muted" id="hint"></p>
    </section>
    <section>
      <button class="primary" id="start">開始</button>
      <button id="stop">停止</button>
      {extra_button}
      <button class="danger" id="quit">關閉程式</button>
    </section>
    <section><h2>狀態紀錄</h2><div id="logs"></div></section>
  </main>
  <script>
    const role = {json.dumps(state.role)};
    let snapshot = null;
    let didFill = false;
    function ids() {{ return role === "central" ? ["host","port","token","copy_mode","interval","cloudflare_tunnel","cloudflared_path","auto_start"] : ["hub_url","token","mt5_files_dir","interval","auto_start"]; }}
    function collect() {{
      const out = {{}};
      for (const id of ids()) {{
        const el = document.getElementById(id);
        out[id] = el.type === "checkbox" ? (el.checked ? "true" : "false") : el.value;
      }}
      return out;
    }}
    function fill(settings) {{
      for (const id of ids()) if (document.getElementById(id)) {{
        const el = document.getElementById(id);
        if (el.type === "checkbox") el.checked = ["true", "1", "yes", "on"].includes(String(settings[id] || "").toLowerCase());
        else el.value = settings[id] || "";
      }}
    }}
    async function post(path, data={{}}) {{
      const res = await fetch(path, {{ method: "POST", headers: {{ "Content-Type": "application/json" }}, body: JSON.stringify(data) }});
      const json = await res.json();
      if (!json.ok) throw new Error(json.error || "request failed");
      return json;
    }}
    async function refresh() {{
      const res = await fetch("/api/status");
      snapshot = await res.json();
      if (!snapshot.ok) return;
      if (!didFill) {{
        fill(snapshot.settings);
        didFill = true;
      }}
      document.getElementById("status").textContent = snapshot.status + (snapshot.running ? ` (${{snapshot.uptime_seconds}}s)` : "");
      document.getElementById("logs").textContent = (snapshot.logs || []).join("\\n");
      document.getElementById("logs").scrollTop = document.getElementById("logs").scrollHeight;
      if (role === "central") {{
        const port = snapshot.settings.port || "8765";
        const host = String(snapshot.settings.host || "").trim();
        const loopbackOnly = host === "127.0.0.1" || host === "localhost" || host === "::1";
        if (snapshot.cloudflare_url) {{
          document.getElementById("hint").textContent = `會員端 Hub URL：${{snapshot.cloudflare_url}}`;
        }} else if (["true", "1", "yes", "on"].includes(String(snapshot.settings.cloudflare_tunnel || "").toLowerCase())) {{
          document.getElementById("hint").textContent = "Cloudflare Tunnel 啟動中；公開 Hub URL 會出現在狀態紀錄。";
        }} else if (loopbackOnly) {{
          document.getElementById("hint").textContent = "Hub 只監聽本機，會員端無法連線 — 請把「Hub 監聽 IP」改成 0.0.0.0，或勾選 Cloudflare Tunnel。";
        }} else {{
          document.getElementById("hint").textContent = `會員端 Hub URL 可填：http://${{snapshot.lan_ip}}:${{port}}（限同一區網）`;
        }}
      }}
    }}
    document.getElementById("start").onclick = () => post("/api/start", collect()).then(refresh).catch(e => alert(e.message));
    document.getElementById("stop").onclick = () => post("/api/stop").then(refresh).catch(e => alert(e.message));
    document.getElementById("quit").onclick = () => post("/api/quit").then(() => document.body.innerHTML = "<main><section><h1>程式已關閉</h1></section></main>").catch(e => alert(e.message));
    if (document.getElementById("testHub")) document.getElementById("testHub").onclick = () => post("/api/test-hub", collect()).then(j => alert(`連線成功 latest_seq=${{j.health.latest_seq}}`)).catch(e => alert(e.message));
    if (document.getElementById("openHub")) document.getElementById("openHub").onclick = () => {{
      const s = collect();
      window.open(`http://127.0.0.1:${{s.port || "8765"}}/?token=${{encodeURIComponent(s.token || "")}}`, "_blank");
    }};
    refresh();
    setInterval(refresh, 1000);
  </script>
</body>
</html>"""


def _install_logging(state: LauncherState) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    handler = QueueLogHandler(state.log_queue)
    handler.setFormatter(logging.Formatter("%H:%M:%S %(levelname)s: %(message)s"))
    logging.getLogger().addHandler(handler)


def _find_running_instance(role: str, port_file: Path) -> Optional[str]:
    """若同角色的程式已在執行，回傳其控制台 URL；否則回傳 None。"""
    try:
        port = int(port_file.read_text(encoding="utf-8").strip())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok") and data.get("role") == role:
            return f"http://127.0.0.1:{port}/"
    except Exception:
        pass
    return None


def main(default_role: Optional[str] = None) -> None:
    role = _infer_role(default_role)
    state = LauncherState(role)
    _install_logging(state)

    # 單一實例：重複開啟時直接打開既有控制台，避免兩個 agent 同時下單。
    port_file = DATA_DIR / f"{role}_web_launcher_port.txt"
    if port_file.exists():
        existing = _find_running_instance(role, port_file)
        if existing:
            logger.info("%s 已在執行，開啟既有控制台：%s", state.title, existing)
            webbrowser.open(existing)
            return

    logger.info("%s 已啟動", state.title)

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    state.control_server = server
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        port_file.parent.mkdir(parents=True, exist_ok=True)
        port_file.write_text(str(server.server_address[1]), encoding="utf-8")
    except Exception:
        pass
    webbrowser.open(url)
    logger.info("控制台：%s", url)

    if _truthy(state.settings.get("auto_start")):
        logger.info("已設定自動開始，啟動服務中…")
        state.start_service()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop_service()
        server.server_close()
        try:
            port_file.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
