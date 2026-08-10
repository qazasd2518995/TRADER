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

from copy_trader.config import DATA_DIR, _instance_name, load_config

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
        # 多開時把實例名稱掛進標題 — 兩個控制台長得一模一樣, 分頁上分不出來
        # 就很容易對著錯的那個改設定 (見 config._instance_name)。
        _inst = _instance_name()
        if _inst:
            self.title = f"{self.title}（{_inst}）"
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
                "hub_url": "",
                "host": "0.0.0.0",
                "port": "8765",
                "token": secrets.token_urlsafe(24),
                "interval": "1.0",
                "cloudflare_tunnel": "true",
                "cloudflared_path": "",
                "auto_start": "false",
            }
        return {
            "hub_url": "https://gold-signal-hub-tw.fly.dev",
            "token": "79yy4q8ldFRUqPZT",
            "mt5_files_dir": "",
            "interval": "1.0",
            "auto_start": "false",
            "default_lot_size": "0.01",
            "use_martingale": "true",
            "martingale_multiplier": "2.0",
            "martingale_max_level": "5",
            "martingale_lots": "",
            "partial_close_ratios": "0.5,0.3,0.2",
            "cancel_pending_after_seconds": "10800",
            "cancel_if_price_beyond_percent": "0",
            # 每個訊號來源各自的下單模式，存成 JSON 字串（設定檔全部是字串型別）
            "source_profiles": "{}",
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
        # 先鋪目前設定再蓋上這次送來的欄位：只送部分欄位時，其餘設定必須留著。
        # （原本直接蓋在 defaults() 上，少送一個欄位就會被悄悄重設成預設值。）
        merged = self.defaults()
        merged.update({k: v for k, v in self.settings.items() if k in merged})
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
            from copy_trader.central.mt5_client_agent import HubClient
            from copy_trader.central.signal_collector import CentralSignalCollector, HubPublisher

            token = str(self.settings.get("token") or "")
            interval = max(0.2, float(self.settings.get("interval") or 1.0))
            remote_hub = str(self.settings.get("hub_url") or "").strip().rstrip("/")

            if remote_hub:
                # 雲端 Hub 模式：不在本機開 Hub，直接把訊號發到雲端（例如 Fly.io）。
                # 會員端 Hub URL 也填同一個雲端網址。
                publish_url = remote_hub
                logger.info("雲端 Hub 模式，發布到：%s", remote_hub)
                try:
                    health = HubClient(remote_hub, token).health()
                    logger.info("雲端 Hub 連線成功：latest_seq=%s", health.get("latest_seq"))
                except urllib.error.HTTPError as exc:
                    if exc.code == 401:
                        logger.error("雲端 Hub 密碼錯誤（401），請檢查「Hub 密碼」設定")
                    else:
                        logger.warning("雲端 Hub 健康檢查失敗（%s），仍會嘗試發布", exc)
                except Exception as exc:
                    logger.warning("無法連線雲端 Hub（%s），仍會嘗試發布：%s", remote_hub, exc)
                logger.info("會員端 Hub URL 請填：%s", remote_hub)
            else:
                # 本機自架 Hub 模式（區網直連或 Cloudflare Tunnel）。
                host = str(self.settings.get("host") or "0.0.0.0")
                port = int(self.settings.get("port") or 8765)
                self.httpd = HubHTTPServer((host, port), HubRequestHandler, SignalStore(DATA_DIR / "central_hub_signals.jsonl"), token)
                server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
                server_thread.start()

                local_url = f"http://127.0.0.1:{port}"
                lan_url = f"http://{_lan_ip()}:{port}"
                publish_url = local_url
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

            collector = CentralSignalCollector(load_config(), HubPublisher(publish_url, token))
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

    def _apply_client_trade_settings(self) -> None:
        """把會員面板的手數 / 馬丁 / 分批設定套到 agent 的 TradeManager。"""
        tm = self.client_agent.trade_manager

        def _flt(key, fallback):
            try:
                v = str(self.settings.get(key) or "").strip()
                return float(v) if v else fallback
            except Exception:
                return fallback

        def _lots(key):
            raw = str(self.settings.get(key) or "").strip().replace("，", ",")
            out = []
            for part in raw.split(","):
                part = part.strip()
                if part:
                    try:
                        out.append(float(part))
                    except Exception:
                        pass
            return out

        tm.default_lot_size = _flt("default_lot_size", tm.default_lot_size)
        tm.use_martingale = _truthy(self.settings.get("use_martingale"))
        tm.martingale_multiplier = _flt("martingale_multiplier", tm.martingale_multiplier)
        lvl = str(self.settings.get("martingale_max_level") or "").strip()
        if lvl:
            try:
                tm.martingale_max_level = int(float(lvl))
            except Exception:
                pass
        ml = _lots("martingale_lots")
        if ml:
            tm.martingale_lots = ml
        pcr = _lots("partial_close_ratios")
        if pcr:
            tm.partial_close_ratios = pcr

        # 每個訊號來源各自的下單模式（均注 / 馬丁）。壞掉的 JSON 就當沒設定，
        # 讓全域設定接手，不要因為一個欄位打錯就讓整個跟單起不來。
        profiles = {}
        raw_profiles = str(self.settings.get("source_profiles") or "").strip()
        if raw_profiles:
            try:
                parsed = json.loads(raw_profiles)
                if isinstance(parsed, dict):
                    profiles = {str(k): v for k, v in parsed.items() if isinstance(v, dict)}
                else:
                    logger.warning("source_profiles 不是物件，已忽略")
            except json.JSONDecodeError as exc:
                logger.warning("source_profiles JSON 解析失敗，已忽略：%s", exc)
        tm.source_profiles = profiles
        if profiles:
            # 混用均注/馬丁時，層級一定要各群分開算，否則會互相污染
            tm.martingale_per_source = True
            for name, p in profiles.items():
                mode = "均注" if str(p.get("mode", "")).lower() == "flat" else "馬丁"
                on = "跟單" if p.get("enabled", True) else "已停用"
                logger.info("來源設定：%s → %s / %s / 基礎手數 %s", name, on, mode, p.get("base_lot", "(全域)"))

        # 刪單規則讀的是 agent 的 config（submit_signal 送單當下才取值），不是 TradeManager。
        # 這兩個值會寫進每一筆新掛單；已經送出的舊單沿用當初的設定。
        cfg = self.client_agent.config
        cfg.cancel_pending_after_seconds = int(_flt("cancel_pending_after_seconds", cfg.cancel_pending_after_seconds))
        cfg.cancel_if_price_beyond_percent = _flt("cancel_if_price_beyond_percent", cfg.cancel_if_price_beyond_percent)
        timeout_text = (
            f"{cfg.cancel_pending_after_seconds} 秒（{cfg.cancel_pending_after_seconds / 3600:.1f} 小時）"
            if cfg.cancel_pending_after_seconds else "關閉"
        )
        beyond_text = (
            f"{cfg.cancel_if_price_beyond_percent}%" if cfg.cancel_if_price_beyond_percent else "關閉"
        )
        logger.info("刪單規則：逾時未進場 %s｜價格偏離 %s", timeout_text, beyond_text)

        # martingale_per_source 只從 config.json 或每群設定推導，面板沒有這個欄位；
        # 跟多個報單群時這個值決定虧損會不會互相放大手數，所以印出來。
        logger.info(
            "套用會員設定：基礎手數=%s 馬丁=%s 倍數=%s 最大層數=%s 每層手數=%s 分批=%s 馬丁計算=%s",
            tm.default_lot_size, tm.use_martingale, tm.martingale_multiplier,
            tm.martingale_max_level, tm.martingale_lots, tm.partial_close_ratios,
            "各群獨立" if tm.martingale_per_source else "全域共用",
        )

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

            self._apply_client_trade_settings()
            # 重啟後把 MT5 上還活著的單接回追蹤，否則會變成沒人管的孤兒單：
            # 不會逾時自動刪，成交後的輸贏也不會計入馬丁層級。
            cfg = self.client_agent.config
            self.client_agent.trade_manager.adopt_open_orders(
                cancel_after_seconds=cfg.cancel_pending_after_seconds,
                cancel_if_price_beyond=cfg.cancel_if_price_beyond_percent,
            )
            self.client_agent.trade_manager.start()
            logger.info("會員端已啟動，Hub=%s，last_seq=%s", hub_url, self.client_agent.last_seq)
            self.status = "運行中"
            self.service_started_at = time.time()

            consecutive_fail = 0  # 連線連續失敗次數 (用來壓 log 洗版)
            while not self.stop_event.is_set():
                try:
                    count = self.client_agent.run_cycle()
                    if consecutive_fail > 0:
                        logger.info("Hub 連線已恢復（中斷 %d 次後）", consecutive_fail)
                        consecutive_fail = 0
                    if count:
                        logger.info("本輪送出 %s 筆 MT5 指令", count)
                except urllib.error.HTTPError as exc:
                    if exc.code == 401:
                        logger.error("Hub 密碼錯誤（401），請檢查「Hub 密碼」設定")
                    else:
                        logger.warning("Hub 連線失敗：%s", exc)
                except (urllib.error.URLError, TimeoutError) as exc:
                    consecutive_fail += 1
                    # 只在第一次 + 每隔約 30 次記一次, 避免洗版
                    if consecutive_fail == 1 or consecutive_fail % 30 == 0:
                        logger.warning("Hub 連線卡頓（連續 %d 次，會自動重連）：%s", consecutive_fail, exc)
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
            if parsed.path == "/api/stats":
                # 績效統計純粹是讀檔彙整，MT5 沒開就回空資料，不該讓控制台整頁掛掉
                try:
                    from copy_trader.central.stats import build_stats

                    agent = state.client_agent
                    stats = build_stats(
                        state.settings,
                        trade_manager=agent.trade_manager if agent is not None else None,
                    )
                    _json_response(self, 200, {"ok": True, "stats": stats})
                except Exception as exc:
                    logger.exception("stats failed: %s", exc)
                    _json_response(self, 500, {"ok": False, "error": str(exc)})
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
    """整頁前端在 copy_trader.central.webui，這裡只負責把 state 交出去。"""
    from copy_trader.central.webui import render

    return render(state)


def _install_logging(state: LauncherState) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    handler = QueueLogHandler(state.log_queue)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S"))
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
