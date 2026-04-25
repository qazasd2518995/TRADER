"""
Client-side MT5 execution agent.

Run one copy on each user's computer. It polls the central hub and writes MT5
bridge commands locally through the existing TradeManager.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List

from copy_trader.config import DATA_DIR, DEFAULT_SYMBOL, load_config
from copy_trader.signal_parser.regex_parser import ParsedSignal
from copy_trader.trade_manager import OrderStatus, TradeManager

logger = logging.getLogger(__name__)


class HubClient:
    def __init__(self, hub_url: str, token: str = "", timeout: float = 8.0):
        self.hub_url = hub_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, path: str) -> Dict:
        req = urllib.request.Request(f"{self.hub_url}{path}", method="GET")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def health(self) -> Dict:
        return self._request("/health")

    def signals_after(self, after: int, limit: int = 50) -> List[Dict]:
        data = self._request(f"/signals?after={after}&limit={limit}")
        if not data.get("ok"):
            raise RuntimeError(data.get("error") or "hub_request_failed")
        return list(data.get("signals") or [])


def _load_state(path: Path) -> Dict:
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("failed to load state %s: %s", path, e)
    return {}


def _save_state(path: Path, state: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _parsed_signal_from_payload(payload: Dict) -> ParsedSignal:
    return ParsedSignal(
        is_valid=True,
        symbol=str(payload.get("symbol") or DEFAULT_SYMBOL),
        direction=str(payload.get("direction") or ""),
        entry_price=payload.get("entry_price"),
        is_market_order=bool(payload.get("is_market_order")),
        stop_loss=payload.get("stop_loss"),
        take_profit=list(payload.get("take_profit") or []),
        lot_size=payload.get("lot_size"),
        confidence=float(payload.get("confidence") or 0.95),
        raw_text_summary=str(payload.get("raw_text_summary") or ""),
        parse_method=str(payload.get("parse_method") or "hub"),
        error=str(payload.get("error") or ""),
    )


def _is_executable_signal(signal: ParsedSignal) -> bool:
    has_entry = signal.entry_price is not None or bool(signal.is_market_order)
    return bool(
        signal.is_valid
        and signal.direction in {"buy", "sell"}
        and has_entry
        and signal.stop_loss is not None
        and signal.take_profit
    )


class MT5ClientAgent:
    def __init__(self, hub: HubClient, state_file: Path, mt5_files_dir: str = "", replay: bool = False):
        self.hub = hub
        self.state_file = state_file
        self.config = load_config()
        if mt5_files_dir:
            self.config.mt5_files_dir = mt5_files_dir

        self.trade_manager = TradeManager(self.config.mt5_files_dir)
        self.trade_manager.default_lot_size = self.config.default_lot_size
        self.trade_manager.set_symbol_name(getattr(self.config, "symbol_name", DEFAULT_SYMBOL))
        self.trade_manager.partial_close_ratios = self.config.partial_close_ratios
        self.trade_manager.use_martingale = self.config.use_martingale
        self.trade_manager.martingale_multiplier = self.config.martingale_multiplier
        self.trade_manager.martingale_max_level = self.config.martingale_max_level
        self.trade_manager.martingale_lots = getattr(self.config, "martingale_lots", [])
        self.trade_manager.martingale_per_source = getattr(self.config, "martingale_per_source", False)
        self.trade_manager.martingale_source_lots = getattr(self.config, "martingale_source_lots", {})

        self.state = _load_state(state_file)
        if replay:
            self.state["last_seq"] = 0
            self.state["hub_url"] = self.hub.hub_url
            _save_state(self.state_file, self.state)
        else:
            health = self.hub.health()
            latest_seq = int(health.get("latest_seq") or 0)
            state_hub_url = str(self.state.get("hub_url") or "")
            state_last_seq = int(self.state.get("last_seq") or 0)
            if (
                "last_seq" not in self.state
                or state_hub_url != self.hub.hub_url
                or state_last_seq > latest_seq
            ):
                self.state["last_seq"] = latest_seq
            self.state["hub_url"] = self.hub.hub_url
            _save_state(self.state_file, self.state)

    @property
    def last_seq(self) -> int:
        return int(self.state.get("last_seq") or 0)

    def _mark_seq(self, seq: int) -> None:
        self.state["last_seq"] = max(self.last_seq, int(seq or 0))
        self.state["updated_at"] = time.time()
        _save_state(self.state_file, self.state)

    def run_cycle(self) -> int:
        count = 0
        for item in self.hub.signals_after(self.last_seq):
            seq = int(item.get("seq") or 0)
            if item.get("type") != "trade_signal":
                self._mark_seq(seq)
                continue

            payload = item.get("signal") or {}
            signal = _parsed_signal_from_payload(payload)
            if not _is_executable_signal(signal):
                logger.warning("invalid hub signal seq=%s: incomplete signal payload=%s", seq, payload)
                self._mark_seq(seq)
                continue

            source = str(item.get("source") or item.get("source_name") or "central")
            signal_id = self.trade_manager.submit_signal(
                signal,
                auto_execute=True,
                cancel_after_seconds=self.config.cancel_pending_after_seconds,
                cancel_if_price_beyond=self.config.cancel_if_price_beyond_percent,
                source_window=source,
            )
            order = self.trade_manager.get_order_status(signal_id)
            if order is not None and order.status == OrderStatus.FAILED:
                raise RuntimeError(f"failed to write MT5 command for hub seq={seq}")
            logger.info("submitted hub seq=%s as local signal %s: %s", seq, signal_id, signal)
            self._mark_seq(seq)
            count += 1
        return count

    def run_forever(self, interval: float = 1.0) -> None:
        self.trade_manager.start()
        logger.info("MT5 client agent running; last_seq=%s", self.last_seq)
        try:
            while True:
                try:
                    self.run_cycle()
                except (urllib.error.URLError, TimeoutError) as e:
                    logger.warning("hub connection failed: %s", e)
                except Exception as e:
                    logger.exception("agent cycle failed: %s", e)
                time.sleep(max(0.5, interval))
        except KeyboardInterrupt:
            logger.info("MT5 client agent stopped")
        finally:
            self.trade_manager.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local MT5 agent that executes central hub signals.")
    parser.add_argument("--hub-url", default=os.environ.get("COPY_TRADER_HUB_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--token", default=os.environ.get("COPY_TRADER_HUB_TOKEN", ""))
    parser.add_argument("--mt5-files-dir", default=os.environ.get("COPY_TRADER_MT5_FILES_DIR", ""))
    parser.add_argument("--state-file", default=os.environ.get("COPY_TRADER_AGENT_STATE", str(DATA_DIR / "central_client_state.json")))
    parser.add_argument("--interval", type=float, default=float(os.environ.get("COPY_TRADER_AGENT_INTERVAL", "1.0")))
    parser.add_argument("--replay", action="store_true", help="Process existing hub history. Default starts from the current latest seq.")
    parser.add_argument("--log-level", default=os.environ.get("COPY_TRADER_LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    hub = HubClient(args.hub_url, args.token)
    agent = MT5ClientAgent(hub, Path(args.state_file), mt5_files_dir=args.mt5_files_dir, replay=args.replay)
    agent.run_forever(interval=args.interval)


if __name__ == "__main__":
    main()
