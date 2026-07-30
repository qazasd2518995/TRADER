"""
Trade Manager for Copy Trading System
Handles order lifecycle, partial closes, and cancellation.
"""
import calendar
import json
import time
import threading
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict
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
            # 立刻寫檔：原本只在成交時才存，導致新群組要等到第一筆成交
            # 才會出現在「訊號來源設定」清單裡（沒開 MT5 就永遠不出現）。
            self._save_signal_sources()

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

    def cancel_latest_pending(self, direction: str = "", reason: str = "group_cancel") -> bool:
        """撤掉「最近一筆」尚未成交的掛單 (只動 PENDING/SENT, 不碰已成交部位)。

        對應群組發的「取消/撤」訊息。direction 給 'buy'/'sell' 時只撤該方向。
        找不到符合的掛單就回 False (安全：什麼都不做, 不會誤平已成交部位)。
        """
        with self._lock:
            candidates = [
                (sid, o) for sid, o in self.orders.items()
                if o.status in (OrderStatus.PENDING, OrderStatus.SENT)
                and (not direction or getattr(o.signal, "direction", "") == direction)
            ]
        if not candidates:
            logger.info("group_cancel: 沒有符合的未成交掛單可撤 (direction=%r)", direction or "any")
            return False
        candidates.sort(key=lambda kv: kv[1].created_at, reverse=True)
        sid = candidates[0][0]
        return self.cancel_order(sid, reason=reason)

    def cancel_pending_same_direction(self, direction: str, within_seconds: float, exclude_signal_id: str = "") -> int:
        """撤掉 within_seconds 內、同方向的未成交舊掛單 (只動 PENDING/SENT)。

        「同方向短時間改單防呆」：新訊號下單前呼叫，撤掉剛剛同方向的舊掛單，
        避免乘改單/OCR怪異造成同方向多下一張。回傳撤掉的張數。
        """
        if not direction or within_seconds <= 0:
            return 0
        now = time.time()
        with self._lock:
            victims = [
                sid for sid, o in self.orders.items()
                if sid != exclude_signal_id
                and o.status in (OrderStatus.PENDING, OrderStatus.SENT)
                and getattr(o.signal, "direction", "") == direction
                and (now - o.created_at) <= within_seconds
            ]
        n = 0
        for sid in victims:
            if self.cancel_order(sid, reason="superseded_same_direction"):
                n += 1
        if n:
            logger.info("同方向改單防呆：撤掉 %d 張 %s 舊掛單 (被新訊號取代)", n, direction)
        return n

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

    def adopt_open_orders(
        self,
        cancel_after_seconds: Optional[int] = None,
        cancel_if_price_beyond: Optional[float] = None,
    ) -> int:
        """重啟後把 MT5 上還活著的本系統單接回追蹤清單。

        沒有這一步的話，每次重啟都會留下「孤兒單」：MT5 上掛著，但會員端不認得它，
        於是既不會逾時自動刪單，成交後的輸贏也不會計入馬丁層級。

        靠 magic number 認自己的單，comment 裡帶著原本的 signal_id。
        回傳認領的張數。
        """
        from copy_trader.signal_parser.groq_parser import ParsedSignal

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
                cancel_after_seconds=cancel_after_seconds,
                cancel_if_price_beyond=cancel_if_price_beyond,
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
        return {
            "source": source_window,
            "configured": bool(raw),
            "enabled": bool(raw.get("enabled", True)),
            "mode": mode,
            "base_lot": _num("base_lot", self.default_lot_size),
            "multiplier": _num("multiplier", self.martingale_multiplier),
            "max_level": int(_num("max_level", self.martingale_max_level)),
            "lots": [float(x) for x in lots if float(x) > 0]
                    or self.martingale_source_lots.get(source_window, [])
                    or [],
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
        # 分批計畫以「原始手數」為基準；空計畫=手數不足以乾淨分割 → 退回整包在 TP1 平。
        partial_plan = self._plan_partial_chunks(lot_size, tps)
        if partial_plan:
            # 多 TP + 可分割：MT5 TP 設在最後一關(當尾段的安全網),中間關由 _check_partial_tp_hits 處理。
            mt5_tp = tps[-1]
            logger.info(f"分批平倉啟用: {lot_size} 手, 中間關計畫={partial_plan} + 尾段, MT5 TP=最後 {mt5_tp}")
        elif len(tps) >= 1:
            # 單一 TP,或手數不足以分批:用第一個 TP,整包一次平。
            mt5_tp = tps[0]
            if len(tps) > 1:
                logger.info(f"多 TP 但 {lot_size} 手不足以分批(每塊需≥0.01)→ 退回整包在 TP1 平: {mt5_tp}")
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
                self._check_order_fills()
                self._check_closed_positions()  # Check for wins/losses
                self._check_partial_tp_hits()
                # 先偵測成交(上面)，再對帳消失的掛單，避免把「剛成交」誤判成「被刪除」
                self._check_vanished_orders()
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
                        if f"copy_{signal_id}" in comment:
                            order.ticket = po.get('ticket')
                            logger.debug(f"Pending order ticket found: {signal_id} -> {order.ticket}")
                            # Map ticket → source immediately so cancel-by-source works
                            # (don't wait until fill — pending orders need source isolation too)
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

        沒有這一步的話，使用者在 MT5 手動刪單後，會員端會一直顯示那張掛單還在等，
        倒數也還在跑——面板跟現實對不起來。
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

    def _plan_partial_chunks(self, volume: float, tps: List[float]) -> List[float]:
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
        num_intermediate = len(tps) - 1
        if num_intermediate < 1 or volume < 0.02:
            return []

        chunks: List[float] = []
        allocated = 0.0
        for i in range(num_intermediate):
            if i >= len(self.partial_close_ratios):
                # 中間關比原始比例表還多 → 無法規劃乾淨分割
                return []
            chunk = round(volume * self.partial_close_ratios[i], 2)
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
