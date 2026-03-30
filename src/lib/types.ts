/* Data types matching Python sidecar JSON structures */

export interface AccountInfo {
  balance: number;
  equity: number;
  margin: number;
  free_margin: number;
  profit: number;
  timestamp?: number;
  timestamp_gmt?: number;
  gmt_offset?: number;
  server_time?: string;
}

export interface Position {
  ticket: number;
  type: number | string; // 0=buy, 1=sell
  volume: number;
  price_open: number;
  price_current: number;
  sl: number;
  tp: number;
  profit: number;
  comment: string;
}

export interface PendingOrder {
  ticket: number;
  type: number; // 2=buy_limit, 3=sell_limit, 4=buy_stop, 5=sell_stop
  volume: number;
  price: number;
  sl: number;
  tp: number;
  comment: string;
}

export interface ClosedTrade {
  ticket: number;
  position_id?: number;
  type: number | string;
  volume: number;
  // EA outputs entry_price/exit_price, not price_open/price_close
  entry_price: number;
  exit_price: number;
  profit: number;
  change_percent: number;
  // EA outputs open_timestamp / close_timestamp (Unix seconds)
  open_timestamp?: number;
  close_timestamp: number;
  comment: string;
  // Source window that produced the signal (enriched by mt5_reader)
  source_window?: string;
}

export interface MartingaleState {
  level: number;
  lot_size: number;
  consecutive_losses: number;
}

export interface TradingStats {
  daily_trades: number;
  wins: number;
  losses: number;
  daily_loss: number;
  api_calls: number;
}

export interface CaptureWindow {
  window_name: string;
  app_name: string;
  name: string;
}

export interface TradingConfig {
  default_lot_size: number;
  symbol_name: string;
  auto_execute: boolean;
  cancel_pending_after_seconds: number;
  use_martingale: boolean;
  martingale_lots: number[];
  martingale_max_level: number;
  martingale_per_source: boolean;
  martingale_source_lots: Record<string, number[]>;
  parser_mode: string;
  capture_interval: number;
  capture_windows: CaptureWindow[];
  ocr_confirm_count: number;
  ocr_confirm_delay: number;
  min_confidence: number;
  max_price_deviation: number;
  signal_dedup_minutes: number;
  max_daily_loss: number;
  max_open_positions: number;
  mt5_files_dir: string;
  partial_close_ratios: number[];
}

export interface LogEntry {
  timestamp: string;
  level: "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";
  message: string;
}

/* Sidecar event envelope */
export type SidecarEvent =
  | { event: "price"; data: { bid: number; ask: number } }
  | { event: "account"; data: AccountInfo }
  | { event: "positions"; data: Position[] }
  | { event: "orders"; data: PendingOrder[] }
  | { event: "trades"; data: ClosedTrade[] }
  | { event: "connection"; data: { connected: boolean } }
  | { event: "martingale"; data: MartingaleState }
  | { event: "stats"; data: TradingStats }
  | { event: "status"; data: { status: string } }
  | { event: "signal_detected"; data: { direction: string; price: number; confidence: number } }
  | { event: "trade_submitted"; data: { ticket: number; direction: string; volume: number; price: number } }
  | { event: "log"; data: LogEntry }
  | { event: "signal_skipped"; data: { reason: string; signal: string; source: string; details: string } }
  | { event: "config"; data: TradingConfig };

/* JSON-RPC request/response */
export interface JsonRpcRequest {
  id: number;
  method: string;
  params?: Record<string, unknown>;
}

export interface JsonRpcResponse {
  id: number;
  result?: unknown;
  error?: { code: number; message: string };
}
