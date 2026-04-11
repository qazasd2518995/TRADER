"""
黃金跟單系統 — JSON-RPC Sidecar Entry Point
Replaces main_gui.py. Communicates with Tauri frontend via stdin/stdout.

Protocol:
  Frontend → Sidecar (stdin):  {"id": 1, "method": "start_trading", "params": {...}}
  Sidecar → Frontend (stdout): {"id": 1, "result": {"status": "ok"}}          // response
                                {"event": "price", "data": {"bid": 2645, ...}} // push event
"""
import asyncio
import json
import sys
import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

# Force UTF-8 on stdout/stdin so emoji and CJK survive cp950 Windows consoles
if sys.stdout.encoding != 'utf-8':
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', errors='replace', buffering=1)
if sys.stdin.encoding != 'utf-8':
    sys.stdin = open(sys.stdin.fileno(), mode='r', encoding='utf-8', errors='replace')

# Ensure copy_trader package is importable without shadowing stdlib modules.
_this_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_this_dir)
while _this_dir in sys.path:
    sys.path.remove(_this_dir)
sys.path.append(_this_dir)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

try:
    from copy_trader.config import Config, CaptureWindow, load_config, save_config as _save_config, DATA_DIR, DEFAULT_SYMBOL
    from copy_trader.mt5_reader import MT5DataReader
    from copy_trader.auth_handler import AuthHandler
except ModuleNotFoundError as exc:
    # Dev fallback: allow running sidecar_main.py directly from copy_trader/.
    if exc.name not in {"copy_trader", "copy_trader.config", "copy_trader.mt5_reader", "copy_trader.auth_handler"}:
        raise
    from config import Config, CaptureWindow, load_config, save_config as _save_config, DATA_DIR, DEFAULT_SYMBOL
    from mt5_reader import MT5DataReader
    from auth_handler import AuthHandler

logger = logging.getLogger(__name__)


class StdoutLogHandler(logging.Handler):
    """Forwards Python logs to frontend as JSON events on stdout."""

    def __init__(self, emit_fn):
        super().__init__()
        self._emit = emit_fn
        self.setFormatter(logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%H:%M:%S",
        ))
        # Rate limiter: max 50 log lines/sec
        self._count = 0
        self._window_start = time.time()

    def emit(self, record):
        try:
            now = time.time()
            if now - self._window_start > 1:
                self._count = 0
                self._window_start = now
            self._count += 1
            if self._count > 50:
                return  # drop excessive logs

            msg = self.format(record)
            self._emit("log", {
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "level": record.levelname,
                "message": msg,
            })
        except Exception:
            pass


