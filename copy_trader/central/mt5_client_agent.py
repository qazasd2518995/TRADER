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
from typing import Dict, List, Optional

from copy_trader.config import DATA_DIR, DEFAULT_SYMBOL, load_config
from copy_trader.signal_parser.regex_parser import ParsedSignal
from copy_trader.trade_manager import OrderStatus, TradeManager

logger = logging.getLogger(__name__)


class HubClient:
    def __init__(self, hub_url: str, token: str = "", timeout: float = 15.0):
        self.hub_url = hub_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        # The Hub scans raw records before applying membership source filters.
        # This cursor may therefore be ahead of the last visible signal.
        self.last_cursor = 0

    def _request(self, path: str, retries: int = 2) -> Dict:
        """GET with 退避重試: 暫時性網路卡頓(timeout/連線斷)時自動重試, 避免單次抖動就放棄。"""
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(f"{self.hub_url}{path}", method="GET")
                if self.token:
                    req.add_header("Authorization", f"Bearer {self.token}")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError:
                raise  # 401 等 HTTP 錯誤不重試
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_exc = e
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))  # 退避: 0.5s, 1.0s
        raise last_exc if last_exc else RuntimeError("hub request failed")

    def health(self) -> Dict:
        return self._request("/health")

    def signals_after(self, after: int, limit: int = 50) -> List[Dict]:
        data = self._request(f"/signals?after={after}&limit={limit}")
        if not data.get("ok"):
            raise RuntimeError(data.get("error") or "hub_request_failed")
        signals = list(data.get("signals") or [])
        fallback_cursor = max(
            [int(item.get("seq") or 0) for item in signals],
            default=max(0, int(after or 0)),
        )
        try:
            self.last_cursor = max(
                max(0, int(after or 0)),
                int(data.get("cursor") if data.get("cursor") is not None else fallback_cursor),
            )
        except (TypeError, ValueError):
            self.last_cursor = fallback_cursor
        return signals


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
        # LINE DB parsing is deterministic; there is no statistical confidence
        # score. Keep the legacy field at zero for ParsedSignal compatibility.
        confidence=float(payload.get("confidence") or 0.0),
        raw_text_summary=str(payload.get("raw_text_summary") or ""),
        parse_method=str(payload.get("parse_method") or "hub"),
        error=str(payload.get("error") or ""),
    )


def _sl_tp_consistent(signal: ParsedSignal) -> bool:
    """
    確認 SL/TP 落在方向正確的一側，擋掉解析錯誤的反向訊號。

    buy：SL 必須低於 entry、entry 必須低於每個 TP（市價單沒有 entry 時，
    只要求每個 TP 高於 SL）。sell 相反。這能擋掉像「buy entry=4588
    sl=4605 tp=4580」這種 SL/TP 顛倒的壞訊號被送進 MT5。
    """
    sl = signal.stop_loss
    tps = [float(t) for t in (signal.take_profit or []) if t is not None]
    if sl is None or not tps:
        return True
    sl = float(sl)
    entry = float(signal.entry_price) if signal.entry_price is not None else None

    if signal.direction == "buy":
        if not all(tp > sl for tp in tps):
            return False
        if entry is not None and not (sl < entry < min(tps)):
            return False
        return True
    if signal.direction == "sell":
        if not all(tp < sl for tp in tps):
            return False
        if entry is not None and not (max(tps) < entry < sl):
            return False
        return True
    return True


def _is_executable_signal(signal: ParsedSignal) -> bool:
    has_entry = signal.entry_price is not None or bool(signal.is_market_order)
    return bool(
        signal.is_valid
        and signal.direction in {"buy", "sell"}
        and has_entry
        and signal.stop_loss is not None
        and signal.take_profit
        and _sl_tp_consistent(signal)
    )


