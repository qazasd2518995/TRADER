"""
Browser-based one-click launcher.

The app starts a localhost control panel in the user's browser. This avoids
requiring Tk/PySide on member machines while still giving non-technical users a
Start/Stop interface.
"""
from __future__ import annotations

import json
import logging
import os
import platform
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
from typing import Any, Dict, List, Optional

from copy_trader.config import DATA_DIR, _instance_name
from copy_trader.central.membership import MIN_PASSWORD_LENGTH, ULTRA_HIGH_FREQ

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

        # ── 會員登入 ────────────────────────────────────────────────────
        # session 另外存一個檔, 不跟 settings 混在一起: settings 是使用者會
        # 匯出/分享的東西 (問「我的設定長怎樣」時常常整個貼出來), session
        # token 等同於密碼, 不能跟著一起外流。
        self.auth_path = DATA_DIR / "member_session.json"
        self.auth: Optional[Dict[str, Any]] = None      # 已登入的會員資料
        self.auth_error = ""                            # 給前端顯示的最後一次失敗原因
        self.auth_checked_at = 0.0
        if self.role == "client":
            self._load_session()

    def defaults(self) -> Dict[str, Any]:
        if self.role == "central":
            # Delayed import keeps the member package independent from the
            # central-only encrypted LINE database stack.
            from copy_trader.line_db.factory import DEFAULT_LINE_CHATS

            return {
                "line_database_path": "",
                "line_keychain_service": "line-db-research",
                "line_chats": json.dumps(DEFAULT_LINE_CHATS, ensure_ascii=False, indent=2),
                "hub_url": "",
                "host": "0.0.0.0",
                "port": "8765",
                "token": secrets.token_urlsafe(24),
                "interval": "1.0",
                "shadow_mode": "false",
                # 第三來源的行情由中央機自己的 MT5 bridge 提供。策略開關預設
                # 關閉，避免升級既有部署時在未確認 broker/路徑前突然發布實單。
                "market_mt5_files_dir": "",
                "ultra_strategy_enabled": "false",
                "ultra_max_signals_per_day": "12",
                "ultra_cooldown_seconds": "900",
                "ultra_pending_expiry_seconds": "1200",
                "ultra_max_spread": "1.20",
                "ultra_min_h1_atr": "4.0",
                "ultra_max_h1_atr": "60.0",
                "ultra_max_market_age_seconds": "90",
                "cloudflare_tunnel": "true",
                "cloudflared_path": "",
                "auto_start": "false",
            }
        return {
            "hub_url": "https://gold-signal-hub-tw.fly.dev",
            # 這裡刻意沒有 "token"。
            #
            # 以前會員端內建那把共用 token，等於每個會員的硬碟上都躺著一份
            # **管理權限**憑證 —— 帶著它打 Hub 可以拿到全部訊號來源，完全
            # 繞過等級過濾。改成帳密登入之後，start_service() 沒登入就不給
            # 啟動、_run_client() 只用 session token，那把已經是死碼，留著
            # 只剩外洩風險。
            #
            # 舊的 settings.json 裡若還存著 token，也會在下一次存檔時被
            # save_settings() 濾掉（它只保留 defaults() 有的鍵）。
            "mt5_files_dir": "",
            "interval": "1.0",
            "auto_start": "true",   # 會員端拿掉了開關,登入後自動開始跟單
            "default_lot_size": "0.01",
            "use_martingale": "true",
            "martingale_multiplier": "2.0",
            "martingale_max_level": "5",
            "martingale_lots": "",
            "partial_close_ratios": "0.5,0.3,0.2",
            # 每個訊號來源各自的下單模式，存成 JSON 字串（設定檔全部是字串型別）
            "source_profiles": json.dumps({
                ULTRA_HIGH_FREQ: {
                    "enabled": False,
                    "mode": "flat",
                    "base_lot": 0.01,
                    "tp_mode": "breakeven",
                    "max_active_orders": 1,
                    "max_daily_trades": 12,
                    "max_daily_loss": 25.0,
                }
            }, ensure_ascii=False),
            # 同一個 MT5 帳戶裡，另外掛的、自己會下單的 EA（例如趨勢線策略）——
            # magic number -> 顯示名稱，純粹讓報表認出「這是誰下的」，不控制下單。
            # 20260503 是目前這台機器上「趨勢追蹤_EA_NR」的預設魔術編號。
            "ea_sources": '{"20260503": "趨勢線策略"}',
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
        if self.role == "central":
            # The first LINE-DB refactor shipped with only the mid-frequency
            # room in its saved default. Upgrade exactly that legacy value so
            # existing central machines regain yuyu; never alter custom lists.
            from copy_trader.line_db.factory import migrate_legacy_default_line_chats

            migrated, changed = migrate_legacy_default_line_chats(data.get("line_chats"))
            if changed:
                data["line_chats"] = migrated
                logger.info("已將既有中央 LINE DB 預設升級為中頻／yuyu 嚴格解析設定")
        else:
            # 升級舊會員端時，第三來源一定先以 0.01 均注、明確停用加入。
            # 使用者必須親自在來源表打開；不會因為升級就突然多出實單。
            try:
                profiles = json.loads(str(data.get("source_profiles") or "{}"))
                if not isinstance(profiles, dict):
                    profiles = {}
            except (TypeError, ValueError):
                profiles = {}
            profiles.setdefault(ULTRA_HIGH_FREQ, {
                "enabled": False,
                "mode": "flat",
                "base_lot": 0.01,
                "tp_mode": "breakeven",
                "max_active_orders": 1,
                "max_daily_trades": 12,
                "max_daily_loss": 25.0,
            })
            data["source_profiles"] = json.dumps(profiles, ensure_ascii=False)
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

        # 服務正在跑就立刻套用到交易引擎，不要等下次重啟。
        #
        # 原本 _apply_client_trade_settings() 只在 start_service() 裡呼叫一次，
        # 所以按「儲存」只會寫檔、更新 self.settings —— 執行中的 TradeManager 仍在用
        # 啟動當下載入的那份。而 self.settings 又確實被更新了，於是面板和 /api/status
        # 都顯示新值、實際下單卻是舊行為，「看起來改好了但沒生效」。
        #
        # 實測 2026-08-17：instance_3 在 8/14 20:55 啟動、20:57 把 tp_mode 從
        # breakeven 改成 partial。三天後面板顯示 partial，但當天兩筆 yuyu 的單
        # log 都還是「保本移損模式」—— 連我自己都先看檔案而誤判了一次。
        if self.is_running() and self.client_agent is not None:
            try:
                self._apply_client_trade_settings()
                logger.info("設定已即時套用到交易引擎（不需重啟）")
            except Exception as exc:
                logger.warning("設定已存檔，但即時套用失敗，需重啟才生效：%s", exc)
        return merged

    def _log(self, message: str) -> None:
        """把一行訊息推進面板日誌（跟 QueueLogHandler 走同一條隊列）。"""
        self.log_queue.put(f"{time.strftime('%H:%M:%S')} {message}")

    # ── 會員登入 ────────────────────────────────────────────────────────
    AUTH_ERROR_TEXT = {
        "bad_credentials": "帳號或密碼錯誤",
        "expired": "會員已到期，請聯繫管理員續期",
        "suspended": "帳號已停權，請聯繫管理員",
        "session_invalid": "此帳號已在其他裝置登入，你已被登出",
        "session_expired": "登入逾時，請重新登入",
        "membership_unavailable": "伺服器的會員系統暫時無法使用",
        "no_token": "尚未登入",
    }

    def _hub_base(self) -> str:
        return str(self.settings.get("hub_url") or "").rstrip("/")

    def _admin_base(self) -> str:
        """後台要打的 Hub 位址。

        中央機有兩種模式：填了 hub_url 就是用雲端 Hub，留空則是本機自架
        （_run_central 會在 settings["port"] 上開一個）。兩種都要能管會員。
        """
        remote = str(self.settings.get("hub_url") or "").strip().rstrip("/")
        if remote:
            return remote
        return f"http://127.0.0.1:{int(self.settings.get('port') or 8765)}"

    def admin_proxy(self, path: str, payload: Optional[Dict[str, Any]] = None):
        """代理一個 /admin/* 請求到 Hub，帶上管理 token。

        瀏覽器不直接打 Hub 的原因：管理 token 就不必送進前端 JS。面板只跟
        本機的控制台講話，token 全程留在這支程式裡。
        """
        return self._hub_call(path, payload,
                              token=str(self.settings.get("token") or ""),
                              base=self._admin_base(), timeout=20.0)

    def _hub_call(self, path: str, payload: Optional[Dict[str, Any]] = None,
                  token: str = "", timeout: float = 12.0, base: str = ""):
        """打 Hub。回傳 (狀態碼, 解析後的 JSON)。連不上回 (0, {...})。"""
        url = f"{base or self._hub_base()}{path}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url, data=data, method="POST" if data is not None else "GET",
            headers={"Content-Type": "application/json; charset=utf-8"})
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8"))
            except Exception:                        # noqa: BLE001
                return e.code, {"ok": False, "error": f"http_{e.code}"}
        except Exception as e:                       # noqa: BLE001
            return 0, {"ok": False, "error": "network", "detail": str(e)}

    def _load_session(self) -> None:
        try:
            if self.auth_path.exists():
                self.auth = json.loads(self.auth_path.read_text(encoding="utf-8"))
        except Exception as e:                       # noqa: BLE001
            logger.debug("讀取已存 session 失敗: %s", e)
            self.auth = None

    def _save_session(self) -> None:
        try:
            if self.auth:
                self.auth_path.write_text(
                    json.dumps(self.auth, ensure_ascii=False), encoding="utf-8")
            elif self.auth_path.exists():
                self.auth_path.unlink()
        except Exception as e:                       # noqa: BLE001
            logger.warning("寫入 session 失敗: %s", e)

    def _device_label(self) -> str:
        """給後台看的裝置名稱，方便判斷是誰在哪台登入。"""
        inst = _instance_name()
        return f"{platform.node()}{f'#{inst}' if inst else ''}"

    def login(self, username: str, password: str) -> Dict[str, Any]:
        if not self._hub_base():
            self.auth_error = "尚未設定訊號伺服器，請聯繫管理員"
            return {"ok": False, "error": self.auth_error}

        status, body = self._hub_call("/auth/login", {
            "username": username, "password": password, "device": self._device_label()})

        if status == 0:
            self.auth_error = "連不上伺服器，請檢查網路"
            return {"ok": False, "error": self.auth_error}
        if not body.get("ok"):
            code = str(body.get("error") or "unknown")
            self.auth_error = self.AUTH_ERROR_TEXT.get(code, f"登入失敗（{code}）")
            return {"ok": False, "error": self.auth_error, "code": code}

        self.auth = body["member"]
        self.auth_error = ""
        self.auth_checked_at = time.time()
        self._save_session()
        ent = self.auth.get("entitlements") or {}
        self._log(f"登入成功：{self.auth.get('username')}（{self.auth.get('tier_label')}）"
                  f" 可跟來源 {', '.join(ent.get('sources') or []) or '無'}")
        if self.auth.get("kicked_previous"):
            self._log("注意：此帳號原本在其他裝置登入，那一台已被登出")
        # 登入後把等級額度套進交易設定 — 不能等下次存檔。
        # 服務還沒啟動時 client_agent 是 None，那時不用套：start_service()
        # 會在建好 agent 之後自己呼叫一次。
        if self.is_running() and self.client_agent is not None:
            # 正在輪詢的 HubClient 是在啟動當下就把 token 存進去的，不換掉的話
            # 它會繼續用舊的那把。而「單一裝置」的規則是新登入會踢掉舊 session
            # ——包含同一台自己重新登入——所以舊 token 立刻失效，下一輪就 401、
            # 服務自己停掉。2026-08-24 實測踩到：程式讀回存檔的 session 自動
            # 開始跟單，我再登入一次，三台全部在一分鐘內停止。
            hub = getattr(self.client_agent, "hub", None)
            if hub is not None:
                hub.token = self.auth.get("session_token") or ""
                logger.info("已把新的登入憑證交給執行中的跟單服務")
            try:
                self._apply_client_trade_settings()
            except Exception as exc:                 # noqa: BLE001
                logger.warning("登入後套用等級額度失敗：%s", exc)
        return {"ok": True, "member": self.auth}

    def change_password(self, old_password: str, new_password: str) -> Dict[str, Any]:
        """會員自己改密碼。改完 session 保留，不用重新登入。"""
        token = (self.auth or {}).get("session_token") or ""
        if not token:
            return {"ok": False, "error": "尚未登入"}
        status, body = self._hub_call("/auth/change-password", {
            "old_password": old_password, "new_password": new_password}, token=token)
        if status == 0:
            return {"ok": False, "error": "連不上伺服器，請檢查網路"}
        if body.get("ok"):
            self._log("密碼已更新")
            return {"ok": True}
        code = str(body.get("error") or "unknown")
        return {"ok": False, "error": self.PASSWORD_ERROR_TEXT.get(
            code, self.AUTH_ERROR_TEXT.get(code, f"變更失敗（{code}）"))}

    PASSWORD_ERROR_TEXT = {
        "bad_old_password": "目前密碼不正確",
        # 門檻直接引用 membership 的常數，前後端才不會各講一套
        "too_short": f"新密碼至少要 {MIN_PASSWORD_LENGTH} 個字元",
        "same_as_old": "新密碼不能跟目前的一樣",
    }

    def logout(self, *, notify_hub: bool = True) -> None:
        token = (self.auth or {}).get("session_token") or ""
        if notify_hub and token:
            self._hub_call("/auth/logout", {}, token=token)
        if self.is_running():
            self.stop_service()
        self.auth = None
        self._save_session()
        self._log("已登出")

    def refresh_auth(self) -> bool:
        """跟 Hub 對一次帳號狀態。回傳是否仍然有效。

        會員端每秒輪詢訊號，但那條路徑只會拿到 401；這裡是為了把 401 的
        「原因」問清楚（被踢 / 到期 / 停權），好在面板上顯示正確訊息。
        """
        token = (self.auth or {}).get("session_token") or ""
        if not token:
            return False
        status, body = self._hub_call(f"/auth/me", token=token)
        self.auth_checked_at = time.time()
        if status == 0:
            return True          # 網路問題不當成登出，避免斷網就被踢回登入頁
        if body.get("ok"):
            member = body.get("member") or {}
            member["session_token"] = token          # /auth/me 不回 token
            self.auth = member
            self._save_session()
            return True
        code = str(body.get("error") or "session_invalid")
        self.auth_error = self.AUTH_ERROR_TEXT.get(code, f"登入失效（{code}）")
        self._log(f"帳號狀態異常：{self.auth_error} — 已停止跟單")
        self.auth = None
        self._save_session()
        if self.is_running():
            self.stop_service()
        return False

    def entitlements(self) -> Dict[str, Any]:
        """目前登入者的額度。沒登入就是全部不給。"""
        if not self.auth:
            return {"sources": [], "max_lot": 0.0, "martingale": False,
                    "partial_close": False, "label": ""}
        ent = dict(self.auth.get("entitlements") or {})
        ent.setdefault("sources", [])
        ent.setdefault("max_lot", None)
        ent.setdefault("martingale", False)
        ent.setdefault("partial_close", False)
        return ent

    def is_running(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    def start_service(self) -> None:
        if self.is_running():
            return
        # 會員端沒登入不准跑。否則會拿舊的共用 token 去輪詢, 繞過整個會員制。
        if self.role == "client" and not self.auth:
            self.status = "請先登入"
            self._log("尚未登入，無法啟動跟單")
            raise PermissionError("not_logged_in")
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
            from copy_trader.line_db.factory import build_line_database_source

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

            publisher = HubPublisher(publish_url, token)
            from copy_trader.central.ultra_strategy import UltraStrategyConfig, UltraStrategyEngine
            from copy_trader.central.stats import resolve_mt5_dir
            from copy_trader.line_db.ledger import LineMessageLedger

            ultra = UltraStrategyEngine(
                str(self.settings.get("market_mt5_files_dir") or ""),
                publisher,
                DATA_DIR / "ultra_strategy_state.json",
                UltraStrategyConfig.from_settings(self.settings),
            )
            logger.info(
                "第三來源「%s」：%s（實單訊號；中央 MT5=%s）",
                ULTRA_HIGH_FREQ,
                "已啟用" if ultra.config.enabled else "未啟用",
                ultra.mt5_dir,
            )
            # LINE 與市場模型共用 Hub，但不是同一條資料 pipeline。LINE DB
            # 尚未登入、資料庫暫時鎖住或金鑰錯誤時，模型仍應照常維護掛單與撤單；
            # collector 在背景每十秒重試初始化，不阻擋第三來源。
            collector = None
            next_line_init_at = 0.0
            self.status = "運行中"
            self.service_started_at = time.time()

            while not self.stop_event.is_set():
                if collector is None and time.monotonic() >= next_line_init_at:
                    try:
                        source = build_line_database_source(
                            database_path=str(self.settings.get("line_database_path") or ""),
                            keychain_service=str(
                                self.settings.get("line_keychain_service") or "line-db-research"
                            ),
                            line_chats=self.settings.get("line_chats"),
                            state_path=DATA_DIR / "line_db_cursor.json",
                        )
                        line_status = source.status()
                        collector = CentralSignalCollector(
                            source,
                            publisher,
                            LineMessageLedger(DATA_DIR / "line_message_ledger.sqlite3"),
                            shadow_mode=_truthy(self.settings.get("shadow_mode")),
                        )
                        logger.info(
                            "LINE 資料庫已連線：integrity=%s，聊天室=%s",
                            line_status.get("integrity_check"),
                            ", ".join(
                                chat["display_name"] for chat in line_status.get("chats", [])
                            ),
                        )
                    except Exception as exc:
                        next_line_init_at = time.monotonic() + 10.0
                        logger.warning("LINE pipeline 尚未就緒，10 秒後重試；第三來源不受影響：%s", exc)
                if collector is not None:
                    try:
                        published = collector.run_cycle()
                        if published:
                            logger.info("本輪發布 %s 筆訊號", published)
                    except Exception as exc:
                        logger.exception("中央擷取錯誤：%s", exc)
                try:
                    # 策略的故障不能拖垮 LINE 訊號；LINE 的故障也不能讓既有
                    # 超高頻掛單失去逾時撤單機會，因此兩條 pipeline 分開執行。
                    live_config = UltraStrategyConfig.from_settings(self.settings)
                    if live_config != ultra.config:
                        ultra.config = live_config
                        ultra.mt5_dir = resolve_mt5_dir(
                            str(self.settings.get("market_mt5_files_dir") or "")
                        )
                        logger.info(
                            "超高頻設定已即時更新：%s / MT5=%s",
                            "已啟用實單" if live_config.enabled else "已停用新訊號",
                            ultra.mt5_dir,
                        )
                    strategy_events = ultra.run_cycle()
                    if strategy_events:
                        logger.info("本輪發布 %s 筆超高頻事件", strategy_events)
                except Exception as exc:
                    logger.exception("超高頻策略錯誤：%s", exc)
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

    def _clamp_to_entitlements(self, profiles: Dict[str, Any]) -> Dict[str, Any]:
        """把來源設定壓到會員等級允許的範圍內。

        沒登入（例如訊號中心自己，或還沒接上會員制的舊部署）就原封不動放行。
        """
        if not self.auth:
            return profiles
        ent = self.entitlements()
        allowed = set(ent.get("sources") or [])
        max_lot = ent.get("max_lot")
        out: Dict[str, Any] = {}
        for name, p in profiles.items():
            p = dict(p)
            if p.get("enabled") and name not in allowed:
                p["enabled"] = False
                logger.info("來源「%s」不在等級「%s」的授權範圍，已停用", name, ent.get("label"))
            if max_lot:
                try:
                    if float(p.get("base_lot") or 0) > max_lot:
                        logger.info("來源「%s」基礎手數 %s 超過等級上限，改為 %s",
                                    name, p.get("base_lot"), max_lot)
                        p["base_lot"] = max_lot
                except (TypeError, ValueError):
                    pass
            if not ent.get("martingale") and str(p.get("mode", "")).lower() == "martingale":
                logger.info("來源「%s」的馬丁不在等級授權內，改為均注", name)
                p["mode"] = "flat"
            if not ent.get("partial_close") and str(p.get("tp_mode", "")).lower() == "partial":
                # 降級成保本移損: 一樣吃得到多 TP, 但不分批出場
                logger.info("來源「%s」的分批平倉不在等級授權內，改為保本移損", name)
                p["tp_mode"] = "breakeven"
            out[name] = p
        return out

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
        # 依會員等級箝制。這是用戶端自律的那一層 —— 真正擋住未授權來源的是
        # Hub（它根本不會把那些訊號送下來），這裡處理的是手數上限、馬丁、
        # 分批平倉這些「只影響會員自己帳戶」的額度。
        profiles = self._clamp_to_entitlements(profiles)
        ent = self.entitlements()
        max_lot = ent.get("max_lot")
        if max_lot and tm.default_lot_size > max_lot:
            logger.info("等級手數上限 %s：全域基礎手數 %s → %s",
                        max_lot, tm.default_lot_size, max_lot)
            tm.default_lot_size = max_lot
        if self.auth and not ent.get("martingale") and tm.use_martingale:
            logger.info("等級「%s」不含馬丁，全域馬丁已關閉", ent.get("label"))
            tm.use_martingale = False

        tm.source_profiles = profiles
        if profiles:
            # 混用均注/馬丁時，層級一定要各群分開算，否則會互相污染
            tm.martingale_per_source = True
            for name, p in profiles.items():
                mode = "均注" if str(p.get("mode", "")).lower() == "flat" else "馬丁"
                on = "跟單" if p.get("enabled", True) else "已停用"
                logger.info("來源設定：%s → %s / %s / 基礎手數 %s", name, on, mode, p.get("base_lot", "(全域)"))

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
            # 只用會員自己的 session token —— Hub 會依他的等級過濾來源。
            # 這裡不再回退到任何共用 token: 沒有 session 就代表沒登入,
            # 而沒登入本來就不該跑 (start_service 會先擋下)。
            token = str((self.auth or {}).get("session_token") or "")
            if not token:
                logger.error("尚未登入，無法連線訊號伺服器")
                self.status = "請先登入"
                return
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
                    # 日誌會顯示在會員的面板上，不要把伺服器位址印出去
                    logger.warning("無法連線訊號伺服器，10 秒後重試：%s", exc)
                    self.stop_event.wait(10)
            if self.client_agent is None:
                return

            self._apply_client_trade_settings()
            # 重啟後把 MT5 上還活著的單接回追蹤，讓成交結果與 LINE 引用撤單
            # 仍能命中原始 execution ID。
            self.client_agent.trade_manager.adopt_open_orders()
            self.client_agent.trade_manager.start()
            logger.info("會員端已啟動，last_seq=%s", self.client_agent.last_seq)
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
                    if exc.code in (401, 403):
                        # 已登入的會員收到 401/403，代表 session 出事了 —— 被別台
                        # 踢掉、到期、或被停權。問清楚原因並停止跟單，不要繼續
                        # 每秒重試洗版（而且再怎麼重試也拿不到訊號）。
                        if self.auth:
                            logger.error("Hub 拒絕此連線（%s），確認帳號狀態中…", exc.code)
                            self.refresh_auth()      # 失效時會自己 stop_service()
                            break
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

    # 多久跟 Hub 對一次帳號狀態。面板每 2 秒輪詢一次 /api/status，不能每次都
    # 打網路；60 秒足夠讓「被別台踢掉 / 到期 / 停權」在畫面上反應出來。
    AUTH_RECHECK_SEC = 60.0

    def _maybe_recheck_auth(self) -> None:
        if not self.auth or self.role != "client":
            return
        if time.time() - self.auth_checked_at < self.AUTH_RECHECK_SEC:
            return
        self.auth_checked_at = time.time()      # 先記時間，避免併發重複發請求
        # 丟到背景做：這是在 HTTP handler 執行緒裡被呼叫的，不能卡住面板
        threading.Thread(target=self.refresh_auth, daemon=True).start()

    # 會員端絕不能送到瀏覽器的設定欄位。
    #
    # token 是**管理權限**的通行證 —— 帶著它打 Hub 可以拿到全部訊號來源，
    # 完全繞過等級過濾。以前這個值會隨 /api/status 明文送進前端（設定面板
    # 有個 type="password" 的欄位在填它），等於任何會員打開自己的面板網址
    # 加 /api/status 就能把整個付費牆拆掉。
    #
    # hub_url 沒那麼致命，但也沒有讓會員知道的理由，一起擋掉。
    _CLIENT_SECRET_KEYS = ("token", "hub_url")

    def _public_settings(self) -> Dict[str, Any]:
        if self.role != "client":
            return self.settings
        return {k: v for k, v in self.settings.items()
                if k not in self._CLIENT_SECRET_KEYS}

    def snapshot(self) -> Dict[str, Any]:
        self.drain_logs()
        self._maybe_recheck_auth()
        return {
            "role": self.role,
            "title": self.title,
            "settings": self._public_settings(),
            # 前端只需要知道「有沒有設定好」，不需要知道設定成什麼
            "hub_configured": bool(str(self.settings.get("hub_url") or "").strip()),
            "status": self.status,
            "running": self.is_running(),
            "logs": self.logs[-200:],
            "lan_ip": _lan_ip(),
            "cloudflare_url": self.cloudflare_url,
            "uptime_seconds": int(time.time() - self.service_started_at) if self.service_started_at else 0,
            "auth": self._auth_snapshot(),
            "line_chats": self._line_chat_names(),
            "tick": self._live_tick(),
        }

    def _live_tick(self) -> Optional[Dict[str, Any]]:
        """主商品的即時 bid/ask。EA 每秒寫一次，這裡搭 /api/status 的順風車
        —— 前端本來就每秒打這支，不用為了報價多開一條輪詢。
        訊號中心沒有 MT5，直接回 None。"""
        if self.role != "client":
            return None
        try:
            from copy_trader.central.market import live_tick
            return live_tick(self.settings)
        except Exception:
            logger.debug("讀不到即時報價", exc_info=True)
            return None

    def _line_chat_names(self) -> List[str]:
        """Return configured LINE DB chat labels for the central dashboard."""
        if self.role != "central":
            return []
        try:
            from copy_trader.line_db.factory import parse_line_chat_targets

            return [target.display_name for target in parse_line_chat_targets(
                self.settings.get("line_chats")
            )]
        except Exception:
            logger.debug("讀不到 line_chats", exc_info=True)
            return []

    def test_line_database(self) -> Dict[str, Any]:
        """Validate the encrypted DB, configured chats and persistent cursor."""
        if self.role != "central":
            raise PermissionError("central_only")
        from copy_trader.line_db.factory import build_line_database_source

        source = build_line_database_source(
            database_path=str(self.settings.get("line_database_path") or ""),
            keychain_service=str(self.settings.get("line_keychain_service") or "line-db-research"),
            line_chats=self.settings.get("line_chats"),
            state_path=DATA_DIR / "line_db_cursor.json",
        )
        return source.status()

    def find_line_databases(self) -> Dict[str, Any]:
        """Return bounded local candidates without opening or decrypting them."""
        if self.role != "central":
            raise PermissionError("central_only")
        from copy_trader.line_db.discovery import (
            choose_database_candidate,
            discover_database_candidates,
        )

        candidates = discover_database_candidates()
        try:
            recommended = str(choose_database_candidate(candidates))
        except RuntimeError:
            recommended = ""
        return {
            "platform": sys.platform,
            "recommended": recommended,
            "candidates": [candidate.public_dict() for candidate in candidates],
        }

    def _auth_snapshot(self) -> Optional[Dict[str, Any]]:
        """給前端的登入狀態。session_token 絕不外送到瀏覽器。"""
        if self.role != "client":
            return None
        if not self.auth:
            return {"logged_in": False, "error": self.auth_error}
        a = self.auth
        return {
            "logged_in": True,
            "username": a.get("username"),
            "tier": a.get("tier"),
            "tier_label": a.get("tier_label"),
            "expires_at": a.get("expires_at"),
            "entitlements": self.entitlements(),
            "error": "",
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
            # 會員後台：/api/admin/* 原樣轉給 Hub 的 /admin/*。
            # 只有訊號中心能用，而且控制台只綁本機（對外的 Cloudflare
            # Tunnel 開的是 Hub 那個 port，不是這個控制台）。
            if parsed.path.startswith("/api/admin/"):
                if state.role != "central":
                    _json_response(self, 403, {"ok": False, "error": "central_only"})
                    return
                q = f"?{parsed.query}" if parsed.query else ""
                status, body = state.admin_proxy(parsed.path[len("/api"):] + q)
                _json_response(self, status or 502, body)
                return
            if parsed.path == "/api/stats":
                # 績效統計純粹是讀檔彙整，MT5 沒開就回空資料，不該讓控制台整頁掛掉
                try:
                    from copy_trader.central.stats import build_stats

                    agent = state.client_agent
                    stats = build_stats(
                        state.settings,
                        trade_manager=agent.trade_manager if agent is not None else None,
                        known_sources=(state.entitlements().get("sources") or [])
                        if state.role == "client" else [],
                    )
                    _json_response(self, 200, {"ok": True, "stats": stats})
                except Exception as exc:
                    logger.exception("stats failed: %s", exc)
                    _json_response(self, 500, {"ok": False, "error": str(exc)})
                return
            if parsed.path == "/api/market":
                # 圖表資料。跟 /api/stats 一樣，MT5 沒開就回空的，不讓整頁掛掉
                try:
                    from copy_trader.central.market import build_market

                    q = urllib.parse.parse_qs(parsed.query)
                    tf = (q.get("tf") or ["M15"])[0]
                    _json_response(self, 200,
                                   {"ok": True, "market": build_market(state.settings, tf)})
                except Exception as exc:
                    logger.exception("market failed: %s", exc)
                    _json_response(self, 500, {"ok": False, "error": str(exc)})
                return
            _json_response(self, 404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            try:
                if parsed.path.startswith("/api/admin/"):
                    if state.role != "central":
                        _json_response(self, 403, {"ok": False, "error": "central_only"})
                        return
                    status, body = state.admin_proxy(parsed.path[len("/api"):],
                                                     _read_json(self))
                    _json_response(self, status or 502, body)
                    return
                if parsed.path == "/api/login":
                    data = _read_json(self)
                    result = state.login(str(data.get("username") or ""),
                                         str(data.get("password") or ""))
                    _json_response(self, 200 if result.get("ok") else 401, result)
                    return
                if parsed.path == "/api/logout":
                    state.logout()
                    _json_response(self, 200, {"ok": True})
                    return
                if parsed.path == "/api/change-password":
                    data = _read_json(self)
                    result = state.change_password(
                        str(data.get("old_password") or ""),
                        str(data.get("new_password") or ""))
                    _json_response(self, 200 if result.get("ok") else 400, result)
                    return
                if parsed.path == "/api/settings":
                    settings = state.save_settings(_read_json(self))
                    _json_response(self, 200, {"ok": True, "settings": settings})
                    return
                if parsed.path == "/api/start":
                    data = _read_json(self)
                    if data:
                        state.save_settings(data)
                    try:
                        state.start_service()
                    except PermissionError:
                        _json_response(self, 403, {"ok": False, "error": "not_logged_in",
                                                   "message": "請先登入會員帳號"})
                        return
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
                if parsed.path == "/api/test-line-database":
                    if state.role != "central":
                        _json_response(self, 403, {"ok": False, "error": "central_only"})
                        return
                    state.save_settings(_read_json(self))
                    status = state.test_line_database()
                    _json_response(self, 200, {"ok": True, "line_database": status})
                    return
                if parsed.path == "/api/find-line-databases":
                    if state.role != "central":
                        _json_response(self, 403, {"ok": False, "error": "central_only"})
                        return
                    result = state.find_line_databases()
                    _json_response(self, 200, {"ok": True, "line_databases": result})
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


def _open_browser(url: str) -> None:
    """開啟控制台。設了 COPY_TRADER_NO_BROWSER 就只記在 log 不彈視窗。

    給兩種情境用：CI 上的打包冒煙測試（沒有桌面環境），以及把這支程式
    掛在遠端主機當服務跑的時候。
    """
    if _truthy(os.environ.get("COPY_TRADER_NO_BROWSER")):
        logger.info("已停用自動開啟瀏覽器（COPY_TRADER_NO_BROWSER）")
        return
    try:
        webbrowser.open(url)
    except Exception:
        # 沒有預設瀏覽器不該讓整個服務起不來 —— 網址已經寫在 log 裡了
        logger.debug("開啟瀏覽器失敗", exc_info=True)


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
            _open_browser(existing)
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
    _open_browser(url)
    logger.info("控制台：%s", url)

    if _truthy(state.settings.get("auto_start")):
        logger.info("已設定自動開始，啟動服務中…")
        try:
            state.start_service()
        except PermissionError:
            # 會員端沒登入就不給啟動 (start_service 會擋)。這裡一定要接住 ——
            # 不接的話例外會往上冒、整支程式在 serve_forever() 之前就結束，
            # 使用者連登入畫面都看不到, 只會覺得「程式打不開」。
            logger.info("尚未登入，請在控制台登入後開始跟單")
            state.status = "請先登入"
        except Exception as exc:                     # noqa: BLE001
            logger.exception("自動啟動失敗：%s", exc)
            state.status = "啟動失敗"

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
