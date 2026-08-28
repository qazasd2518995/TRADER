"""
Trade Manager for Copy Trading System
Handles order lifecycle, partial closes, and cancellation.
"""
import calendar
import json
import re
import time
import threading
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict, Tuple
from pathlib import Path
from enum import Enum

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


class CancelState(Enum):
    """Exact pending-order cancellation lifecycle."""
    NONE = "none"
    REQUESTED = "requested"
    COMMAND_SENT = "command_sent"
    MT5_CONFIRMED = "mt5_confirmed"
    FAILED_RETRY = "failed_retry"
    ALREADY_FILLED = "already_filled"


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
    initial_volume: float = 0.0  # 成交當下的手數；分批比例以「這個」為基準(佔原始)
    partial_plan: List[float] = field(default_factory=list)  # 各中間 TP 要平的手數(預先算好)
    partial_closes: List[dict] = field(default_factory=list)
    last_known_profit: float = 0.0  # Last profit seen while position was open
    pending_partial_close: bool = False  # True while waiting for EA to confirm partial close
    pending_partial_volume: float = 0.0  # Volume we asked EA to close
    pending_partial_since: float = 0.0   # Timestamp when partial close was sent

    # Close confirmation: wait for closed_trades.json before deciding win/loss
    close_detected_at: Optional[float] = None  # Timestamp when position disappeared

    # 掛單從 MT5 消失（被手動刪或券商撤）的偵測時間，用來過濾「成交瞬間」的空窗
    vanish_detected_at: Optional[float] = None

    # LINE 引用撤單只送 pending-delete。送出後仍維持 SENT，直到 MT5 對帳
    # 確認掛單消失；若同時已成交，fill detector 會接管而不會平倉。
    cancel_requested: bool = False
    cancel_delete_sent: bool = False
    cancel_reason: str = ""
    cancel_state: CancelState = CancelState.NONE
    cancel_sent_at: float = 0.0
    cancel_attempts: int = 0
    cancel_error: str = ""
    cancel_last_result: str = ""

    # 保本移損模式：已經因為觸及第幾關而推過停損（0=還沒推過）
    sl_trail_index: int = 0
    trailed_sl: Optional[float] = None   # 目前已送出的停損價，避免重複送同一張改單

    # Signal source window
    source_window: str = ""  # Display name of the window that produced this signal

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
        self.martingale_max_level = 5     # 關卡數(總關數): 5關 => 手數 base×2^0..2^4, 最大 base×16
        self.martingale_lots: List[float] = []  # 自訂每層手數（優先使用）
        self.martingale_per_source = False  # True=per source, False=global
        self.martingale_source_lots: Dict[str, List[float]] = {}  # per-source lot tables

        # 每個訊號來源(LINE 群組)各自的下單模式。key 必須等於訊號帶的 source_window。
        #   {"昊哥": {"enabled": True, "mode": "martingale",
        #             "base_lot": 1.0, "multiplier": 2.0, "max_level": 5, "lots": []},
        #    "YU":  {"enabled": True, "mode": "flat", "base_lot": 0.5}}
        # 沒設定的來源回退到上面的全域值。mode="flat" = 均注，每次固定手數、不進關。
        self.source_profiles: Dict[str, dict] = {}

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
            from copy_trader.config import DATA_DIR
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
        source_window: str = "",
        signal_id: str = "",
    ) -> str:
        """
        Submit a new signal for execution.

        Args:
            signal: Parsed trading signal
            auto_execute: If True, execute immediately
            source_window: Display name of the window that produced this signal
            signal_id: Stable LINE-derived execution ID. Empty uses a local ID.

        Returns:
            Signal ID for tracking
        """
        # 多 TP 一律重排成「由近到遠」（買遞增 / 賣遞減）。
        # 分批平倉靠這個順序：MT5 的 TP 設在 tps[-1] 當尾段安全網，中間關由
        # _check_partial_tp_hits 逐一處理。順序反了的話（賣單常見），MT5 的 TP
        # 會落在最近的止盈上，整倉在第一個目標就平掉，分批完全失效。
        # 這裡再排一次是防線：Hub 上舊訊號的順序是壞的，訊號中心也可能是舊版。
        try:
            from copy_trader.signal_parser.regex_parser import order_take_profits

            fixed = order_take_profits(signal.direction, signal.take_profit or [])
            if fixed != (signal.take_profit or []):
                logger.info(
                    "重排 %s 單的止盈順序（由近到遠）：%s → %s",
                    signal.direction, signal.take_profit, fixed,
                )
                signal.take_profit = fixed
        except Exception as exc:
            logger.warning("止盈排序失敗，沿用原順序：%s", exc)

        with self._lock:
            if signal_id:
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", signal_id):
                    raise ValueError("signal_id must be 1-24 safe ASCII characters")
                existing = self.orders.get(signal_id)
                if existing is not None:
                    retry_failed = auto_execute and existing.status is OrderStatus.FAILED and existing.ticket is None
                    logger.info("LINE execution %s is already tracked (status=%s)", signal_id, existing.status.value)
                    if not retry_failed:
                        return signal_id
            else:
                # id 必須在鎖內產生並確定唯一。毫秒解析度碰撞時往後找。
                ms = int(time.time() * 1000)
                while f"copy_{ms}" in self.orders or f"copy_{ms}" in self._signal_sources:
                    ms += 1
                signal_id = f"copy_{ms}"

            if signal_id in self.orders:
                order = self.orders[signal_id]
                retry_existing = True
            else:
                retry_existing = False
            if not retry_existing:
                order = ManagedOrder(
                    signal_id=signal_id,
                    signal=signal,
                    source_window=source_window,
                    remaining_volume=signal.lot_size or self.default_lot_size,
                )
                self.orders[signal_id] = order

        # Save source_window mapping immediately for reports and restart adoption.
        if source_window:
            self._signal_sources[signal_id] = source_window
            # 立刻寫檔：原本只在成交時才存，導致新群組要等到第一筆成交
            # 才會出現在「訊號來源設定」清單裡（沒開 MT5 就永遠不出現）。
            self._save_signal_sources()

        logger.info(f"Signal submitted: {signal_id} - {signal}")

        if auto_execute:
            self._execute_order(signal_id)

        return signal_id

    def cancel_pending_order(self, signal_id: str, reason: str = "line_reply") -> bool:
        """Handle one exact LINE cancellation without ever closing a position.

        ``False`` means the order exists but its MT5 ticket is not visible yet,
        so the Hub sequence must not advance and the caller should retry.
        """
        with self._lock:
            order = self.orders.get(signal_id)
            if order is None:
                # The trade may have been filtered by membership/source settings.
                return True
            if order.cancel_state in (CancelState.MT5_CONFIRMED, CancelState.ALREADY_FILLED):
                return True
            if order.status is OrderStatus.CANCELLED:
                order.cancel_state = CancelState.MT5_CONFIRMED
                return True
            if order.status not in (OrderStatus.PENDING, OrderStatus.SENT):
                if order.status in (OrderStatus.FILLED, OrderStatus.PARTIAL_CLOSED, OrderStatus.CLOSED):
                    order.cancel_state = CancelState.ALREADY_FILLED
                return True
            order.cancel_requested = True
            order.cancel_reason = reason
            if order.cancel_state is CancelState.NONE:
                order.cancel_state = CancelState.REQUESTED
            ticket = order.ticket
            sent_at = order.cancel_sent_at
            state = order.cancel_state

        expected_comment = f"copy_{signal_id}"
        if not ticket:
            # The EA can consume commands.json before orders.json is refreshed.
            # Resolve the exact comment directly instead of guessing by direction.
            for pending in self._get_pending_orders():
                if str(pending.get("comment") or "") == expected_comment:
                    ticket = pending.get("ticket")
                    break

        if not ticket:
            # A fill may have won the race. Treat the cancel event as handled,
            # update local tracking, and most importantly never send "close".
            for position in self._get_positions():
                if str(position.get("comment") or "") != expected_comment:
                    continue
                with self._lock:
                    current = self.orders.get(signal_id)
                    if current is not None:
                        current.status = OrderStatus.FILLED
                        current.ticket = position.get("ticket")
                        current.cancel_requested = False
                        current.cancel_state = CancelState.ALREADY_FILLED
                logger.info("LINE 引用撤單到達時訂單已成交，保留部位：%s", signal_id)
                return True
            logger.info("LINE 引用撤單等待 MT5 ticket：%s", signal_id)
            return False

        # Do not overwrite commands.json continuously while the EA is still
        # consuming the previous delete command.
        if state is CancelState.COMMAND_SENT and self.clock_age(sent_at) < self.CANCEL_RETRY_SECONDS:
            return False

        success = self._delete_pending_order(int(ticket), signal_id)
        if not success:
            with self._lock:
                current = self.orders.get(signal_id)
                if current is not None:
                    current.cancel_state = CancelState.FAILED_RETRY
                    current.cancel_error = "command_write_failed"
            return False
        with self._lock:
            current = self.orders.get(signal_id)
            if current is not None and current.status in (OrderStatus.PENDING, OrderStatus.SENT):
                current.ticket = int(ticket)
                current.cancel_delete_sent = True
                current.cancel_state = CancelState.COMMAND_SENT
                current.cancel_sent_at = time.time()
                current.cancel_attempts += 1
                current.cancel_error = ""
        logger.info("精確撤單已送出 pending-delete：%s ticket=%s", signal_id, ticket)
        self._write_journal(
            "LINE_REPLY_CANCEL_SENT",
            f"signal_id={signal_id} | ticket={ticket} | 原因={reason}",
        )
        # Writing commands.json is not cancellation confirmation. The Hub
        # cursor advances only after _check_vanished_orders reconciles MT5.
        return False

    CANCEL_RETRY_SECONDS = 5

    @staticmethod
    def clock_age(timestamp: float) -> float:
        return max(0.0, time.time() - float(timestamp or 0.0))

    # MT5 order type: 偶數=buy 系列(0 BUY / 2 BUY_LIMIT / 4 BUY_STOP), 奇數=sell 系列
    @staticmethod
    def _direction_from_mt5_type(raw_type: Any) -> str:
        try:
            return "sell" if int(raw_type) % 2 else "buy"
        except (TypeError, ValueError):
            return ""

    def _broker_time_to_epoch(self, text: str) -> Optional[float]:
        """把 EA 寫的券商牆上時間字串 (2026.07.28 20:41) 轉成真正的 epoch。

        EA 記的是券商當地時間，要扣掉 gmt_offset 才會對上 time.time()。
        """
        text = str(text or "").strip()
        if not text:
            return None
        for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
            try:
                naive = datetime.strptime(text, fmt)
            except ValueError:
                continue
            wall = calendar.timegm(naive.timetuple())   # 當成 UTC 讀 = 券商牆上時間
            info = self._read_json_file(self.mt5_files_dir / "account_info.json") or {}
            try:
                offset = int(info.get("gmt_offset") or 0)
            except (TypeError, ValueError):
                offset = 0
            return wall - offset
        return None

    def adopt_open_orders(self) -> int:
        """重啟後把 MT5 上還活著的本系統單接回追蹤清單。

        沒有這一步的話，每次重啟都會留下「孤兒單」：MT5 上掛著，但會員端不認得它，
        成交後的輸贏也不會計入馬丁層級，LINE 引用撤單也找不到原始 execution ID。

        靠 magic number 認自己的單，comment 裡帶著原本的 signal_id。
        回傳認領的張數。
        """
        from copy_trader.signal_parser.regex_parser import ParsedSignal

        adopted = 0
        pending = self._read_json_file(self.pending_orders_file) or {}
        positions = self._read_json_file(self.positions_file) or {}

        def _signal_id(raw: dict, ticket: Any) -> str:
            comment = str(raw.get("comment") or "")
            if comment.startswith("copy_copy_"):
                return comment[5:]
            return comment or f"adopted_{ticket}"

        def _register(raw: dict, status: OrderStatus, created_at: Optional[float]) -> bool:
            ticket = raw.get("ticket")
            try:
                if int(raw.get("magic") or 0) != self.magic_number:
                    return False
            except (TypeError, ValueError):
                return False
            signal_id = _signal_id(raw, ticket)
            with self._lock:
                if signal_id in self.orders:
                    return False
            direction = self._direction_from_mt5_type(raw.get("type"))
            entry = raw.get("price") if raw.get("price") is not None else raw.get("price_open")
            tp = raw.get("tp")
            signal = ParsedSignal(
                is_valid=True,
                symbol=str(raw.get("symbol") or self.symbol_name),
                direction=direction,
                entry_price=float(entry) if entry else None,
                stop_loss=float(raw.get("sl")) if raw.get("sl") else None,
                take_profit=[float(tp)] if tp else [],
                confidence=1.0,
                raw_text="adopted from MT5 on restart",
            )
            volume = float(raw.get("volume") or 0.0)
            order = ManagedOrder(
                signal_id=signal_id,
                signal=signal,
                status=status,
                ticket=int(ticket) if ticket is not None else None,
                remaining_volume=volume,
                initial_volume=volume,
                source_window=self._signal_sources.get(signal_id, ""),
                created_at=created_at if created_at else time.time(),
            )
            if status is OrderStatus.FILLED:
                order.entry_time = order.created_at
                order.entry_price = signal.entry_price
            with self._lock:
                self.orders[signal_id] = order
            return True

        for raw in (pending.get("orders") or []):
            if isinstance(raw, dict) and _register(
                raw, OrderStatus.SENT, self._broker_time_to_epoch(raw.get("time_setup"))
            ):
                adopted += 1
                logger.info(
                    "認領 MT5 掛單 ticket=%s %s %s @%s (%.1f 分鐘前掛出)",
                    raw.get("ticket"), raw.get("symbol"),
                    self._direction_from_mt5_type(raw.get("type")), raw.get("price"),
                    (time.time() - (self._broker_time_to_epoch(raw.get("time_setup")) or time.time())) / 60.0,
                )

        for raw in (positions.get("positions") or []):
            if isinstance(raw, dict):
                created = raw.get("open_timestamp") or raw.get("time")
                try:
                    created = float(created) if created else None
                except (TypeError, ValueError):
                    created = None
                if _register(raw, OrderStatus.FILLED, created):
                    adopted += 1
                    logger.info("認領 MT5 持倉 ticket=%s %s", raw.get("ticket"), raw.get("symbol"))

        if adopted:
            logger.info("重啟後共認領 %d 張 MT5 既有單，恢復追蹤", adopted)
        return adopted

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

    def source_risk_snapshot(self, source_window: str) -> Dict[str, Any]:
        """Return live exposure and today's realised result for one source.

        ``closed_trades.json`` may contain several partial-close rows for one
        position and repeats the position's net profit on each row.  Count each
        position once; summing raw rows would multiply both P/L and trade count.
        The day boundary follows the broker's GMT offset because that is the
        clock encoded in the EA's timestamps.
        """
        active_statuses = {
            OrderStatus.PENDING,
            OrderStatus.SENT,
            OrderStatus.FILLED,
            OrderStatus.PARTIAL_CLOSED,
        }
        with self._lock:
            active = sum(
                1 for order in self.orders.values()
                if order.source_window == source_window and order.status in active_statuses
            )
            sources = dict(self._signal_sources)

        account = self._read_json_file(self.mt5_files_dir / "account_info.json") or {}
        try:
            broker_offset = int(account.get("gmt_offset") or 0)
        except (TypeError, ValueError):
            broker_offset = 0
        broker_now = time.time() + broker_offset
        broker_day = datetime.fromtimestamp(broker_now, timezone.utc).strftime("%Y-%m-%d")

        closed = self._read_json_file(self.mt5_files_dir / "closed_trades.json") or {}
        positions: Dict[str, float] = {}
        for raw in (closed.get("trades") or []):
            if not isinstance(raw, dict):
                continue
            try:
                closed_at = float(raw.get("close_timestamp") or 0)
            except (TypeError, ValueError):
                continue
            if (
                not closed_at
                or datetime.fromtimestamp(closed_at, timezone.utc).strftime("%Y-%m-%d") != broker_day
            ):
                continue
            comment = str(raw.get("comment") or "")
            signal_id = comment[5:] if comment.startswith("copy_") else comment
            position_id = str(raw.get("position_id") or raw.get("ticket") or signal_id)
            source = (
                sources.get(signal_id)
                or sources.get(position_id)
                or sources.get(str(raw.get("ticket") or ""))
                or ""
            )
            if source != source_window or position_id in positions:
                continue
            try:
                positions[position_id] = float(raw.get("profit") or 0.0)
            except (TypeError, ValueError):
                positions[position_id] = 0.0

        return {
            "source": source_window,
            "active_orders": active,
            "daily_trades": len(positions),
            "daily_profit": round(sum(positions.values()), 2),
            "broker_day": broker_day,
        }

    def profile_for(self, source_window: str = "") -> dict:
        """把某個來源的下單設定解析成完整的一份（沒設定的欄位回退全域值）。

        mode: "martingale" = 逐關加碼；"flat" = 均注，每筆固定手數、不進關。
        """
        raw = self.source_profiles.get(source_window) or {} if source_window else {}
        mode = str(raw.get("mode") or "").strip().lower()
        if mode not in ("martingale", "flat"):
            mode = "martingale" if self.use_martingale else "flat"

        def _num(key, fallback):
            try:
                v = float(raw.get(key))
                return v if v > 0 else fallback
            except (TypeError, ValueError):
                return fallback

        lots = raw.get("lots") or []
        if not isinstance(lots, list):
            lots = []
        # 多 TP 的處理方式：
        #   "partial"   = 依比例分批平倉（舊行為）
        #   "breakeven" = 不分批，觸及 TP(n) 就把停損推到 TP(n-1)，TP1 推到成交價
        tp_mode = str(raw.get("tp_mode") or "").strip().lower()
        if tp_mode not in ("partial", "breakeven"):
            tp_mode = "partial"
        # 每個來源可各自設分批比例(佔原始手數);沒給或不合法就用全域預設 [0.5,0.3,0.2]。
        partial_ratios = self.partial_close_ratios
        raw_ratios = raw.get("partial_ratios")
        if isinstance(raw_ratios, list):
            parsed = []
            for x in raw_ratios:
                try:
                    f = float(x)
                except (TypeError, ValueError):
                    f = 0.0
                if f > 0:
                    parsed.append(f)
            if len(parsed) >= 2:
                partial_ratios = parsed
        return {
            "source": source_window,
            "configured": bool(raw),
            "enabled": bool(raw.get("enabled", True)),
            "mode": mode,
            "tp_mode": tp_mode,
            "partial_ratios": [float(x) for x in partial_ratios],
            "base_lot": _num("base_lot", self.default_lot_size),
            "multiplier": _num("multiplier", self.martingale_multiplier),
            "max_level": int(_num("max_level", self.martingale_max_level)),
            "lots": [float(x) for x in lots if float(x) > 0]
                    or self.martingale_source_lots.get(source_window, [])
                    or [],
            # 0 代表不限。這些限制由會員端在真正寫 MT5 指令前執行。
            # 每日止盈 / 止損:該來源當日累積損益達到門檻就自動停跟(當天不再進場)。
            # max_active_orders / max_daily_trades 保留欄位但面板已移除,未設就 0=不限。
            "max_active_orders": int(_num("max_active_orders", 0)),
            "max_daily_trades": int(_num("max_daily_trades", 0)),
            "max_daily_loss": _num("max_daily_loss", 0.0),
            "max_daily_profit": _num("max_daily_profit", 0.0),
        }

    def is_source_enabled(self, source_window: str = "") -> bool:
        """該來源是否要跟單。未設定的來源預設跟。"""
        return self.profile_for(source_window)["enabled"]

    def get_martingale_lot_size(self, source_window: str = "") -> float:
        """
        Calculate current lot size based on Martingale level.

        Args:
            source_window: If martingale_per_source=True, use this source's level.
        """
        profile = self.profile_for(source_window)

        # 均注：固定手數，完全不看馬丁層級
        if profile["mode"] == "flat":
            lot = round(profile["base_lot"], 2)
            tag = f" [{source_window}]" if source_window else ""
            logger.info(f"均注{tag}: Lot size = {lot}")
            return lot

        # 這個來源有自己的馬丁設定 → 用它的 base/倍數/關卡數
        if profile["configured"]:
            state = self._source_martingale.get(source_window, {"level": 0, "losses": 0})
            level = state["level"]
            lots = profile["lots"]
            if lots:
                level = min(level, len(lots) - 1)
                lot = round(lots[level], 2)
                logger.info(f"馬丁 Level {level} [{source_window}]: Lot size = {lot} (該群自訂手數)")
                return lot
            level = min(level, profile["max_level"] - 1)
            lot = round(profile["base_lot"] * (profile["multiplier"] ** level), 2)
            logger.info(f"馬丁 Level {level} [{source_window}]: Lot size = {lot} (該群設定)")
            return lot

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
        # martingale_max_level 是「關卡數」, 有效層索引為 0..(關卡數-1), 最大手數 = base×2^(關卡數-1)
        level = min(level, self.martingale_max_level - 1)
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
        profile = self.profile_for(source_window)

        # 均注來源：輸贏都不進關、也不能去動全域層級，否則會污染跑馬丁的那一群。
        if profile["mode"] == "flat":
            logger.info(
                "%s [%s]: 均注模式，不調整馬丁層級",
                "WIN" if is_win else "LOSS", source_window or "全域",
            )
            self._write_journal(
                f"TRADE_CLOSED_{'WIN' if is_win else 'LOSS'}",
                f"signal_id={signal_id} | 來源={source_window} | 模式=均注 "
                f"| 下一手數={self.get_martingale_lot_size(source_window)}"
            )
            return

        # 這個來源有自己的馬丁設定 → 一律走各群獨立層級，
        # 不受全域 martingale_per_source 影響（混用模式時共用層級一定是錯的）。
        if profile["configured"] and source_window:
            state = self._source_martingale.get(source_window, {"level": 0, "losses": 0})
            top = (len(profile["lots"]) - 1) if profile["lots"] else (profile["max_level"] - 1)
            if is_win:
                if state["level"] > 0:
                    logger.info(f"WIN [{source_window}]: 馬丁層級 {state['level']} → 0")
                state["level"] = 0
                state["losses"] = 0
            else:
                state["losses"] += 1
                if state["level"] < top:
                    state["level"] += 1
                    logger.info(f"LOSS [{source_window}]: 馬丁層級 → {state['level']}")
                else:
                    logger.warning(f"LOSS [{source_window}]: 已達最大層級 {top}，重置")
                    state["level"] = 0
                    state["losses"] = 0
            self._source_martingale[source_window] = state
            self._save_martingale_state()
            self._write_journal(
                f"TRADE_CLOSED_{'WIN' if is_win else 'LOSS'}",
                f"signal_id={signal_id} | 來源={source_window} | 馬丁層級={state['level']} "
                f"| 下一手數={self.get_martingale_lot_size(source_window)}"
            )
            return

        if not self.use_martingale:
            return

        src_tag = f" [{source_window}]" if self.martingale_per_source and source_window else ""
        max_level = (len(self.martingale_lots) - 1) if self.martingale_lots else (self.martingale_max_level - 1)

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
        """Reset martingale to base level (manual reset).

        每群獨立模式下也要一併清掉各來源的層級 — 否則手動重置在該模式等於沒作用
        (下單走的是 _source_martingale, 不是 current_martingale_level)。
        """
        logger.info(f"Manual martingale reset from level {self.current_martingale_level} to 0")
        self.current_martingale_level = 0
        self.consecutive_losses = 0
        if self._source_martingale:
            logger.info("同時重置各群馬丁層級: %s", list(self._source_martingale.keys()))
            self._source_martingale = {}
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
        # 以下兩種模式都依賴 RegexSignalParser 的不變量：take_profit 一律「由近到遠」
        # (buy 升冪 / sell 降冪)，所以 tps[-1] 必定是最遠的目標。曾經 parser 無條件
        # 升冪，賣單的 tps[-1] 是最近的目標 → 整單在第一個目標就被 MT5 全平。
        partial_plan: List[float] = []
        mt5_tp = None

        if self.profile_for(order.source_window)["tp_mode"] == "breakeven" and len(tps) > 1:
            # 保本移損：不分批、手數整筆保留。MT5 停利掛在最遠那關，中途由
            # _check_trailing_sl 把停損往有利方向推（TP1→成交價、TP2→TP1…）。
            mt5_tp = tps[-1]
            logger.info(
                "保本移損模式: %s 手整筆不分批, MT5 TP=最遠 %s（觸及 TP1 後停損移到成交價）",
                lot_size, mt5_tp,
            )
        else:
            # 分批計畫以「原始手數」為基準；空計畫=手數不足以乾淨分割 → 退回整包在 TP1 平。
            # 用該來源自訂的分批比例(profile_for 已回退到全域預設)。
            source_ratios = self.profile_for(order.source_window).get("partial_ratios")
            partial_plan = self._plan_partial_chunks(lot_size, tps, source_ratios)
            if partial_plan:
                # 多 TP + 可分割：MT5 TP 設在最後一關(當尾段的安全網),中間關由 _check_partial_tp_hits 處理。
                mt5_tp = tps[-1]
                logger.info(f"分批平倉啟用: {lot_size} 手, 中間關計畫={partial_plan} + 尾段, MT5 TP=最後 {mt5_tp}")
            elif tps:
                # 單一 TP,或手數不足以分批:用第一個 TP,整包一次平。
                mt5_tp = tps[0]
                if len(tps) > 1:
                    logger.info(f"多 TP 但 {lot_size} 手不足以分批(每塊需≥0.01)→ 退回整包在 TP1 平: {mt5_tp}")

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

        pending_order_type = str(getattr(signal, "pending_order_type", "") or "").strip().lower()
        if pending_order_type:
            command["pending_order_type"] = pending_order_type

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

    def _get_pending_orders(self, allow_none: bool = False) -> List[dict]:
        """Get current pending orders from MT5.

        allow_none=True 時，讀檔失敗回 None（而不是空清單）。對帳用途必須分得出
        「MT5 真的沒有掛單」和「檔案剛好被鎖住讀不到」，否則會把讀取失敗當成
        單被刪掉。
        """
        data = self._read_json_file(self.pending_orders_file)
        if data:
            return data.get('orders', [])
        return None if allow_none else []

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
                self._check_trade_results()
                self._check_order_fills()
                self._check_closed_positions()  # Check for wins/losses
                self._check_partial_tp_hits()
                self._check_trailing_sl()
                # 先偵測成交(上面)，再對帳消失的掛單，避免把「剛成交」誤判成「被刪除」
                self._check_vanished_orders()
                # 掛單掛太久還沒進場就自動撤掉
                self._check_unfilled_timeout()

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

    # 部位從 positions.json 消失後，等 closed_trades.json 出現該筆成交的寬限秒數。
    #
    # 必須大於 EA 寫 closed_trades.json 的週期，否則「每一筆」都會等不到而退回
    # last_known_profit（最後看到的浮動損益），拿到的是接近平倉但不等於平倉的數字。
    # EA 是每 10 秒寫一次（MT5_File_Bridge_Enhanced.mq5:117 last_trades_write >= 10），
    # 而 positions.json 每 2 秒寫一次 —— 部位消失最快 2 秒就被我們看到，最糟情況要
    # 再等將近一整個 10 秒週期，所以留兩倍餘裕。
    #
    # 實例 (2026-08-10)：三筆全部走 fallback，帳上 +1500/-600/0 被記成
    # +1494/-500/+16。均注來源只是紀錄失真，但馬丁來源會據此判 WIN/LOSS，
    # 浮動損益剛好跨過零點就會把該進關的虧損誤判成獲利。
    CLOSE_CONFIRM_TIMEOUT = 25

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
                # 帶上這張單原本的成交量：分批平倉要等所有段都寫進檔案才採信損益，
                # 否則只會拿到第一段（例如 TP1 那 0.5 手獲利），把淨虧判成 WIN。
                profit = self._get_closed_trade_profit(
                    order.ticket, expected_volume=order.initial_volume or 0.0
                )

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

    def _get_closed_trade_profit(self, ticket: int, expected_volume: float = 0.0) -> Optional[float]:
        """
        Get the profit of a closed trade from MT5.

        Args:
            ticket: The position ticket number (from positions.json)
            expected_volume: 這個 position 應該被平掉的總手數。分批平倉時 EA 會
                分好幾筆寫入 closed_trades.json，手數湊不滿代表還沒寫完，
                這時回 None 讓呼叫端再等一輪，避免拿到只涵蓋第一段的損益。

        Returns:
            Profit amount (negative = loss), or None if not found
        """
        closed_trades_file = self.mt5_files_dir / "closed_trades.json"

        data = self._read_json_file(closed_trades_file)
        if data:
            trades = data.get('trades', [])

            # Match by position_id first (correct field from EA)。
            # EA 把「position 的淨損益」寫進該 position 的每一筆成交紀錄，所以取
            # 任何一筆都是淨額——但前提是所有分批都已經寫進檔案。分批平倉的單會
            # 分好幾筆陸續寫入，太早讀只會拿到第一段的損益。
            #
            # 實例 (2026-07-31)：0.5 手在 TP1 平掉 +245.50、剩餘 0.5 手停損 -250，
            # position 淨額 -4.50 是「輸」。但部位消失當下只讀到 +245.50，被判成 WIN。
            #
            # 所以用「已平手數是否補齊」當閘門：湊不滿就回 None，讓呼叫端繼續等，
            # 等不到再由 CLOSE_CONFIRM_TIMEOUT 走保底路徑。
            matched = [t for t in trades if t.get('position_id') == ticket]
            if matched:
                closed_volume = sum(float(t.get('volume') or 0) for t in matched)
                profit = float(matched[0].get('profit', 0))
                if expected_volume and closed_volume + 1e-9 < expected_volume:
                    logger.info(
                        "position %s 分批尚未寫完（已平 %.2f / 應為 %.2f 手），先不採信損益 %.2f",
                        ticket, closed_volume, expected_volume, profit,
                    )
                    return None
                logger.info(
                    f"Found closed trade profit by position_id: ticket={ticket}, "
                    f"profit={profit} (共 {len(matched)} 筆成交, 已平 {closed_volume:.2f} 手)"
                )
                return profit

            # Fallback: match by deal ticket (legacy)
            for trade in trades:
                if trade.get('ticket') == ticket:
                    profit = float(trade.get('profit', 0))
                    logger.info(f"Found closed trade profit by deal ticket: ticket={ticket}, profit={profit}")
                    return profit

        # 還找不到 → 回 None 讓呼叫端再等一輪。
        #
        # 這裡刻意用 debug 而不是 warning：EA 每 10 秒才寫一次 closed_trades.json，
        # 而這個函式在 CLOSE_CONFIRM_TIMEOUT (25s) 的寬限期內每秒被呼叫一次，
        # 所以「暫時找不到」是預期中的正常狀態，不是異常。實測 2026-08-11 一筆
        # 正常平倉就噴了 11 行 WARNING，最後在第 11 秒成功找到損益 —— 結果是對的，
        # 過程卻在 log 上看起來像出事了，一天幾十單就會把真正的警告淹掉。
        #
        # 真正該警告的是「寬限期跑完仍然沒有」，那由呼叫端的 timeout fallback 分支
        # 印一行 WARNING (見 _check_closed_positions)，該有的告警不會漏。
        logger.debug("closed_trades.json 尚未出現 ticket %s 的損益，稍後重試", ticket)
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
                    if comment == f"copy_{signal_id}":
                        order.status = OrderStatus.FILLED
                        if order.cancel_requested:
                            order.cancel_state = CancelState.ALREADY_FILLED
                        order.cancel_requested = False
                        order.cancel_delete_sent = False
                        order.ticket = pos.get('ticket')
                        order.entry_price = pos.get('price_open')
                        order.entry_time = time.time()
                        order.remaining_volume = pos.get('volume', 0)
                        order.initial_volume = order.remaining_volume
                        # 用「實際成交量」重算分批計畫(佔原始),空=不分批
                        order.partial_plan = self._plan_partial_chunks(
                            order.initial_volume, order.signal.take_profit
                        )
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
                        if comment == f"copy_{signal_id}":
                            order.ticket = po.get('ticket')
                            logger.debug(f"Pending order ticket found: {signal_id} -> {order.ticket}")
                            # Map ticket → source immediately for history and restart adoption.
                            if order.source_window and order.ticket:
                                self._signal_sources[str(order.ticket)] = order.source_window
                                self._save_signal_sources()
                            break

    # 掛單從 MT5 消失後，要連續看不到這麼久才認定它真的沒了。
    # 成交的瞬間掛單會先從 orders.json 消失、稍後才出現在 positions.json，
    # 這段空窗如果不等就會被誤判成「被刪掉」。
    VANISH_CONFIRM_SECONDS = 4

    def _check_vanished_orders(self):
        """對帳：掛單在 MT5 被手動刪掉（或券商撤掉）時，把追蹤狀態同步過來。

        沒有這一步的話，使用者手動刪單或 LINE delete 指令成功後，會員端
        仍會顯示掛單在等待，面板會跟 MT5 實況對不起來。
        """
        pending = self._get_pending_orders(allow_none=True)
        if pending is None:
            return  # 檔案讀不到，這輪跳過，不能當成「掛單都不見了」
        positions = self._get_positions(allow_none=True)
        if positions is None:
            return

        live_tickets = {o.get("ticket") for o in pending}
        live_tickets |= {p.get("ticket") for p in positions}
        now = time.time()
        vanished = []

        with self._lock:
            for signal_id, order in self.orders.items():
                # 只看「MT5 已經確認過、拿得到 ticket」的未成交單
                if order.status not in (OrderStatus.PENDING, OrderStatus.SENT) or not order.ticket:
                    continue
                if order.ticket in live_tickets:
                    order.vanish_detected_at = None
                    continue
                if order.vanish_detected_at is None:
                    order.vanish_detected_at = now
                    continue
                if now - order.vanish_detected_at >= self.VANISH_CONFIRM_SECONDS:
                    order.status = OrderStatus.CANCELLED
                    order.cancel_requested = False
                    order.cancel_delete_sent = False
                    order.cancel_state = CancelState.MT5_CONFIRMED
                    vanished.append((signal_id, order))

        for signal_id, order in vanished:
            logger.info(
                "掛單已從 MT5 消失（在 MT5 端被刪除）：%s ticket=%s — 同步標記為已撤銷",
                signal_id, order.ticket,
            )
            self.on_order_cancelled(signal_id)
            self._write_journal(
                "ORDER_REMOVED_IN_MT5",
                f"signal_id={signal_id} | ticket={order.ticket} | 原因=在 MT5 端被刪除或撤銷 "
                f"| 信號={order.signal} | 來源={order.source_window}"
            )

    # 掛單掛了這麼久還沒成交就自動撤掉：進場價通常已經跑遠，這張限價單多半失去意義。
    # 從「掛單實際掛出的時間」起算——認領既有單時 created_at 用券商 time_setup(已扣
    # gmt_offset 對上 time.time())，所以會員端重啟後這個 4 小時仍然是連續的、不會重置。
    UNFILLED_PENDING_TIMEOUT_SECONDS = 4 * 3600

    def _check_unfilled_timeout(self):
        """掛單逾時未成交就自動撤單——等同一次 LINE 撤單，只是由時間觸發。

        只碰未成交的 PENDING/SENT 單；已成交部位由 cancel_pending_order 內部
        擋掉(標 ALREADY_FILLED、永不送平倉)，就算 4 小時剛到又同時成交也不會誤平。
        """
        if self.UNFILLED_PENDING_TIMEOUT_SECONDS <= 0:
            return
        now = time.time()
        due = []
        with self._lock:
            for signal_id, order in self.orders.items():
                if order.status not in (OrderStatus.PENDING, OrderStatus.SENT):
                    continue
                age = now - order.created_at
                if age < self.UNFILLED_PENDING_TIMEOUT_SECONDS:
                    continue
                # 只在「第一次」觸發時記 log/journal，之後每輪重試不再洗版
                first = order.cancel_state is CancelState.NONE and not order.cancel_requested
                due.append((signal_id, order, age, first))

        for signal_id, order, age, first in due:
            if first:
                logger.info(
                    "掛單逾時未成交（已掛 %.1f 小時 ≥ %.1f 小時）自動撤單：%s ticket=%s",
                    age / 3600, self.UNFILLED_PENDING_TIMEOUT_SECONDS / 3600,
                    signal_id, order.ticket,
                )
                self._write_journal(
                    "ORDER_TIMEOUT_CANCEL",
                    f"signal_id={signal_id} | ticket={order.ticket} | 原因=掛單逾時未成交自動撤單 "
                    f"| 已掛 {age / 3600:.1f} 小時 | 信號={order.signal} | 來源={order.source_window}"
                )
            # cancel_pending_order 自帶節流與重試；ticket 還沒出現時回 False，下一輪會再試。
            self.cancel_pending_order(signal_id, reason="unfilled_timeout_4h")

    # EA 每執行一筆指令就往 trade_results.txt 追加一行：
    #   2026.08.03 14:57 | sell | FAIL | 1.00 | XAUUSD | copy_1785758261661 | retcode:10016 | Invalid stops
    # 只讀尾端這麼多行就夠——被標成 FAILED 的單不會再被比對到，重複讀不會有副作用。
    TRADE_RESULTS_TAIL_LINES = 200

    def _check_trade_results(self):
        """讀 EA 的執行回報，把被券商拒絕的單標成失敗。

        沒有這一步的話，拒單會變成幽靈：commands.json 被 EA 消化掉了，但 MT5 上
        不會有任何訂單，會員端卻一直把它當成「等待成交」。

        實例 (2026-08-03)：市價賣單的停利 4035 落在市價 4030 之上（賣單的停利
        必須低於進場價），MT5 回 retcode 10016 Invalid stops 直接拒單。
        """
        path = self.mt5_files_dir / "trade_results.txt"
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-self.TRADE_RESULTS_TAIL_LINES:]
        except OSError:
            return

        failures: Dict[str, Tuple[str, str, str, str]] = {}
        for line in lines:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 6 or parts[2].upper() != "FAIL":
                continue
            action = parts[1].casefold()
            signal_id = parts[5]
            retcode = parts[6] if len(parts) > 6 else ""
            message = parts[7] if len(parts) > 7 else ""
            if signal_id:
                failures[signal_id] = (action, retcode, message, line.strip())
        if not failures:
            return

        rejected = []
        with self._lock:
            for signal_id, (action, retcode, message, result_line) in failures.items():
                order = self.orders.get(signal_id)
                if order is None or order.status not in (OrderStatus.PENDING, OrderStatus.SENT):
                    continue
                if action == "delete":
                    if order.cancel_last_result == result_line:
                        continue
                    order.cancel_last_result = result_line
                    order.cancel_state = CancelState.FAILED_RETRY
                    order.cancel_delete_sent = False
                    order.cancel_sent_at = 0.0
                    order.cancel_error = f"{retcode} {message}".strip()
                    logger.warning(
                        "MT5 撤單失敗，將重試：%s（%s %s）",
                        signal_id,
                        retcode,
                        message,
                    )
                    continue
                order.status = OrderStatus.FAILED
                rejected.append((signal_id, order, retcode, message))

        for signal_id, order, retcode, message in rejected:
            logger.error(
                "MT5 拒單：%s（%s %s）— %s",
                signal_id, retcode, message, order.signal,
            )
            self._write_journal(
                "ORDER_REJECTED_BY_MT5",
                f"signal_id={signal_id} | {retcode} | 原因={message} "
                f"| 信號={order.signal} | 來源={order.source_window}"
            )

    def _check_trailing_sl(self):
        """保本移損：觸及 TP(n) 就把停損推到 TP(n-1)，TP1 推到實際成交價。

        手數完全不動——用「不會再賠」換「跑滿全程的機會」，跟分批平倉是二選一。
        最後一關不處理：那一關由 MT5 的停利整筆平掉。
        """
        current_price = self._get_current_price()
        if not current_price:
            return

        pending = []
        with self._lock:
            for signal_id, order in self.orders.items():
                if order.status not in (OrderStatus.FILLED, OrderStatus.PARTIAL_CLOSED):
                    continue
                if not order.ticket or not order.entry_price:
                    continue
                if self.profile_for(order.source_window)["tp_mode"] != "breakeven":
                    continue

                signal = order.signal
                tps = signal.take_profit or []
                direction = str(getattr(signal, "direction", "") or "").lower()
                if len(tps) < 2 or direction not in ("buy", "sell"):
                    continue

                # 從還沒推過的那一關往後看，一次可能跨過好幾關（跳空）
                target_sl = None
                reached = order.sl_trail_index
                for index in range(order.sl_trail_index, len(tps) - 1):
                    tp = tps[index]
                    hit = current_price >= tp if direction == "buy" else current_price <= tp
                    if not hit:
                        break
                    # 第一關推到實際成交價（保本），之後每一關推到前一關
                    target_sl = order.entry_price if index == 0 else tps[index - 1]
                    reached = index + 1

                if target_sl is None:
                    continue
                # 停損只准往有利方向移，不可退回
                previous = order.trailed_sl
                if previous is not None:
                    better = target_sl > previous if direction == "buy" else target_sl < previous
                    if not better:
                        order.sl_trail_index = reached
                        continue
                order.sl_trail_index = reached
                order.trailed_sl = target_sl
                pending.append((signal_id, order, target_sl, reached))

        for signal_id, order, target_sl, reached in pending:
            label = "成交價（保本）" if reached == 1 else f"第 {reached - 1} 關"
            if self._modify_position(order.ticket, sl=target_sl):
                logger.info(
                    "保本移損：%s 觸及第 %d 關，停損移到 %s %s",
                    signal_id, reached, target_sl, label,
                )
                self._write_journal(
                    "SL_MOVED",
                    f"signal_id={signal_id} | ticket={order.ticket} | 觸及第 {reached} 關 "
                    f"| 停損移到 {target_sl}（{label}）| 來源={order.source_window}"
                )
            else:
                # 送不出去就把狀態退回，下一輪重試
                with self._lock:
                    order.sl_trail_index = reached - 1
                    order.trailed_sl = None
                logger.warning("保本移損失敗（改單指令沒送出）：%s，下一輪重試", signal_id)

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
                # 保本移損與分批平倉互斥，該來源選了保本就不在這裡分批
                if self.profile_for(order.source_window)["tp_mode"] == "breakeven":
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
                # Only process intermediate TPs (last TP is handled by MT5 safety net).
                # tps 是「由近到遠」排序的 (見 RegexSignalParser.parse)，所以
                # tps[current_tp_index] 依序就是下一個該被打到的目標。
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

    def _plan_partial_chunks(self, volume: float, tps: List[float],
                             ratios: Optional[List[float]] = None) -> List[float]:
        """
        預先算好「中間各 TP(除了最後一關)」要平的手數,比例以【原始成交量】為基準。

        規則:
          - 中間關數 = TP 數 - 1(最後一關由 MT5 的 TP 整包平掉尾段)。
          - 每一關 close = round(原始量 × 比例, 2),必須 ≥ 0.01(券商最低)。
          - 全部中間關平完後,尾段(給最後一關)也必須 ≥ 0.01。
          - 任一條件不滿足 → 回傳 []，代表「無法乾淨分批」,呼叫端會退回整包在 TP1 平。

        這樣 50/30/20 就是名副其實的「佔原始」,不會像舊版「佔剩餘」而被扭曲。
        """
        volume = round(volume or 0.0, 2)
        tps = tps or []
        ratios = ratios or self.partial_close_ratios      # 沒給就用全域比例
        num_intermediate = len(tps) - 1
        if num_intermediate < 1 or volume < 0.02:
            return []

        chunks: List[float] = []
        allocated = 0.0
        for i in range(num_intermediate):
            if i >= len(ratios):
                # 中間關比比例表還多 → 無法規劃乾淨分割
                return []
            chunk = round(volume * ratios[i], 2)
            if chunk < 0.01:
                return []
            chunks.append(chunk)
            allocated = round(allocated + chunk, 2)

        final = round(volume - allocated, 2)
        if final < 0.01:
            return []
        return chunks

    def _execute_partial_close(self, order: ManagedOrder):
        """
        送出分批平倉指令給 EA。

        手數取自預先算好的 order.partial_plan(佔原始量),所以 50/30/20 不論已平多少
        都維持 50/30/20。不立即更新狀態 — 等 _check_partial_tp_hits 第二階段確認 EA 已減倉。
        """
        if order.current_tp_index >= len(order.partial_plan):
            return

        close_volume = order.partial_plan[order.current_tp_index]

        # 安全網:永遠保留至少 0.01 給最後一關,不要一次平光。
        if order.remaining_volume - close_volume < 0.01:
            close_volume = round(order.remaining_volume - 0.01, 2)
        if close_volume < 0.01:
            logger.info(
                f"分批平倉跳過: {order.signal_id} 剩餘 {order.remaining_volume} 手不足以再分"
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

    def _delete_pending_order(self, ticket: int, signal_id: str = "") -> bool:
        """Send a delete command to MT5 to remove a pending order."""
        command = {
            "action": "delete",
            "ticket": ticket,
            "trade_id": signal_id,
            "comment": f"copy_{signal_id}" if signal_id else "",
        }
        return self._write_command(command)
