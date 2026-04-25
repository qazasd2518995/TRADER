"""
Trade Manager for Copy Trading System
Handles order lifecycle, partial closes, and cancellation.
"""
import json
import time
import threading
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from pathlib import Path
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class OrderStatus(Enum):
    """Order lifecycle status."""
    PENDING = "pending"           # Signal received, awaiting execution
    SENT = "sent"                 # Command sent to MT5
    FILLED = "filled"             # Position opened
    PARTIAL_CLOSED = "partial"    # Some TP levels hit
    CLOSED = "closed"             # Fully closed
    CANCELLED = "cancelled"       # Cancelled before execution
    FAILED = "failed"             # Execution failed


@dataclass
class ManagedOrder:
    """Order with full lifecycle tracking."""
    signal_id: str
    signal: 'ParsedSignal'
    status: OrderStatus = OrderStatus.PENDING
    ticket: Optional[int] = None
    entry_time: Optional[float] = None
    entry_price: Optional[float] = None
    current_tp_index: int = 0
    remaining_volume: float = 0.0
    partial_closes: List[dict] = field(default_factory=list)
    last_known_profit: float = 0.0  # Last profit seen while position was open
    pending_partial_close: bool = False  # True while waiting for EA to confirm partial close
    pending_partial_volume: float = 0.0  # Volume we asked EA to close
    pending_partial_since: float = 0.0   # Timestamp when partial close was sent

    # Close confirmation: wait for closed_trades.json before deciding win/loss
    close_detected_at: Optional[float] = None  # Timestamp when position disappeared

    # Signal source window
    source_window: str = ""  # Display name of the window that produced this signal

    # Cancellation rules
    cancel_after_seconds: Optional[int] = None
    cancel_if_price_beyond: Optional[float] = None
    created_at: float = field(default_factory=time.time)