class MT5ClientAgent:
    def __init__(self, hub: HubClient, state_file: Path, mt5_files_dir: str = "", replay: bool = False):
        self.hub = hub
        self.state_file = state_file
        self.config = load_config()
        if mt5_files_dir:
            self.config.mt5_files_dir = mt5_files_dir
            # 商品代號在 load_config() 的 __post_init__ 就依「當時的」MT5 路徑定案了，
            # 而那個路徑是自動偵測來的 (通常是機器上第一個 MT5)，跟這裡覆寫進來的
            # 可能是完全不同的券商。不重算的話，代號會沿用錯的那一家。
            #
            # 實測 2026-08-11：用 --instance 接 MetaQuotes-Demo (D:\MT5-2)，卻沿用了
            # C:\Program Files\MetaTrader 5 那台 AXPM 的 "XAUUSD.s"。EA 收到不存在的
            # 代號 → SymbolInfoDouble(ASK) 回 0 → 每一筆都被拒 (invalid ask price)，
            # 而且會員端這邊看起來一切正常，只有 MT5 的 trade_results.txt 才看得到。
            #
            # 只有在新路徑「真的有券商檔案」時才重算 —— 否則 EA 還沒啟動的空目錄會讓
            # detect_mt5_symbol() 回退到預設值，把使用者明確設好的代號蓋掉。
            _dir = Path(mt5_files_dir)
            if _dir.is_dir() and (any(_dir.glob("*_price.json")) or (_dir / "symbol_info.json").exists()):
                _resolved = self.config._resolve_symbol_name(self.config.symbol_name)
                if _resolved != self.config.symbol_name:
                    logger.info(
                        "依 MT5 路徑重新解析商品代號：%s → %s (%s)",
                        self.config.symbol_name, _resolved, mt5_files_dir,
                    )
                self.config.symbol_name = _resolved
        logger.info("撤單策略: 只接受 LINE 引用關係指定的原始報單")

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
        records = self.hub.signals_after(self.last_seq)
        for item in records:
            seq = int(item.get("seq") or 0)

            # LINE 引用撤單／原訊息收回：事件直接帶原報單的 deterministic execution ID。
            # 只撤未成交掛單，不猜方向、不撤「最近一張」、也不平已成交部位。
            if item.get("type") == "cancel_signal":
                cancel_reason = str(item.get("cancel_reason") or "line_reply")
                target_ids = [
                    str(value) for value in (item.get("target_execution_ids") or [])
                    if str(value)
                ]
                handled = 0
                try:
                    for signal_id in target_ids:
                        ready = self.trade_manager.cancel_pending_order(
                            signal_id,
                            reason=f"{cancel_reason}:{item.get('line_message_id') or seq}",
                        )
                        if not ready:
                            logger.info("LINE 撤單等待 MT5 確認，保留 Hub seq=%s 下輪重試", seq)
                            return count
                        handled += 1
                    logger.info(
                        "收到 LINE 撤單 seq=%s reason=%s target_message=%s → 已處理 %s/%s 個指定 ID",
                        seq,
                        cancel_reason,
                        item.get("target_line_message_id") or "?",
                        handled,
                        len(target_ids),
                    )
                except Exception as e:
                    logger.warning("處理撤單失敗 seq=%s: %s", seq, e)
                    return count
                self._mark_seq(seq)
                count += 1
                continue

            if item.get("type") != "trade_signal":
                self._mark_seq(seq)
                continue

            payload = item.get("signal") or {}
            signal = _parsed_signal_from_payload(payload)
            if not _is_executable_signal(signal):
                if signal.direction in {"buy", "sell"} and not _sl_tp_consistent(signal):
                    logger.warning(
                        "rejected hub signal seq=%s: SL/TP on wrong side for %s (entry=%s sl=%s tp=%s) — skipped",
                        seq, signal.direction, signal.entry_price, signal.stop_loss, signal.take_profit,
                    )
                else:
                    logger.warning("invalid hub signal seq=%s: incomplete signal payload=%s", seq, payload)
                self._mark_seq(seq)
                continue

            source = str(item.get("source") or item.get("source_name") or "central")
            # 該來源被關掉就不跟這一單（仍要 mark_seq，否則會卡在同一筆重試）
            if not self.trade_manager.is_source_enabled(source):
                logger.info("來源「%s」已停用，略過 seq=%s", source, seq)
                self._mark_seq(seq)
                continue
            signal_id = str(item.get("execution_id") or f"copy_hub_{seq}")
            signal_id = self.trade_manager.submit_signal(
                signal,
                auto_execute=True,
                source_window=source,
                signal_id=signal_id,
            )
            order = self.trade_manager.get_order_status(signal_id)
            if order is not None and order.status == OrderStatus.FAILED:
                raise RuntimeError(f"failed to write MT5 command for hub seq={seq}")
            logger.info("submitted hub seq=%s as local signal %s: %s", seq, signal_id, signal)
            self._mark_seq(seq)
            count += 1
        # A member may be unable to see every source in the raw Hub page. Move
        # past those filtered records only after every visible event in this
        # batch was handled successfully. Early-return and exception paths above
        # deliberately do not reach this acknowledgement.
        try:
            filtered_cursor = int(getattr(self.hub, "last_cursor", 0) or 0)
        except (TypeError, ValueError):
            filtered_cursor = 0
        if filtered_cursor > self.last_seq:
            self._mark_seq(filtered_cursor)
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
