"""
黃金跟單系統 - Pure asyncio MT5 JSON data reader (sidecar version)
Replaces gui/mt5_data_reader.py — no Qt dependency.
"""
import asyncio
import json
import time
import logging
from pathlib import Path
from typing import Callable, Optional

try:
    from copy_trader.config import DEFAULT_SYMBOL
except ModuleNotFoundError:
    from config import DEFAULT_SYMBOL

logger = logging.getLogger(__name__)


class MT5DataReader:
    """Polls MT5 JSON files and calls event callbacks.

    Intervals: price 2s, account/positions/orders 5s, history 10s.
    """

    def __init__(self, mt5_dir: str, emit: Callable[[str, object], None], symbol_name: str = DEFAULT_SYMBOL):
        self._mt5_dir = Path(mt5_dir)
        self._emit = emit  # emit(event_name, data)
        self._symbol_name = symbol_name or DEFAULT_SYMBOL
        self._running = False
        self._last_connection_state: Optional[bool] = None

    def set_mt5_dir(self, mt5_dir: str):
        self._mt5_dir = Path(mt5_dir)

    def set_symbol_name(self, symbol_name: str):
        self._symbol_name = symbol_name or DEFAULT_SYMBOL

    def _price_filenames(self) -> list[str]:
        candidates = []
        if self._symbol_name:
            candidates.append(f"{self._symbol_name}_price.json")
        candidates.append(f"{DEFAULT_SYMBOL}_price.json")
        return list(dict.fromkeys(candidates))

    def _read_json(self, filename: str) -> dict:
        filepath = self._mt5_dir / filename
        try:
            if filepath.exists():
                age = time.time() - filepath.stat().st_mtime
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["_file_age"] = age
                return data
        except (json.JSONDecodeError, PermissionError, OSError) as e:
            logger.debug(f"Failed to read {filename}: {e}")
        return {}

    async def _poll_price(self):
        while self._running:
            try:
                data = {}
                for filename in self._price_filenames():
                    data = self._read_json(filename)
                    if data:
                        break
                if data:
                    bid = data.get("bid", 0)
                    ask = data.get("ask", 0)
                    age = data.get("_file_age", 999)
                    if bid > 0 and ask > 0:
                        self._emit("price", {"bid": bid, "ask": ask})

                    connected = age < 30
                    if connected != self._last_connection_state:
                        self._last_connection_state = connected
                        self._emit("connection", {"connected": connected})
            except Exception as e:
                logger.debug(f"Price poll error: {e}")
            await asyncio.sleep(2)

    async def _poll_account(self):
        while self._running:
            try:
                account = self._read_json("account_info.json")
                if account:
                    account.pop("_file_age", None)
                    self._emit("account", account)

                positions_data = self._read_json("positions.json")
                positions = positions_data.get("positions", [])
                self._emit("positions", positions)

                orders_data = self._read_json("orders.json")
                orders = orders_data.get("orders", [])
                self._emit("orders", orders)
            except Exception as e:
                logger.debug(f"Account poll error: {e}")
            await asyncio.sleep(5)

    async def _poll_history(self):
        while self._running:
            try:
                trades_data = self._read_json("closed_trades.json")
                trades = trades_data.get("trades", [])
                # Enrich trades with source window info
                sources = self._read_signal_sources()
                if sources:
                    for trade in trades:
                        ticket = str(trade.get("position_id", trade.get("ticket", "")))
                        if ticket in sources:
                            trade["source_window"] = sources[ticket]
                trades = [self._normalize_trade(trade) for trade in trades]
                self._emit("trades", trades)
            except Exception as e:
                logger.debug(f"History poll error: {e}")
            await asyncio.sleep(10)

    def _read_signal_sources(self) -> dict:
        """Read signal source mapping file."""
        filepath = self._mt5_dir / "signal_sources.json"
        try:
            if filepath.exists():
                with open(filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, PermissionError, OSError):
            pass
        return {}

    def _normalize_trade(self, trade: dict) -> dict:
        """Fix direction/change fields that MT5 exit deals can invert."""
        try:
            entry_price = float(trade.get("entry_price", 0) or 0)
            exit_price = float(trade.get("exit_price", 0) or 0)
            profit = float(trade.get("profit", 0) or 0)
        except (TypeError, ValueError):
            return trade

        if entry_price <= 0 or exit_price <= 0 or entry_price == exit_price:
            return trade

        if profit >= 0:
            inferred_type = "buy" if exit_price > entry_price else "sell"
        else:
            inferred_type = "sell" if exit_price > entry_price else "buy"

        if inferred_type == "buy":
            change_percent = ((exit_price - entry_price) / entry_price) * 100.0
        else:
            change_percent = ((entry_price - exit_price) / entry_price) * 100.0

        trade["type"] = inferred_type
        trade["change_percent"] = round(change_percent, 2)
        return trade

    async def start(self):
        self._running = True
        logger.info("MT5DataReader started (asyncio)")
        await asyncio.gather(
            self._poll_price(),
            self._poll_account(),
            self._poll_history(),
        )

    def stop(self):
        self._running = False