class TradeManager:
    """
    Manages trade lifecycle from signal to close.
    Integrates with MT5 via file-based bridge.
    Supports Martingale lot sizing.
    """

    def __init__(self, mt5_files_dir: str):
        """
        Initialize trade manager.

        Args:
            mt5_files_dir: Path to MT5 Files directory
        """
        self.mt5_files_dir = Path(mt5_files_dir)
        self.commands_file = self.mt5_files_dir / "commands.json"
        self.positions_file = self.mt5_files_dir / "positions.json"
        self.pending_orders_file = self.mt5_files_dir / "orders.json"

        self.orders: Dict[str, ManagedOrder] = {}
        self._lock = threading.Lock()
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None

        # Configuration
        self.default_lot_size = 0.01
        self.partial_close_ratios = [0.5, 0.3, 0.2]
        self.magic_number = 999999  # Unique ID for copy trader orders
        self.symbol_name = "XAUUSD"  # MT5 symbol name (broker-specific)
        self.price_file = self.mt5_files_dir / f"{self.symbol_name}_price.json"

        # Martingale Settings
        self.use_martingale = True
        self.martingale_multiplier = 2.0  # 2x after each loss
        self.martingale_max_level = 5     # Max 5 levels (0.01 -> 0.02 -> 0.04 -> 0.08 -> 0.16)
        self.martingale_lots: List[float] = []  # 自訂每層手數（優先使用）
        self.martingale_per_source = False  # True=per source, False=global
        self.martingale_source_lots: Dict[str, List[float]] = {}  # per-source lot tables

        # Global martingale state
        self.current_martingale_level = 0
        self.consecutive_losses = 0

        # Per-source martingale state: source_window -> {"level": int, "losses": int}
        self._source_martingale: Dict[str, dict] = {}

        # Martingale state persistence
        self._martingale_state_file = self.mt5_files_dir / "martingale_state.json"
        self._load_martingale_state()

        # Trade journal path (same as app.py uses)
        try:
            from config import DATA_DIR
            self._journal_file = DATA_DIR / "trade_journal.txt"
        except Exception:
            self._journal_file = Path("trade_journal.txt")

        # Signal source mapping: ticket -> source_window (for trade history enrichment)
        self._signal_sources_file = self.mt5_files_dir / "signal_sources.json"
        self._signal_sources: Dict[str, str] = self._load_signal_sources()

        logger.info(f"TradeManager initialized with MT5 dir: {mt5_files_dir}")
        logger.info(f"Martingale: {'ON' if self.use_martingale else 'OFF'} (x{self.martingale_multiplier})")

    def _write_journal(self, action: str, details: str = ""):
        """Write to trade journal for audit trail."""
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self._journal_file, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*60}\n[{ts}] {action}\n")
                if details:
                    f.write(f"  {details}\n")
        except Exception:
            pass

    def set_symbol_name(self, symbol_name: str):
        self.symbol_name = symbol_name or "XAUUSD"
        self.price_file = self.mt5_files_dir / f"{self.symbol_name}_price.json"

    def start(self):
        """Start the trade manager monitoring loop."""
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="TradeManagerMonitor"
        )
        self._monitor_thread.start()
        logger.info("Trade manager started")

    def stop(self):
        """Stop the trade manager."""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        logger.info("Trade manager stopped")

    def submit_signal(
        self,
        signal: 'ParsedSignal',
        auto_execute: bool = True,
        cancel_after_seconds: int = None,
        cancel_if_price_beyond: float = None,
        source_window: str = ""
    ) -> str:
        """
        Submit a new signal for execution.

        Args:
            signal: Parsed trading signal
            auto_execute: If True, execute immediately
            cancel_after_seconds: Cancel pending order after this time
            cancel_if_price_beyond: Cancel if price moves beyond this percent from entry
            source_window: Display name of the window that produced this signal

        Returns:
            Signal ID for tracking
        """
        signal_id = f"copy_{int(time.time() * 1000)}"

        order = ManagedOrder(
            signal_id=signal_id,
            signal=signal,
            source_window=source_window,
            cancel_after_seconds=cancel_after_seconds,
            cancel_if_price_beyond=cancel_if_price_beyond,
            remaining_volume=signal.lot_size or self.default_lot_size
        )

        with self._lock:
            self.orders[signal_id] = order

        # Save source_window mapping immediately (not just after fill)
        # so cancel keywords can match pending orders to their source
        if source_window:
            self._signal_sources[signal_id] = source_window

        logger.info(f"Signal submitted: {signal_id} - {signal}")

        if auto_execute:
            self._execute_order(signal_id)

        return signal_id

    def cancel_order(self, signal_id: str, reason: str = "user_cancelled") -> bool:
        """
        Cancel a pending or close an open order.
        NOTE: Cancelled orders do NOT affect martingale level.

        Args:
            signal_id: The signal ID to cancel
            reason: Reason for cancellation

        Returns:
            True if successful
        """
        with self._lock:
            order = self.orders.get(signal_id)
            if not order:
                return False

            if order.status in [OrderStatus.PENDING, OrderStatus.SENT]:
                order.status = OrderStatus.CANCELLED
                # Delete from MT5 if we have the ticket
                if order.ticket:
                    self._delete_pending_order(order.ticket)
                self.on_order_cancelled(signal_id)
                logger.info(f"Cancelled order {signal_id} (ticket: {order.ticket}): {reason}")
                return True

            if order.status in [OrderStatus.FILLED, OrderStatus.PARTIAL_CLOSED]:
                # Need to close the position - this WILL affect martingale
                # based on whether it's currently in profit or loss
                current_profit = self._get_position_profit(order.ticket)
                success = self._close_position(order.ticket)
                if success:
                    order.status = OrderStatus.CLOSED
                    # Manual close counts as win/loss based on current P/L
                    is_win = current_profit >= 0
                    self.on_trade_result(is_win, signal_id, source_window=order.source_window)
                    logger.info(f"Closed position {signal_id}: {reason} ({'WIN' if is_win else 'LOSS'})")
                return success

        return False

    def _get_position_profit(self, ticket: int) -> float:
        """Get current profit of an open position."""
        positions = self._get_positions()
        for pos in positions:
            if pos.get('ticket') == ticket:
                return pos.get('profit', 0)
        return 0

    def get_order_status(self, signal_id: str) -> Optional[ManagedOrder]:
        """Get the current status of an order."""
        with self._lock:
            return self.orders.get(signal_id)

    def get_all_orders(self) -> List[ManagedOrder]:
        """Get all managed orders."""
        with self._lock:
            return list(self.orders.values())

    def get_martingale_lot_size(self, source_window: str = "") -> float:
        """
        Calculate current lot size based on Martingale level.

        Args:
            source_window: If martingale_per_source=True, use this source's level.
        """
        if not self.use_martingale:
            return self.default_lot_size

        # Get the right level (per-source or global)
        if self.martingale_per_source and source_window:
            state = self._source_martingale.get(source_window, {"level": 0, "losses": 0})
            level = state["level"]
        else:
            level = self.current_martingale_level

        src_tag = f" [{source_window}]" if self.martingale_per_source and source_window else ""

        # Per-source lot table (highest priority when in per_source mode)
        source_lots = self.martingale_source_lots.get(source_window, []) if source_window else []
        # Fall back to global lot table
        lots = source_lots or self.martingale_lots

        if lots:
            max_level = len(lots) - 1
            level = min(level, max_level)
            lot = lots[level]
            lot = round(lot, 2)
            tag = "(各群自訂)" if source_lots else "(全域自訂)"
            logger.info(f"Martingale Level {level}{src_tag}: Lot size = {lot} {tag}")
            return lot

        # 退回公式計算
        level = min(level, self.martingale_max_level)
        lot = self.default_lot_size * (self.martingale_multiplier ** level)
        lot = round(lot, 2)

        logger.info(f"Martingale Level {level}{src_tag}: Lot size = {lot}")
        return lot

    def on_trade_result(self, is_win: bool, signal_id: str = None, source_window: str = ""):
        """
        Update martingale level based on trade result.

        Args:
            is_win: True if trade was profitable, False if loss
            signal_id: Optional signal ID for logging
            source_window: Source window for per-source martingale
        """
        if not self.use_martingale:
            return

        src_tag = f" [{source_window}]" if self.martingale_per_source and source_window else ""
        max_level = (len(self.martingale_lots) - 1) if self.martingale_lots else self.martingale_max_level

        if self.martingale_per_source and source_window:
            # Per-source mode
            state = self._source_martingale.get(source_window, {"level": 0, "losses": 0})
            if is_win:
                if state["level"] > 0:
                    logger.info(f"WIN{src_tag}: Resetting martingale from level {state['level']} to 0")
                state["level"] = 0
                state["losses"] = 0
            else:
                state["losses"] += 1
                if state["level"] < max_level:
                    state["level"] += 1
                    logger.info(f"LOSS{src_tag}: Martingale level → {state['level']}")
                else:
                    logger.warning(f"LOSS{src_tag}: Max level {max_level}, resetting")
                    state["level"] = 0
                    state["losses"] = 0
            self._source_martingale[source_window] = state
        else:
            # Global mode
            if is_win:
                if self.current_martingale_level > 0:
                    logger.info(f"WIN: Resetting martingale from level {self.current_martingale_level} to 0")
                self.current_martingale_level = 0
                self.consecutive_losses = 0
            else:
                self.consecutive_losses += 1
                if self.current_martingale_level < max_level:
                    self.current_martingale_level += 1
                    logger.info(f"LOSS: Martingale level → {self.current_martingale_level}")
                else:
                    logger.warning(f"LOSS: Max level {max_level}, resetting")
                    self.current_martingale_level = 0
                    self.consecutive_losses = 0

        self._save_martingale_state()

        # Journal
        result_str = "WIN" if is_win else "LOSS"
        mg_level = self.current_martingale_level
        if self.martingale_per_source and source_window:
            mg_level = self._source_martingale.get(source_window, {}).get("level", 0)
        self._write_journal(
            f"TRADE_CLOSED_{result_str}",
            f"signal_id={signal_id} | 來源={source_window} | 馬丁層級={mg_level} "
            f"| 下一手數={self.get_martingale_lot_size(source_window)}"
        )

    def on_order_cancelled(self, signal_id: str):
        """
        Handle order cancellation - does NOT affect martingale level.
        Cancelled orders don't count as wins or losses.

        Args:
            signal_id: The cancelled order's signal ID
        """
        logger.info(f"Order {signal_id} cancelled - martingale level unchanged ({self.current_martingale_level})")

    def reset_martingale(self):
        """Reset martingale to base level (manual reset)."""
        logger.info(f"Manual martingale reset from level {self.current_martingale_level} to 0")
        self.current_martingale_level = 0
        self.consecutive_losses = 0
        self._save_martingale_state()

    def _load_martingale_state(self):
        """Load martingale state from disk to survive restarts."""
        try:
            if self._martingale_state_file.exists():
                with open(self._martingale_state_file, 'r') as f:
                    data = json.load(f)
                self.current_martingale_level = data.get('level', 0)
                self.consecutive_losses = data.get('consecutive_losses', 0)
                self._source_martingale = data.get('per_source', {})
                logger.info(
                    f"Restored martingale state: global level={self.current_martingale_level}, "
                    f"per_source={len(self._source_martingale)} sources"
                )
        except Exception as e:
            logger.warning(f"Failed to load martingale state: {e}")

    def _save_martingale_state(self):
        """Save martingale state to disk."""
        try:
            data = {
                'level': self.current_martingale_level,
                'consecutive_losses': self.consecutive_losses,
                'per_source': self._source_martingale,
                'updated': time.strftime('%Y-%m-%d %H:%M:%S')
            }
            with open(self._martingale_state_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"Failed to save martingale state: {e}")

    def _load_signal_sources(self) -> Dict[str, str]:
        """Load signal source mapping from disk."""
        try:
            if self._signal_sources_file.exists():
                with open(self._signal_sources_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load signal sources: {e}")
        return {}

    def _save_signal_sources(self):
        """Save signal source mapping to disk."""
        try:
            # Keep only last 200 entries to prevent unbounded growth
            if len(self._signal_sources) > 200:
                keys = sorted(self._signal_sources.keys())
                for k in keys[:-200]:
                    del self._signal_sources[k]
            with open(self._signal_sources_file, 'w', encoding='utf-8') as f:
                json.dump(self._signal_sources, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to save signal sources: {e}")

    def get_signal_sources(self) -> Dict[str, str]:
        """Get the ticket -> source_window mapping (for trade history enrichment)."""
        return self._signal_sources

    def _execute_order(self, signal_id: str) -> bool:
        """Execute an order by writing to commands.json."""
        with self._lock:
            order = self.orders.get(signal_id)
            if not order:
                return False

        signal = order.signal

        # Use martingale lot size if enabled, otherwise use signal's lot size or default
        if self.use_martingale:
            lot_size = self.get_martingale_lot_size(order.source_window)
        else:
            lot_size = signal.lot_size or self.default_lot_size

        tps = signal.take_profit or []
        # Check if lot size is large enough for partial closes (need at least 0.02 to split)
        can_partial_close = lot_size >= 0.02 and len(tps) > 1
        if can_partial_close:
            # Multiple TPs + enough volume: set MT5 TP to the LAST level (safety net).
            # Intermediate TPs are managed by _check_partial_tp_hits.
            mt5_tp = tps[-1]
            logger.info(f"Multiple TPs with {lot_size} lots — partial close enabled, MT5 TP set to last: {mt5_tp}")
        elif len(tps) >= 1:
            # Single TP, or lot too small for partial close: use first TP, close all at once
            mt5_tp = tps[0]
            if len(tps) > 1:
                logger.info(f"Multiple TPs but lot {lot_size} too small for partial close — using TP1: {mt5_tp}")
        else:
            mt5_tp = None

        # Build command for MT5 bridge
        symbol = self.symbol_name or signal.symbol or "XAUUSD"
        command = {
            "action": signal.direction,
            "symbol": symbol,
            "lot_size": lot_size,
            "magic_number": self.magic_number,
            "comment": f"copy_{signal_id}",
            "trade_id": signal_id
        }

        # Only include SL/TP if they have values (EA can't handle null)
        if signal.stop_loss is not None:
            command["stop_loss"] = signal.stop_loss
        if mt5_tp is not None:
            command["take_profit"] = mt5_tp

        # Add entry price if specified (for pending orders)
        if signal.entry_price:
            command["price"] = signal.entry_price

        # Write command to file
        success = self._write_command(command)

        with self._lock:
            if success:
                order.status = OrderStatus.SENT
                logger.info(f"Order sent to MT5: {signal_id}")
            else:
                order.status = OrderStatus.FAILED
                logger.error(f"Failed to send order: {signal_id}")

        return success

    def _write_command(self, command: dict) -> bool:
        """Write a command to the MT5 commands file.

        Waits for previous command to be consumed by EA (file contains '{}')
        before writing, to prevent command overwrites.
        """
        try:
            # Wait up to 5 seconds for EA to consume the previous command
            for _ in range(50):
                try:
                    if self.commands_file.exists():
                        content = self.commands_file.read_text().strip()
                        if content in ('{}', ''):
                            break  # Previous command consumed, safe to write
                    else:
                        break  # File doesn't exist yet, safe to write
                except (PermissionError, OSError):
                    pass  # File locked by EA, keep waiting
                time.sleep(0.1)

            with open(self.commands_file, 'w') as f:
                json.dump(command, f, separators=(',', ':'))
            logger.debug(f"Command written: {command}")
            return True
        except Exception as e:
            logger.error(f"Failed to write command: {e}")
            return False

    def _read_json_file(self, filepath: Path, retries: int = 3, delay: float = 0.1):
        """
        Read a JSON file with retry logic for Windows file locking.
        MT5 EA locks files during writes, causing PermissionError.
        """
        for attempt in range(retries):
            try:
                if filepath.exists():
                    with open(filepath, 'r') as f:
                        return json.load(f)
            except PermissionError:
                if attempt < retries - 1:
                    time.sleep(delay * (2 ** attempt))  # Exponential backoff
                else:
                    logger.debug(f"File locked after {retries} retries: {filepath.name}")
            except json.JSONDecodeError:
                # Corrupted/partial write — retry once then give up
                if attempt == 0:
                    time.sleep(delay)
                else:
                    logger.debug(f"JSON decode error after {attempt + 1} attempts: {filepath.name}")
                    break
            except Exception as e:
                logger.error(f"Failed to read {filepath.name}: {e}")
                break
        return None

    def _get_current_price(self) -> Optional[float]:
        """Get current market price from MT5."""
        data = self._read_json_file(self.price_file)
        if not data and self.price_file.name != "XAUUSD_price.json":
            data = self._read_json_file(self.mt5_files_dir / "XAUUSD_price.json")
        if data:
            bid = data.get('bid', 0)
            ask = data.get('ask', 0)
            return (bid + ask) / 2
        return None

    def _get_positions(self, allow_none: bool = False):
        """
        Get current positions from MT5.

        Args:
            allow_none: If True, return None on read failure (vs empty list).
                        Callers that need to distinguish "no positions" from
                        "failed to read" should use allow_none=True.
        """
        data = self._read_json_file(self.positions_file)
        if data:
            return data.get('positions', [])
        if data is None and allow_none:
            return None
        return []

    def _get_pending_orders(self) -> List[dict]:
        """Get current pending orders from MT5."""
        data = self._read_json_file(self.pending_orders_file)
        if data:
            return data.get('orders', [])
        return []

    def _close_position(self, ticket: int, volume: float = None) -> bool:
        """Close a position (full or partial)."""
        command = {
            "action": "close",
            "ticket": ticket
        }
        if volume:
            command["close_volume"] = volume

        return self._write_command(command)

    def _modify_position(self, ticket: int, sl: float = None, tp: float = None) -> bool:
        """Modify a position's SL/TP."""
        command = {
            "action": "modify",
            "ticket": ticket
        }
        if sl is not None:
            command["stop_loss"] = sl
        if tp is not None:
            command["take_profit"] = tp

        return self._write_command(command)

    def _monitor_loop(self):
        """Background monitoring loop."""
        last_cleanup = time.time()
        while self._running:
            try:
                self._check_order_fills()
                self._check_closed_positions()  # Check for wins/losses
                self._check_partial_tp_hits()
                self._check_cancellation_conditions()

                # Periodically clean up finished orders to prevent memory growth
                now = time.time()
                if now - last_cleanup > 300:  # Every 5 minutes
                    self._cleanup_finished_orders()
                    last_cleanup = now

                time.sleep(1)
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
                time.sleep(5)

    def _cleanup_finished_orders(self):
        """Remove old closed/cancelled/failed orders to prevent memory growth."""
        cutoff = time.time() - 3600  # Keep for 1 hour after completion
        with self._lock:
            to_remove = [
                sid for sid, order in self.orders.items()
                if order.status in (OrderStatus.CLOSED, OrderStatus.CANCELLED, OrderStatus.FAILED)
                and order.created_at < cutoff
            ]
            for sid in to_remove:
                del self.orders[sid]
            if to_remove:
                logger.info(f"Cleaned up {len(to_remove)} finished orders")

    # Grace period (seconds) to wait for closed_trades.json after position disappears
    CLOSE_CONFIRM_TIMEOUT = 5

    def _check_closed_positions(self):
        """
        Check for closed positions and update martingale level.

        Two-phase approach for accurate profit detection:
        1. When a position disappears from positions.json, mark it as
           "pending confirmation" and start a grace period.
        2. During the grace period, repeatedly try to read the actual
           closing profit from closed_trades.json (written by EA).
        3. Only after the grace period expires, fall back to last_known_profit.

        This prevents stale profit data from causing wrong martingale decisions
        when the price is near the break-even point.
        """
        positions = self._get_positions(allow_none=True)
        if positions is None:
            # File read failed (locked by MT5) — skip this cycle to avoid
            # falsely detecting all positions as closed
            return
        position_tickets = {p.get('ticket') for p in positions}

        now = time.time()

        # Update last_known_profit for all tracked open positions
        with self._lock:
            for signal_id, order in self.orders.items():
                if order.status in [OrderStatus.FILLED, OrderStatus.PARTIAL_CLOSED] and order.ticket:
                    for pos in positions:
                        if pos.get('ticket') == order.ticket:
                            order.last_known_profit = pos.get('profit', 0)
                            break

        newly_confirmed = []

        with self._lock:
            for signal_id, order in list(self.orders.items()):
                if order.status not in [OrderStatus.FILLED, OrderStatus.PARTIAL_CLOSED]:
                    continue

                if not order.ticket or order.ticket in position_tickets:
                    # Position still open — reset any pending close detection
                    order.close_detected_at = None
                    continue

                # --- Position disappeared from positions.json ---

                # Phase 1: First detection — start grace period
                if order.close_detected_at is None:
                    order.close_detected_at = now
                    logger.info(
                        f"Position disappeared: {signal_id} (ticket {order.ticket}) "
                        f"— waiting up to {self.CLOSE_CONFIRM_TIMEOUT}s for closed_trades.json"
                    )

                # Phase 2: Try to get actual profit from closed_trades.json
                profit = self._get_closed_trade_profit(order.ticket)

                if profit is not None:
                    # Got real profit from EA — use it
                    order.status = OrderStatus.CLOSED
                    newly_confirmed.append((signal_id, profit, order.source_window))
                    logger.info(
                        f"Position closed (confirmed by EA): {signal_id} "
                        f"(ticket {order.ticket}) — actual profit: {profit:.2f}"
                    )
                elif now - order.close_detected_at >= self.CLOSE_CONFIRM_TIMEOUT:
                    # Grace period expired — fall back to last known profit
                    profit = order.last_known_profit
                    order.status = OrderStatus.CLOSED
                    newly_confirmed.append((signal_id, profit, order.source_window))
                    logger.warning(
                        f"Position closed (timeout fallback): {signal_id} "
                        f"(ticket {order.ticket}) — using last known profit: {profit:.2f}"
                    )
                # else: still waiting — will retry next cycle

        # Update martingale for EACH trade individually
        # (batch net-sum was incorrect: +100/-500/+100 = loss, but should be 2 wins + 1 loss)
        for signal_id, profit, source_window in newly_confirmed:
            is_win = profit >= 0
            logger.info(
                f"Trade closed: {signal_id}, profit: {profit:.2f} → {'WIN' if is_win else 'LOSS'}"
            )
            self.on_trade_result(is_win, signal_id=signal_id, source_window=source_window)

    def _get_closed_trade_profit(self, ticket: int) -> Optional[float]:
        """
        Get the profit of a closed trade from MT5.

        Args:
            ticket: The position ticket number (from positions.json)

        Returns:
            Profit amount (negative = loss), or None if not found
        """
        closed_trades_file = self.mt5_files_dir / "closed_trades.json"

        data = self._read_json_file(closed_trades_file)
        if data:
            trades = data.get('trades', [])

            # Match by position_id first (correct field from EA)
            for trade in trades:
                if trade.get('position_id') == ticket:
                    profit = float(trade.get('profit', 0))
                    logger.info(f"Found closed trade profit by position_id: ticket={ticket}, profit={profit}")
                    return profit

            # Fallback: match by deal ticket (legacy)
            for trade in trades:
                if trade.get('ticket') == ticket:
                    profit = float(trade.get('profit', 0))
                    logger.info(f"Found closed trade profit by deal ticket: ticket={ticket}, profit={profit}")
                    return profit

        # Could not determine profit - return None so caller can decide
        logger.warning(f"Could not find profit for ticket {ticket} in closed_trades.json")
        return None

    def _check_order_fills(self):
        """Check if sent orders have been filled or are still pending on MT5."""
        positions = self._get_positions(allow_none=True)
        if positions is None:
            return  # File locked, skip this cycle
        pending_orders = self._get_pending_orders()

        with self._lock:
            for signal_id, order in self.orders.items():
                if order.status != OrderStatus.SENT:
                    continue

                # Check if filled (appeared in positions)
                filled = False
                for pos in positions:
                    comment = pos.get('comment', '')
                    if f"copy_{signal_id}" in comment:
                        order.status = OrderStatus.FILLED
                        order.ticket = pos.get('ticket')
                        order.entry_price = pos.get('price_open')
                        order.entry_time = time.time()
                        order.remaining_volume = pos.get('volume', 0)
                        logger.info(f"Order filled: {signal_id} @ {order.entry_price}")
                        self._write_journal(
                            "ORDER_FILLED",
                            f"signal_id={signal_id} | ticket={order.ticket} "
                            f"| 成交價={order.entry_price} | 手數={order.remaining_volume} "
                            f"| 來源={order.source_window}"
                        )
                        # Save source window mapping for trade history
                        if order.source_window and order.ticket:
                            self._signal_sources[str(order.ticket)] = order.source_window
                            self._save_signal_sources()
                        filled = True
                        break

                # If not filled, check pending orders to get MT5 ticket
                if not filled and not order.ticket:
                    for po in pending_orders:
                        comment = po.get('comment', '')
                        if f"copy_{signal_id}" in comment:
                            order.ticket = po.get('ticket')
                            logger.debug(f"Pending order ticket found: {signal_id} -> {order.ticket}")
                            # Map ticket → source immediately so cancel-by-source works
                            # (don't wait until fill — pending orders need source isolation too)
                            if order.source_window and order.ticket:
                                self._signal_sources[str(order.ticket)] = order.source_window
                                self._save_signal_sources()
                            break

    # Max time (seconds) to wait for EA to confirm a partial close before retrying
    PARTIAL_CLOSE_TIMEOUT = 10

    def _check_partial_tp_hits(self):
        """
        Check for partial TP hits and execute partial closes.

        Uses a two-phase approach:
        1. Send partial close command to EA
        2. Wait for MT5 position volume to actually decrease before advancing to next TP

        This prevents out-of-sync state if EA fails to process the command.
        """
        current_price = self._get_current_price()
        if not current_price:
            return

        # Read actual MT5 positions to verify volume changes
        positions = self._get_positions(allow_none=True)
        if positions is None:
            return
        position_map = {p.get('ticket'): p for p in positions}

        with self._lock:
            for signal_id, order in self.orders.items():
                if order.status not in [OrderStatus.FILLED, OrderStatus.PARTIAL_CLOSED]:
                    continue

                signal = order.signal
                tps = signal.take_profit or []

                if order.current_tp_index >= len(tps):
                    continue

                # --- Phase 2: If a partial close is pending, check if EA confirmed it ---
                if order.pending_partial_close:
                    mt5_pos = position_map.get(order.ticket)
                    if mt5_pos:
                        actual_volume = mt5_pos.get('volume', 0)
                        expected = order.remaining_volume - order.pending_partial_volume
                        # Allow 0.005 tolerance for rounding
                        if actual_volume <= expected + 0.005:
                            # EA confirmed: volume decreased
                            order.remaining_volume = round(actual_volume, 2)
                            order.current_tp_index += 1
                            order.status = OrderStatus.PARTIAL_CLOSED
                            order.pending_partial_close = False
                            order.partial_closes.append({
                                "tp_index": order.current_tp_index - 1,
                                "volume": order.pending_partial_volume,
                                "timestamp": time.time()
                            })
                            logger.info(
                                f"Partial close CONFIRMED: {order.signal_id} "
                                f"TP{order.current_tp_index} "
                                f"closed={order.pending_partial_volume} "
                                f"remaining={order.remaining_volume}"
                            )
                            continue
                    # Check timeout — retry if EA didn't process in time
                    if time.time() - order.pending_partial_since > self.PARTIAL_CLOSE_TIMEOUT:
                        logger.warning(
                            f"Partial close TIMEOUT: {order.signal_id} "
                            f"(EA didn't confirm in {self.PARTIAL_CLOSE_TIMEOUT}s), retrying"
                        )
                        order.pending_partial_close = False
                        # Fall through to retry below
                    else:
                        continue  # Still waiting

                # --- Phase 1: Check if price hit current TP and send partial close ---
                # Only process intermediate TPs (last TP is handled by MT5 safety net)
                if order.current_tp_index >= len(tps) - 1:
                    continue

                current_tp = tps[order.current_tp_index]
                hit = False
                if signal.direction == 'buy' and current_price >= current_tp:
                    hit = True
                elif signal.direction == 'sell' and current_price <= current_tp:
                    hit = True

                if hit:
                    self._execute_partial_close(order)

    def _execute_partial_close(self, order: ManagedOrder):
        """
        Send a partial close command to EA.

        Does NOT update order state immediately — waits for EA confirmation
        in _check_partial_tp_hits phase 2.
        """
        if order.current_tp_index >= len(self.partial_close_ratios):
            return

        close_ratio = self.partial_close_ratios[order.current_tp_index]
        close_volume = round(order.remaining_volume * close_ratio, 2)

        # Minimum lot check: broker minimum is typically 0.01
        if close_volume < 0.01:
            logger.info(
                f"Partial close skipped (volume {close_volume} < 0.01): "
                f"{order.signal_id} — lot too small for partial close"
            )
            return

        # Don't close more than what's left minus minimum lot
        # (need at least 0.01 remaining for the final TP)
        if order.remaining_volume - close_volume < 0.01:
            close_volume = round(order.remaining_volume - 0.01, 2)
            if close_volume < 0.01:
                logger.info(
                    f"Partial close skipped: {order.signal_id} "
                    f"— remaining {order.remaining_volume} too small to split"
                )
                return

        # Mark as pending BEFORE sending command to prevent monitor thread
        # from seeing the volume change and double-processing
        order.pending_partial_close = True
        order.pending_partial_volume = close_volume
        order.pending_partial_since = time.time()

        # Send close command to EA
        success = self._close_position(order.ticket, close_volume)

        if success:
            logger.info(
                f"Partial close SENT: {order.signal_id} "
                f"TP{order.current_tp_index + 1} vol={close_volume} "
                f"(waiting for EA confirmation)"
            )
        else:
            # Command failed — clear pending flags
            order.pending_partial_close = False
            order.pending_partial_volume = 0.0
            order.pending_partial_since = 0.0
            logger.error(f"Partial close FAILED to send: {order.signal_id}")

    def _delete_pending_order(self, ticket: int) -> bool:
        """Send a delete command to MT5 to remove a pending order."""
        command = {
            "action": "delete",
            "ticket": ticket
        }
        return self._write_command(command)

    def _check_cancellation_conditions(self):
        """Check time and price-based cancellation conditions."""
        current_time = time.time()
        current_price = self._get_current_price()

        with self._lock:
            for signal_id, order in list(self.orders.items()):
                if order.status not in [OrderStatus.PENDING, OrderStatus.SENT]:
                    continue

                should_cancel = False
                cancel_reason = ""

                # Time-based cancellation
                if order.cancel_after_seconds:
                    elapsed = current_time - order.created_at
                    if elapsed > order.cancel_after_seconds:
                        should_cancel = True
                        cancel_reason = f"timeout ({elapsed:.0f}s)"

                # Price-based cancellation
                if (
                    not should_cancel
                    and order.cancel_if_price_beyond
                    and current_price
                    and order.signal.entry_price
                ):
                    signal = order.signal
                    percent = float(order.cancel_if_price_beyond)
                    entry_price = float(signal.entry_price)
                    upper_bound = entry_price * (1 + percent / 100.0)
                    lower_bound = entry_price * (1 - percent / 100.0)

                    if signal.direction == 'buy' and current_price > upper_bound:
                        should_cancel = True
                        cancel_reason = (
                            f"price {current_price} beyond +{percent}% "
                            f"(>{upper_bound:.3f}) from entry {entry_price}"
                        )
                    elif signal.direction == 'sell' and current_price < lower_bound:
                        should_cancel = True
                        cancel_reason = (
                            f"price {current_price} beyond -{percent}% "
                            f"(<{lower_bound:.3f}) from entry {entry_price}"
                        )

                if should_cancel:
                    order.status = OrderStatus.CANCELLED
                    if order.ticket:
                        self._delete_pending_order(order.ticket)
                        logger.info(f"Order {signal_id} auto-cancelled + MT5 delete sent (ticket {order.ticket}): {cancel_reason}")
                    else:
                        logger.warning(f"Order {signal_id} auto-cancelled but no MT5 ticket found: {cancel_reason}")
                    self.on_order_cancelled(signal_id)
                    self._write_journal(
                        "ORDER_AUTO_CANCELLED",
                        f"signal_id={signal_id} | ticket={order.ticket} | 原因={cancel_reason} "
                        f"| 信號={order.signal} | 來源={order.source_window}"
                    )