class JsonRpcSidecar:
    """JSON-RPC sidecar — mirrors BackendBridge methods."""

    def __init__(self):
        self.config: Config = load_config()
        self.trader = None
        self.mt5_reader: MT5DataReader | None = None
        self._trading_task = None
        self._mt5_task = None
        self._periodic_task = None
        self._stdin_executor = ThreadPoolExecutor(max_workers=1)
        self._auth: AuthHandler | None = None
        self._user_plan: str = "trial"  # current logged-in user's plan
        self._status: str = "stopped"
        self._start_seq: int = 0

    def _get_auth(self) -> AuthHandler:
        """Lazy-init AuthHandler (so boto3 import doesn't block startup)."""
        if self._auth is None:
            self._auth = AuthHandler()
        return self._auth

    def _price_file_candidates(self) -> list[Path]:
        symbol_name = getattr(self.config, "symbol_name", DEFAULT_SYMBOL)
        candidates = []
        if symbol_name:
            candidates.append(Path(self.config.mt5_files_dir) / f"{symbol_name}_price.json")
        candidates.append(Path(self.config.mt5_files_dir) / f"{DEFAULT_SYMBOL}_price.json")
        unique: list[Path] = []
        seen = set()
        for path in candidates:
            if str(path) in seen:
                continue
            unique.append(path)
            seen.add(str(path))
        return unique

    def _read_martingale_snapshot(self) -> dict:
        """Read persisted martingale state even when trading is stopped."""
        state_file = Path(self.config.mt5_files_dir) / "martingale_state.json"
        level = 0
        consecutive_losses = 0

        try:
            with open(state_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
            level = int(payload.get("level", 0) or 0)
            consecutive_losses = int(payload.get("consecutive_losses", 0) or 0)
        except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
            pass

        if getattr(self.config, "martingale_lots", None):
            lots = self.config.martingale_lots
            lot_size = lots[min(level, len(lots) - 1)]
        elif getattr(self.config, "use_martingale", True):
            lot_size = round(
                self.config.default_lot_size * (self.config.martingale_multiplier ** level),
                2,
            )
        else:
            lot_size = self.config.default_lot_size

        return {
            "level": level,
            "lot_size": lot_size,
            "consecutive_losses": consecutive_losses,
        }

    # ── Output ──────────────────────────────────────────────

    def _write(self, obj: dict):
        """Write a JSON line to stdout (thread-safe via event loop)."""
        line = json.dumps(obj, ensure_ascii=False)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def _emit_event(self, event: str, data):
        """Push an event to the frontend."""
        self._write({"event": event, "data": data})

    def _respond(self, req_id: int, result=None, error=None):
        """Send a JSON-RPC response."""
        resp: dict = {"id": req_id}
        if error:
            resp["error"] = error
        else:
            resp["result"] = result if result is not None else {"status": "ok"}
        self._write(resp)

    def _build_copy_trader(self):
        """Import and construct CopyTrader off the main event loop."""
        try:
            from copy_trader.app import CopyTrader
        except ModuleNotFoundError as exc:
            if exc.name not in {"copy_trader", "copy_trader.app"}:
                raise
            from app import CopyTrader
        return CopyTrader(self.config, event_callback=self._backend_event)

    def _request_macos_permissions(self) -> dict:
        """Trigger macOS permission prompts for screen capture/accessibility."""
        if sys.platform != "darwin":
            return {}

        try:
            from copy_trader.platform.macos import get_macos_permission_status
        except ImportError:
            return {}

        try:
            return get_macos_permission_status(prompt=True)
        except Exception as e:
            logger.debug(f"Unable to request macOS permissions: {e}")
            return {}

    async def _run_trading_lifecycle(self, start_seq: int):
        """Build CopyTrader in a worker thread, then run it on the event loop."""
        try:
            logger.info("Initializing CopyTrader in background...")
            loop = asyncio.get_running_loop()
            trader = await loop.run_in_executor(None, self._build_copy_trader)

            if start_seq != self._start_seq or self._status != "starting":
                try:
                    trader.stop()
                except Exception:
                    pass
                return

            self.trader = trader
            self._status = "running"
            self._emit_event("status", {"status": "running"})
            logger.info("Trading started via sidecar RPC")
            await trader.start()

            if start_seq == self._start_seq and self._status == "running":
                self._status = "stopped"
                self._emit_event("status", {"status": "stopped"})
                logger.info("Trading loop exited")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Failed to start trading: {e}", exc_info=True)
            if start_seq == self._start_seq:
                self._status = "error"
                self._emit_event("status", {"status": "error"})
        finally:
            if start_seq == self._start_seq:
                self.trader = None
                self._trading_task = None

    # ── Command handlers ────────────────────────────────────

    # Plan limits: max capture windows per plan
    PLAN_MAX_WINDOWS = {"trial": 1, "standard": 1, "premium": 999}

    async def handle_start_trading(self, req_id: int, params: dict):
        if self._status in {"starting", "running"}:
            self._respond(req_id, result={"status": self._status})
            return

        try:
            # Merge incoming params into config
            self._apply_params_to_config(params)

            # Enforce group limit based on user plan
            max_win = self.PLAN_MAX_WINDOWS.get(self._user_plan, 1)
            if len(self.config.capture_windows) > max_win:
                logger.warning(f"Plan '{self._user_plan}' allows {max_win} group(s), "
                               f"truncating from {len(self.config.capture_windows)}")
                self.config.capture_windows = self.config.capture_windows[:max_win]

            # Update MT5 reader path
            if self.mt5_reader:
                self.mt5_reader.set_mt5_dir(self.config.mt5_files_dir)
                self.mt5_reader.set_symbol_name(getattr(self.config, "symbol_name", DEFAULT_SYMBOL))

            self._start_seq += 1
            start_seq = self._start_seq
            self._status = "starting"
            self._emit_event("status", {"status": "starting"})
            self._trading_task = asyncio.create_task(self._run_trading_lifecycle(start_seq))
            self._respond(req_id, result={"status": "starting"})
        except Exception as e:
            logger.error(f"Failed to start trading: {e}", exc_info=True)
            self._status = "error"
            self._emit_event("status", {"status": "error"})
            self._respond(req_id, error={"code": -1, "message": str(e)})

    async def handle_stop_trading(self, req_id: int, _params: dict):
        self._start_seq += 1
        self._status = "stopped"
        if self.trader:
            self.trader.stop()
            self.trader = None
        self._trading_task = None
        self._emit_event("status", {"status": "stopped"})
        self._respond(req_id)
        logger.info("Trading stopped via sidecar RPC")

    async def handle_reset_martingale(self, req_id: int, _params: dict):
        if self.trader:
            self.trader.trade_manager.reset_martingale()
            lot = self.trader.trade_manager.default_lot_size
            self._emit_event("martingale", {
                "level": 0,
                "lot_size": lot,
                "consecutive_losses": 0,
            })
            logger.info("Martingale reset via sidecar RPC")
        self._respond(req_id)

    async def handle_save_config(self, req_id: int, params: dict):
        try:
            self._apply_params_to_config(params)
            _save_config(self.config)
            self._respond(req_id)
            logger.info("Config saved via sidecar RPC")
        except Exception as e:
            self._respond(req_id, error={"code": -1, "message": str(e)})

    async def handle_get_config(self, req_id: int, _params: dict):
        self.config = load_config()
        data = self._config_to_dict()
        self._respond(req_id, result=data)

    async def handle_test_mt5_connection(self, req_id: int, _params: dict):
        def _check():
            for price_file in self._price_file_candidates():
                if price_file.exists():
                    age = time.time() - price_file.stat().st_mtime
                    return {"connected": age < 30, "age": round(age, 1), "file": str(price_file)}
            return {"connected": False, "age": -1}
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _check)
            self._respond(req_id, result=result)
        except Exception as e:
            self._respond(req_id, error={"code": -1, "message": str(e)})

    async def handle_detect_windows(self, req_id: int, _params: dict):
        def _detect():
            try:
                from copy_trader.signal_capture.screen_capture import list_app_windows
            except ImportError:
                from signal_capture.screen_capture import list_app_windows
            raw = list_app_windows("")
            return [
                {
                    "window_id": w.get("window_id") or w.get("id"),
                    "window_name": w.get("window_name") or w.get("name") or "",
                    "owner": w.get("owner") or "",
                    "label": w.get("label") or w.get("window_name") or w.get("name") or "",
                    "bounds": w.get("bounds") or {},
                }
                for w in raw
                if (w.get("label") or w.get("window_name") or w.get("name"))
            ]
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._request_macos_permissions)
            windows = await loop.run_in_executor(None, _detect)
            self._respond(req_id, result={"windows": windows})
        except Exception as e:
            self._respond(req_id, error={"code": -1, "message": str(e)})

    async def handle_preview_window(self, req_id: int, params: dict):
        def _preview():
            try:
                from copy_trader.signal_capture.screen_capture import capture_window_preview
            except ImportError:
                from signal_capture.screen_capture import capture_window_preview

            return capture_window_preview(
                window_id=params.get("window_id"),
                window_name=params.get("window_name"),
                app_name=params.get("app_name", "LINE"),
            )

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._request_macos_permissions)
            result = await loop.run_in_executor(None, _preview)
            if not result:
                self._respond(req_id, error={"code": -1, "message": "Unable to capture preview"})
                return
            if not result.get("ok", True):
                self._respond(req_id, error={"code": -1, "message": result.get("message", "Unable to capture preview")})
                return
            self._respond(req_id, result=result)
        except Exception as e:
            self._respond(req_id, error={"code": -1, "message": str(e)})

    async def handle_detect_mt5_dir(self, req_id: int, _params: dict):
        def _detect():
            detected = self.config._find_mt5_files_dir()
            return {"path": detected, "exists": os.path.isdir(detected)}
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _detect)
            self._respond(req_id, result=result)
        except Exception as e:
            self._respond(req_id, error={"code": -1, "message": str(e)})

    # ── Auth handlers ────────────────────────────────────────

    async def handle_login(self, req_id: int, params: dict):
        def _do():
            return self._get_auth().login(params.get("email", ""), params.get("password", ""))
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _do)
        if "error" in result:
            self._respond(req_id, error={"code": -1, "message": result["error"]})
        else:
            # Remember user plan for group limit enforcement
            user = result.get("user", {})
            self._user_plan = user.get("plan", "trial")
            logger.info(f"User logged in, plan: {self._user_plan}")
            self._respond(req_id, result=result)

    async def handle_verify_subscription(self, req_id: int, params: dict):
        def _do():
            return self._get_auth().verify_subscription(params.get("email", ""))
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _do)
        # Keep plan in sync
        if result.get("plan"):
            self._user_plan = result["plan"]
        self._respond(req_id, result=result)

    async def handle_change_password(self, req_id: int, params: dict):
        def _do():
            return self._get_auth().change_password(
                params.get("email", ""),
                params.get("old_password", ""),
                params.get("new_password", ""),
            )
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _do)
        if "error" in result:
            self._respond(req_id, error={"code": -1, "message": result["error"]})
        else:
            self._respond(req_id, result=result)

    async def handle_admin_list_users(self, req_id: int, params: dict):
        def _do():
            return self._get_auth().admin_list_users(params.get("admin_email", ""))
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _do)
        self._respond(req_id, result=result)

    async def handle_admin_create_user(self, req_id: int, params: dict):
        def _do():
            admin_email = params.pop("admin_email", "")
            return self._get_auth().admin_create_user(admin_email, params)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _do)
        if "error" in result:
            self._respond(req_id, error={"code": -1, "message": result["error"]})
        else:
            self._respond(req_id, result=result)

    async def handle_admin_update_user(self, req_id: int, params: dict):
        def _do():
            admin_email = params.get("admin_email", "")
            email = params.get("email", "")
            updates = {k: v for k, v in params.items() if k not in ("admin_email", "email")}
            return self._get_auth().admin_update_user(admin_email, email, updates)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _do)
        if "error" in result:
            self._respond(req_id, error={"code": -1, "message": result["error"]})
        else:
            self._respond(req_id, result=result)

    async def handle_admin_delete_user(self, req_id: int, params: dict):
        def _do():
            return self._get_auth().admin_delete_user(
                params.get("admin_email", ""),
                params.get("target_email", ""),
            )
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _do)
        if "error" in result:
            self._respond(req_id, error={"code": -1, "message": result["error"]})
        else:
            self._respond(req_id, result=result)

    # ── Backend event callback ──────────────────────────────

    def _backend_event(self, event_type: str, data: dict):
        try:
            if event_type == "signal_detected":
                self._emit_event("signal_detected", data)
            elif event_type == "trade_submitted":
                self._emit_event("trade_submitted", data)
            elif event_type == "martingale_updated":
                self._emit_event("martingale", {
                    "level": data.get("level", 0),
                    "lot_size": data.get("lot_size", 0.01),
                    "consecutive_losses": data.get("consecutive_losses", 0),
                })
            elif event_type == "stats_updated":
                self._emit_event("stats", data)
        except Exception as e:
            logger.error(f"Backend event error: {e}")

    def _read_today_trade_stats(self) -> dict:
        """Build realized PnL stats from MT5 closed_trades.json."""
        closed_trades_file = Path(self.config.mt5_files_dir) / "closed_trades.json"
        account_info_file = Path(self.config.mt5_files_dir) / "account_info.json"

        try:
            with open(closed_trades_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"wins": 0, "losses": 0, "daily_loss": 0.0, "realized_pnl": 0.0}

        skew_seconds = 0
        try:
            with open(account_info_file, "r", encoding="utf-8") as f:
                account_payload = json.load(f)
            account_timestamp = int(account_payload.get("timestamp", 0))
            if account_timestamp > 0:
                skew_seconds = int(time.time()) - account_timestamp
        except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
            pass

        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        wins = 0
        losses = 0
        realized_pnl = 0.0

        for trade in payload.get("trades", []):
            try:
                close_timestamp = int(trade.get("close_timestamp", 0))
                if close_timestamp <= 0:
                    continue

                close_time = datetime.fromtimestamp(close_timestamp + skew_seconds)
                if not (today_start <= close_time < today_end):
                    continue

                profit = float(trade.get("profit", 0))
                realized_pnl += profit
                if profit >= 0:
                    wins += 1
                else:
                    losses += 1
            except (TypeError, ValueError, OSError):
                continue

        return {
            "wins": wins,
            "losses": losses,
            "daily_loss": -realized_pnl,
            "realized_pnl": realized_pnl,
        }

    async def _periodic_updates(self):
        """Periodically push dashboard data whether or not trading is active."""
        while True:
            try:
                realized = self._read_today_trade_stats()
                if self.trader and self.trader._running:
                    tm = self.trader.trade_manager
                    martingale = {
                        "level": tm.current_martingale_level,
                        "lot_size": tm.get_martingale_lot_size(),
                        "consecutive_losses": getattr(tm, "consecutive_losses", 0),
                    }
                    api_calls = self.trader._api_calls_today
                else:
                    martingale = self._read_martingale_snapshot()
                    api_calls = 0

                self._emit_event("martingale", martingale)
                self._emit_event("stats", {
                    "daily_trades": realized["wins"] + realized["losses"],
                    "wins": realized["wins"],
                    "losses": realized["losses"],
                    "daily_loss": realized["daily_loss"],
                    "api_calls": api_calls,
                })
            except Exception as e:
                logger.debug(f"Periodic update error: {e}")
            await asyncio.sleep(2)

    # ── Config helpers ──────────────────────────────────────

    def _apply_params_to_config(self, params: dict):
        if not params:
            return
        c = self.config
        for key in [
            "default_lot_size", "symbol_name", "auto_execute",
            "cancel_pending_after_seconds", "use_martingale",
            "martingale_lots", "martingale_max_level", "martingale_per_source",
            "martingale_source_lots",
            "parser_mode",
            "capture_interval", "ocr_confirm_count", "ocr_confirm_delay",
            "min_confidence", "max_price_deviation", "signal_dedup_minutes",
            "max_daily_loss", "max_open_positions", "mt5_files_dir",
            "partial_close_ratios",
        ]:
            if key in params:
                setattr(c, key, params[key])

        if "capture_windows" in params:
            c.capture_windows = [
                CaptureWindow(
                    window_name=w.get("window_name", ""),
                    app_name=w.get("app_name", "LINE"),
                    name=w.get("name", f"win_{i}"),
                    window_id=w.get("window_id"),
                    display_name=w.get("display_name", w.get("window_name", "")),
                )
                for i, w in enumerate(params["capture_windows"])
            ]

    def _config_to_dict(self) -> dict:
        c = self.config
        return {
            "default_lot_size": c.default_lot_size,
            "symbol_name": getattr(c, "symbol_name", DEFAULT_SYMBOL),
            "auto_execute": c.auto_execute,
            "cancel_pending_after_seconds": c.cancel_pending_after_seconds,
            "use_martingale": c.use_martingale,
            "martingale_lots": c.martingale_lots,
            "martingale_max_level": c.martingale_max_level,
            "martingale_per_source": getattr(c, 'martingale_per_source', False),
            "martingale_source_lots": getattr(c, 'martingale_source_lots', {}),
            "parser_mode": c.parser_mode,
            "capture_interval": c.capture_interval,
            "capture_windows": [
                {
                    "window_name": w.window_name,
                    "app_name": w.app_name,
                    "name": w.name,
                    "window_id": w.window_id,
                    "display_name": getattr(w, "display_name", w.window_name),
                }
                for w in c.capture_windows
            ],
            "ocr_confirm_count": c.ocr_confirm_count,
            "ocr_confirm_delay": c.ocr_confirm_delay,
            "min_confidence": c.min_confidence,
            "max_price_deviation": c.max_price_deviation,
            "signal_dedup_minutes": c.signal_dedup_minutes,
            "max_daily_loss": c.max_daily_loss,
            "max_open_positions": c.max_open_positions,
            "mt5_files_dir": c.mt5_files_dir,
            "partial_close_ratios": c.partial_close_ratios,
        }

    # ── Stdin reader ────────────────────────────────────────

    def _read_stdin_line(self) -> str | None:
        """Blocking read from stdin (runs in executor thread)."""
        try:
            line = sys.stdin.readline()
            if not line:
                return None  # EOF
            return line.strip()
        except Exception:
            return None

    async def _stdin_loop(self):
        """Read JSON-RPC commands from stdin."""
        loop = asyncio.get_event_loop()
        while True:
            line = await loop.run_in_executor(self._stdin_executor, self._read_stdin_line)
            if line is None:
                logger.info("stdin closed, exiting")
                break
            if not line:
                continue

            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from stdin: {line[:100]}")
                continue

            req_id = req.get("id", 0)
            method = req.get("method", "")
            params = req.get("params", {})

            handler = {
                "start_trading": self.handle_start_trading,
                "stop_trading": self.handle_stop_trading,
                "reset_martingale": self.handle_reset_martingale,
                "save_config": self.handle_save_config,
                "get_config": self.handle_get_config,
                "test_mt5_connection": self.handle_test_mt5_connection,
                "detect_windows": self.handle_detect_windows,
                "preview_window": self.handle_preview_window,
                "detect_mt5_dir": self.handle_detect_mt5_dir,
                # Auth
                "login": self.handle_login,
                "verify_subscription": self.handle_verify_subscription,
                "change_password": self.handle_change_password,
                "admin_list_users": self.handle_admin_list_users,
                "admin_create_user": self.handle_admin_create_user,
                "admin_update_user": self.handle_admin_update_user,
                "admin_delete_user": self.handle_admin_delete_user,
            }.get(method)

            if handler:
                try:
                    await handler(req_id, params)
                except Exception as e:
                    logger.error(f"Handler error [{method}]: {e}", exc_info=True)
                    self._respond(req_id, error={"code": -1, "message": str(e)})
            else:
                self._respond(req_id, error={"code": -32601, "message": f"Unknown method: {method}"})

    # ── Main entry ──────────────────────────────────────────

    async def run(self):
        # Setup logging to stdout
        log_handler = StdoutLogHandler(self._emit_event)
        log_handler.setLevel(logging.DEBUG)
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        root.addHandler(log_handler)
        for logger_name in ("botocore", "boto3", "urllib3", "s3transfer"):
            logging.getLogger(logger_name).setLevel(logging.WARNING)

        # Also keep file handler — use DATA_DIR so logs persist in packaged mode
        fh = logging.FileHandler(str(DATA_DIR / "copy_trader.log"), encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        root.addHandler(fh)

        logger.info("Sidecar starting...")

        # Load config and send to frontend
        self.config = load_config()
        config_data = self._config_to_dict()
        self._emit_event("config", config_data)

        # Start MT5 data reader
        self.mt5_reader = MT5DataReader(
            self.config.mt5_files_dir,
            self._emit_event,
            getattr(self.config, "symbol_name", DEFAULT_SYMBOL),
        )
        mt5_task = asyncio.create_task(self.mt5_reader.start())
        self._periodic_task = asyncio.create_task(self._periodic_updates())

        # Run stdin command loop
        try:
            await self._stdin_loop()
        finally:
            # Cleanup
            if self.trader:
                self.trader.stop()
            if self.mt5_reader:
                self.mt5_reader.stop()
            mt5_task.cancel()
            if self._periodic_task:
                self._periodic_task.cancel()
            logger.info("Sidecar shutdown complete")


def main():
    sidecar = JsonRpcSidecar()
    asyncio.run(sidecar.run())


if __name__ == "__main__":
    main()
