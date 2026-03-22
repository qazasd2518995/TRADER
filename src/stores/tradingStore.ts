import { create } from "zustand";
import type {
  AccountInfo,
  Position,
  PendingOrder,
  ClosedTrade,
  MartingaleState,
  TradingStats,
  TradingConfig,
  LogEntry,
} from "../lib/types";

interface TradingState {
  // Connection
  connected: boolean;
  setConnected: (v: boolean) => void;

  // Price
  bid: number;
  ask: number;
  setPrice: (bid: number, ask: number) => void;

  // Account
  account: AccountInfo;
  setAccount: (data: AccountInfo) => void;
  mt5TimeSkewSeconds: number;

  // Positions & orders
  positions: Position[];
  setPositions: (list: Position[]) => void;
  orders: PendingOrder[];
  setOrders: (list: PendingOrder[]) => void;

  // Trades (history)
  trades: ClosedTrade[];
  setTrades: (list: ClosedTrade[]) => void;

  // Martingale
  martingale: MartingaleState;
  setMartingale: (data: MartingaleState) => void;

  // Stats
  stats: TradingStats;
  setStats: (data: TradingStats) => void;

  // Trading state
  isTrading: boolean;
  setIsTrading: (v: boolean) => void;

  // Config
  config: TradingConfig | null;
  setConfig: (c: TradingConfig) => void;

  // Logs
  logs: LogEntry[];
  addLog: (entry: LogEntry) => void;
  clearLogs: () => void;

  // Status
  status: string;
  setStatus: (s: string) => void;

  // Uptime
  startTime: number | null;
  setStartTime: (t: number | null) => void;

  // Last data received timestamp
  lastDataTime: number;
  setLastDataTime: (t: number) => void;
}

export const useTradingStore = create<TradingState>((set) => ({
  connected: false,
  setConnected: (v) => set({ connected: v }),

  bid: 0,
  ask: 0,
  setPrice: (bid, ask) => set({ bid, ask, lastDataTime: Date.now() }),

  account: { balance: 0, equity: 0, margin: 0, free_margin: 0, profit: 0 },
  setAccount: (data) =>
    set((state) => {
      // Correct MT5 server timestamps to UTC using broker's GMT offset
      const gmtOffset = typeof data.gmt_offset === "number" ? data.gmt_offset : null;

      let mt5TimeSkewSeconds = state.mt5TimeSkewSeconds;
      if (gmtOffset !== null) {
        // EA provides gmt_offset: server_time - UTC = offset
        // To convert server timestamps to UTC: subtract offset
        mt5TimeSkewSeconds = -gmtOffset;
      }
      // Without gmt_offset (old EA): keep skew=0, dates may be off by broker TZ
      // but at least won't jump to "today"

      return {
        account: data,
        mt5TimeSkewSeconds,
        lastDataTime: Date.now(),
      };
    }),
  mt5TimeSkewSeconds: 0,

  positions: [],
  setPositions: (list) => set({ positions: list, lastDataTime: Date.now() }),

  orders: [],
  setOrders: (list) => set({ orders: list }),

  trades: [],
  setTrades: (list) => set({ trades: list }),

  martingale: { level: 0, lot_size: 0.01, consecutive_losses: 0 },
  setMartingale: (data) => set({ martingale: data }),

  stats: { daily_trades: 0, wins: 0, losses: 0, daily_loss: 0, api_calls: 0 },
  setStats: (data) => set({ stats: data }),

  isTrading: false,
  setIsTrading: (v) => set({ isTrading: v }),

  config: null,
  setConfig: (c) => set({ config: c }),

  logs: [],
  addLog: (entry) =>
    set((state) => ({
      logs: state.logs.length >= 5000
        ? [...state.logs.slice(-4000), entry]
        : [...state.logs, entry],
    })),
  clearLogs: () => set({ logs: [] }),

  status: "stopped",
  setStatus: (s) => set({ status: s }),

  startTime: null,
  setStartTime: (t) => set({ startTime: t }),

  lastDataTime: 0,
  setLastDataTime: (t) => set({ lastDataTime: t }),
}));
