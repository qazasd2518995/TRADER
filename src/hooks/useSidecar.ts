import { useEffect, useRef } from "react";
import { useTradingStore } from "../stores/tradingStore";
import type { SidecarEvent, JsonRpcRequest, TradingConfig } from "../lib/types";
import type { LoginResult, VerifyResult, UserData, CreateUserData } from "../lib/auth";

/*
 * Tauri sidecar IPC bridge.
 *
 * Push events (price, account, positions, etc.) → Tauri event system (emit/listen)
 * RPC responses (id + result/error) → Rust-side buffer + invoke polling (100% reliable)
 */

let _rpcId = 0;

/** Check if we're running inside Tauri v2 webview */
function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/** Module-level: eagerly load invoke() */
let _invoke: ((cmd: string, args: Record<string, unknown>) => Promise<unknown>) | null = null;

const _invokeReady: Promise<void> = isTauri()
  ? import("@tauri-apps/api/core")
      .then(({ invoke }) => {
        _invoke = invoke;
        console.log("[sidecar] invoke() ready");
      })
      .catch((err) => {
        console.error("[sidecar] Failed to load @tauri-apps/api/core:", err);
      })
  : Promise.resolve();

/**
 * Send a JSON-RPC command to the sidecar, then poll Rust buffer for the response.
 * This bypasses Tauri's event system for responses — 100% reliable.
 */
async function sendRpc<T = unknown>(method: string, params?: Record<string, unknown>): Promise<T> {
  await _invokeReady;
  if (!_invoke) throw new Error("Not running in Tauri environment");

  const id = ++_rpcId;
  const req: JsonRpcRequest = { id, method, params };
  const line = JSON.stringify(req);

  // Send command to sidecar via stdin
  try {
    await _invoke("send_to_sidecar", { message: line });
  } catch (err) {
    console.error(`[sidecar] Failed to send RPC #${id} (${method}):`, err);
    throw new Error(`Failed to send command: ${method}`);
  }

  // Poll Rust-side buffer for the response
  const startTime = Date.now();
  const timeout = 15000; // 15s timeout
  const pollInterval = 100; // poll every 100ms

  while (Date.now() - startTime < timeout) {
    try {
      const response = await _invoke("poll_rpc_response", { id }) as string | null;
      if (response) {
        const parsed = JSON.parse(response);
        if (parsed.error) {
          const err = parsed.error as { message?: string };
          throw new Error(err.message ?? "RPC error");
        }
        return parsed.result as T;
      }
    } catch (err) {
      // If it's our thrown error (from parsed.error), re-throw
      if (err instanceof Error && err.message !== "RPC error") {
        const msg = err.message;
        if (msg && !msg.includes("poll") && !msg.includes("invoke")) {
          throw err;
        }
      }
    }
    // Wait before next poll
    await new Promise((resolve) => setTimeout(resolve, pollInterval));
  }

  console.warn(`[sidecar] RPC #${id} (${method}) timed out after ${timeout}ms`);
  throw new Error(`RPC timeout: ${method}`);
}

/** Typed command helpers */
export const sidecarCommands = {
  startTrading: (config: TradingConfig) =>
    sendRpc("start_trading", config as unknown as Record<string, unknown>),
  stopTrading: () => sendRpc("stop_trading"),
  resetMartingale: () => sendRpc("reset_martingale"),
  saveConfig: (config: TradingConfig) =>
    sendRpc("save_config", config as unknown as Record<string, unknown>),
  getConfig: () => sendRpc<Record<string, unknown>>("get_config"),
  testMt5Connection: () => sendRpc<{ connected: boolean; age: number }>("test_mt5_connection"),
  detectWindows: () => sendRpc<{ windows: string[] }>("detect_windows"),
  detectMt5Dir: () => sendRpc<{ path: string; exists: boolean }>("detect_mt5_dir"),

  // Auth
  login: (email: string, password: string) =>
    sendRpc<LoginResult>("login", { email, password }),
  verifySubscription: (email: string) =>
    sendRpc<VerifyResult>("verify_subscription", { email }),
  changePassword: (email: string, oldPassword: string, newPassword: string) =>
    sendRpc("change_password", { email, old_password: oldPassword, new_password: newPassword }),

  // Admin
  adminListUsers: (adminEmail: string) =>
    sendRpc<{ users: UserData[] }>("admin_list_users", { admin_email: adminEmail }),
  adminCreateUser: (adminEmail: string, userData: CreateUserData) =>
    sendRpc("admin_create_user", { admin_email: adminEmail, ...userData }),
  adminUpdateUser: (adminEmail: string, email: string, updates: Partial<UserData>) =>
    sendRpc("admin_update_user", { admin_email: adminEmail, email, ...updates }),
  adminDeleteUser: (adminEmail: string, email: string) =>
    sendRpc("admin_delete_user", { admin_email: adminEmail, target_email: email }),
};

/** React hook — wire Tauri sidecar push events to Zustand */
export function useSidecar() {
  const unlistenRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function setup() {
      if (!isTauri()) return;

      try {
        const { listen } = await import("@tauri-apps/api/event");

        const unlisten = await listen<string>("sidecar-event", (event) => {
          if (cancelled) return;
          try {
            const raw = event.payload;
            const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
            // Only handle push events here (no "id" field)
            // RPC responses are handled by polling in sendRpc
            if ("id" in parsed) return;
            dispatchEvent(parsed as SidecarEvent);
          } catch {
            // ignore parse errors
          }
        });

        unlistenRef.current = unlisten;
        console.log("[sidecar] Event listener ready");
      } catch (err) {
        console.error("[sidecar] Failed to set up event listener:", err);
      }
    }

    setup();

    return () => {
      cancelled = true;
      unlistenRef.current?.();
    };
  }, []);

  return { sidecarCommands };
}

function dispatchEvent(ev: SidecarEvent) {
  const s = useTradingStore.getState();

  switch (ev.event) {
    case "price":
      s.setPrice(ev.data.bid, ev.data.ask);
      break;
    case "account":
      s.setAccount(ev.data);
      break;
    case "positions":
      s.setPositions(ev.data);
      break;
    case "orders":
      s.setOrders(ev.data);
      break;
    case "trades":
      s.setTrades(ev.data);
      break;
    case "connection":
      s.setConnected(ev.data.connected);
      break;
    case "martingale":
      s.setMartingale(ev.data);
      break;
    case "stats":
      s.setStats(ev.data);
      break;
    case "status":
      s.setIsTrading(ev.data.status === "running");
      s.setStartTime(ev.data.status === "running" ? s.startTime ?? Date.now() : null);
      s.setStatus(ev.data.status);
      break;
    case "log":
      s.addLog(ev.data);
      break;
    case "signal_skipped":
      // Also inject as a WARNING log so it's visible in the log viewer
      s.addLog({
        timestamp: new Date().toLocaleTimeString("en-GB", { hour12: false }),
        level: "WARNING",
        message: `⚠ 信號跳過 [${ev.data.source || ""}] 原因: ${ev.data.reason}${ev.data.signal ? ` | ${ev.data.signal}` : ""}${ev.data.details ? ` | ${ev.data.details}` : ""}`,
      });
      break;
    case "config":
      s.setConfig(ev.data as unknown as import("../lib/types").TradingConfig);
      break;
  }
}
