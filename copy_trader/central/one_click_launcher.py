"""
One-click desktop launcher for the central signal hub and MT5 client agent.

This GUI is deliberately small: it wraps the already-tested service modules so
operators do not need to type command-line commands.
"""
from __future__ import annotations

import json
import logging
import queue
import secrets
import socket
import sys
import threading
import time
import urllib.error
import webbrowser
from pathlib import Path
from typing import Dict, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from copy_trader.config import DATA_DIR, load_config

logger = logging.getLogger(__name__)


def _infer_role(default_role: Optional[str] = None) -> str:
    if default_role in {"central", "client"}:
        return default_role
    if "--role" in sys.argv:
        try:
            value = sys.argv[sys.argv.index("--role") + 1].strip().lower()
            if value in {"central", "client"}:
                return value
        except Exception:
            pass
    exe_name = Path(sys.argv[0]).stem.lower()
    if any(token in exe_name for token in ("central", "signal", "hub", "訊號")):
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


class OneClickLauncher:
    def __init__(self, role: str):
        self.role = role
        self.title = "黃金訊號中心" if role == "central" else "黃金跟單會員端"
        self.settings_path = DATA_DIR / f"{role}_one_click_settings.json"
        self.settings = self._load_settings()

        self.root = tk.Tk()
        self.root.title(self.title)
        self.root.geometry("760x560")
        self.root.minsize(680, 500)

        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.httpd = None
        self.client_agent = None
        self.log_queue: "queue.Queue[str]" = queue.Queue()

        self._install_logging()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(150, self._drain_logs)

        if self.auto_start_var.get():
            self.root.after(600, self.start)

    # ---------- settings ----------

    def _defaults(self) -> Dict:
        if self.role == "central":
            return {
                "host": "0.0.0.0",
                "port": "8765",
                "token": secrets.token_urlsafe(24),
                "copy_mode": "all",
                "interval": "1.0",
                "auto_start": False,
            }
        return {
            "hub_url": "http://中央電腦IP:8765",
            "token": "",
            "mt5_files_dir": "",
            "interval": "1.0",
            "auto_start": False,
        }

    def _load_settings(self) -> Dict:
        defaults = self._defaults()
        try:
            if self.settings_path.exists():
                with self.settings_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    defaults.update(data)
        except Exception:
            pass
        return defaults

    def _save_settings(self) -> None:
        data = self._collect_settings()
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        with self.settings_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.settings = data
        logger.info("設定已儲存：%s", self.settings_path)

    def _collect_settings(self) -> Dict:
        if self.role == "central":
            return {
                "host": self.host_var.get().strip() or "0.0.0.0",
                "port": self.port_var.get().strip() or "8765",
                "token": self.token_var.get().strip(),
                "copy_mode": self.copy_mode_var.get().strip() or "all",
                "interval": self.interval_var.get().strip() or "1.0",
                "auto_start": bool(self.auto_start_var.get()),
            }
        return {
            "hub_url": self.hub_url_var.get().strip(),
            "token": self.token_var.get().strip(),
            "mt5_files_dir": self.mt5_dir_var.get().strip(),
            "interval": self.interval_var.get().strip() or "1.0",
            "auto_start": bool(self.auto_start_var.get()),
        }

    # ---------- UI ----------

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X)
        ttk.Label(header, text=self.title, font=("", 18, "bold")).pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="尚未啟動")
        ttk.Label(header, textvariable=self.status_var).pack(side=tk.RIGHT)

        form = ttk.LabelFrame(outer, text="設定", padding=12)
        form.pack(fill=tk.X, pady=(14, 10))

        self.token_var = tk.StringVar(value=str(self.settings.get("token", "")))
        self.interval_var = tk.StringVar(value=str(self.settings.get("interval", "1.0")))
        self.auto_start_var = tk.BooleanVar(value=bool(self.settings.get("auto_start", False)))

        if self.role == "central":
            self._build_central_form(form)
        else:
            self._build_client_form(form)

        button_row = ttk.Frame(outer)
        button_row.pack(fill=tk.X, pady=(0, 10))
        self.start_button = ttk.Button(button_row, text="開始", command=self.start)
        self.start_button.pack(side=tk.LEFT)
        self.stop_button = ttk.Button(button_row, text="停止", command=self.stop, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_row, text="儲存設定", command=self._save_settings).pack(side=tk.LEFT, padx=(8, 0))
        if self.role == "central":
            ttk.Button(button_row, text="開啟 Hub 頁面", command=self.open_dashboard).pack(side=tk.LEFT, padx=(8, 0))
        else:
            ttk.Button(button_row, text="測試 Hub", command=self.test_hub).pack(side=tk.LEFT, padx=(8, 0))

        log_frame = ttk.LabelFrame(outer, text="狀態紀錄", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(log_frame, height=14, wrap=tk.WORD, state=tk.DISABLED)
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_central_form(self, form: ttk.LabelFrame) -> None:
        self.host_var = tk.StringVar(value=str(self.settings.get("host", "0.0.0.0")))
        self.port_var = tk.StringVar(value=str(self.settings.get("port", "8765")))
        self.copy_mode_var = tk.StringVar(value=str(self.settings.get("copy_mode", "all")))

        self._row(form, 0, "Hub 監聽 IP", ttk.Entry(form, textvariable=self.host_var))
        self._row(form, 1, "Hub Port", ttk.Entry(form, textvariable=self.port_var))
        token_row = ttk.Frame(form)
        ttk.Entry(token_row, textvariable=self.token_var, show="*").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(token_row, text="產生", command=self._generate_token).pack(side=tk.LEFT, padx=(6, 0))
        self._row(form, 2, "Hub 密碼", token_row)
        self._row(form, 3, "複製模式", ttk.Combobox(form, textvariable=self.copy_mode_var, values=["all", "tail"], state="readonly"))
        self._row(form, 4, "輪詢秒數", ttk.Entry(form, textvariable=self.interval_var))
        ttk.Checkbutton(form, text="開啟程式後自動開始", variable=self.auto_start_var).grid(row=5, column=1, sticky=tk.W, pady=(8, 0))

    def _build_client_form(self, form: ttk.LabelFrame) -> None:
        self.hub_url_var = tk.StringVar(value=str(self.settings.get("hub_url", "http://中央電腦IP:8765")))
        self.mt5_dir_var = tk.StringVar(value=str(self.settings.get("mt5_files_dir", "")))

        self._row(form, 0, "中央 Hub URL", ttk.Entry(form, textvariable=self.hub_url_var))
        self._row(form, 1, "Hub 密碼", ttk.Entry(form, textvariable=self.token_var, show="*"))
        mt5_row = ttk.Frame(form)
        ttk.Entry(mt5_row, textvariable=self.mt5_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(mt5_row, text="選擇", command=self._browse_mt5_dir).pack(side=tk.LEFT, padx=(6, 0))
        self._row(form, 2, "MT5 Files 路徑", mt5_row)
        self._row(form, 3, "輪詢秒數", ttk.Entry(form, textvariable=self.interval_var))
        ttk.Checkbutton(form, text="開啟程式後自動開始", variable=self.auto_start_var).grid(row=4, column=1, sticky=tk.W, pady=(8, 0))

    def _row(self, parent: ttk.LabelFrame, row: int, label: str, widget: tk.Widget) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 12), pady=5)
        widget.grid(row=row, column=1, sticky=tk.EW, pady=5)
        parent.columnconfigure(1, weight=1)

    def _generate_token(self) -> None:
        self.token_var.set(secrets.token_urlsafe(24))

    def _browse_mt5_dir(self) -> None:
        path = filedialog.askdirectory(title="選擇 MT5 MQL5/Files 資料夾")
        if path:
            self.mt5_dir_var.set(path)

    # ---------- service lifecycle ----------

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self._save_settings()
        self.stop_event.clear()
        target = self._run_central if self.role == "central" else self._run_client
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()
        self.status_var.set("啟動中")
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)

    def stop(self) -> None:
        self.stop_event.set()
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
            except Exception:
                pass
        self.status_var.set("停止中")

    def _service_stopped(self) -> None:
        self.status_var.set("已停止")
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)

    def _run_central(self) -> None:
        try:
            from copy_trader.central.hub_server import HubHTTPServer, HubRequestHandler, SignalStore
            from copy_trader.central.signal_collector import CentralSignalCollector, HubPublisher

            settings = self._collect_settings()
            host = settings["host"]
            port = int(settings["port"])
            token = settings["token"]
            interval = max(0.2, float(settings["interval"]))
            copy_mode = settings["copy_mode"]
            store = DATA_DIR / "central_hub_signals.jsonl"

            self.httpd = HubHTTPServer((host, port), HubRequestHandler, SignalStore(store), token)
            server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            server_thread.start()

            local_url = f"http://127.0.0.1:{port}"
            lan_url = f"http://{_lan_ip()}:{port}"
            logger.info("Hub 已啟動：%s", lan_url)
            logger.info("會員端請填：%s", lan_url)
            logger.info("管理頁面：%s/?token=%s", local_url, token)

            collector = CentralSignalCollector(
                load_config(),
                HubPublisher(local_url, token),
                copy_mode=copy_mode,
            )
            self.root.after(0, lambda: self.status_var.set("運行中"))

            while not self.stop_event.is_set():
                try:
                    published = collector.run_cycle()
                    if published:
                        logger.info("本輪發布 %s 筆訊號", published)
                except Exception as e:
                    logger.exception("中央擷取錯誤：%s", e)
                self.stop_event.wait(interval)
        except Exception as e:
            logger.exception("中央訊號中心啟動失敗：%s", e)
            self.root.after(0, lambda: messagebox.showerror("啟動失敗", str(e)))
        finally:
            if self.httpd is not None:
                try:
                    self.httpd.server_close()
                except Exception:
                    pass
                self.httpd = None
            self.root.after(0, self._service_stopped)

    def _run_client(self) -> None:
        try:
            from copy_trader.central.mt5_client_agent import HubClient, MT5ClientAgent

            settings = self._collect_settings()
            hub_url = settings["hub_url"].rstrip("/")
            token = settings["token"]
            mt5_dir = settings["mt5_files_dir"]
            interval = max(0.5, float(settings["interval"]))

            self.client_agent = MT5ClientAgent(
                HubClient(hub_url, token),
                DATA_DIR / "central_client_state.json",
                mt5_files_dir=mt5_dir,
                replay=False,
            )
            self.client_agent.trade_manager.start()
            logger.info("會員端已啟動，Hub=%s，last_seq=%s", hub_url, self.client_agent.last_seq)
            self.root.after(0, lambda: self.status_var.set("運行中"))

            while not self.stop_event.is_set():
                try:
                    count = self.client_agent.run_cycle()
                    if count:
                        logger.info("本輪送出 %s 筆 MT5 指令", count)
                except (urllib.error.URLError, TimeoutError) as e:
                    logger.warning("Hub 連線失敗：%s", e)
                except Exception as e:
                    logger.exception("會員端執行錯誤：%s", e)
                self.stop_event.wait(interval)
        except Exception as e:
            logger.exception("會員端啟動失敗：%s", e)
            self.root.after(0, lambda: messagebox.showerror("啟動失敗", str(e)))
        finally:
            if self.client_agent is not None:
                try:
                    self.client_agent.trade_manager.stop()
                except Exception:
                    pass
                self.client_agent = None
            self.root.after(0, self._service_stopped)

    # ---------- actions ----------

    def open_dashboard(self) -> None:
        port = self.port_var.get().strip() or "8765"
        token = self.token_var.get().strip()
        webbrowser.open(f"http://127.0.0.1:{port}/?token={token}")

    def test_hub(self) -> None:
        try:
            from copy_trader.central.mt5_client_agent import HubClient

            data = HubClient(self.hub_url_var.get().strip(), self.token_var.get().strip()).health()
            messagebox.showinfo("Hub 測試", f"連線成功\nlatest_seq={data.get('latest_seq')}")
        except Exception as e:
            messagebox.showerror("Hub 測試失敗", str(e))

    # ---------- logging ----------

    def _install_logging(self) -> None:
        handler = QueueLogHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s", "%H:%M:%S"))
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)

    def _drain_logs(self) -> None:
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, line + "\n")
                self.log_text.see(tk.END)
                self.log_text.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(150, self._drain_logs)

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("確認", "服務仍在運行，要停止並關閉嗎？"):
                return
            self.stop()
            self.root.after(500, self.root.destroy)
            return
        self.root.destroy()

    def run(self) -> None:
        logger.info("%s 已開啟", self.title)
        self.root.mainloop()


def main(default_role: Optional[str] = None) -> None:
    role = _infer_role(default_role)
    app = OneClickLauncher(role)
    app.run()


if __name__ == "__main__":
    main()
