import { useEffect, useState } from "react";
import { S } from "../lib/constants";
import { useTradingStore } from "../stores/tradingStore";

function formatUptime(ms: number): string {
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

export default function StatusBar() {
  const connected = useTradingStore((s) => s.connected);
  const bid = useTradingStore((s) => s.bid);
  const ask = useTradingStore((s) => s.ask);
  const isTrading = useTradingStore((s) => s.isTrading);
  const status = useTradingStore((s) => s.status);
  const startTime = useTradingStore((s) => s.startTime);
  const martingale = useTradingStore((s) => s.martingale);
  const lastDataTime = useTradingStore((s) => s.lastDataTime);
  const symbolName = useTradingStore((s) => s.config?.symbol_name || "XAUUSD");
  const isStarting = status === "starting";

  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  let heartbeat = "等待資料";
  let heartbeatColor = "var(--color-ink-ghost)";
  if (lastDataTime > 0) {
    const ago = Math.floor((now - lastDataTime) / 1000);
    if (ago <= 5) {
      heartbeat = "正常";
      heartbeatColor = "var(--color-profit)";
    } else if (ago <= 15) {
      heartbeat = `延遲 ${ago}s`;
      heartbeatColor = "var(--color-warning)";
    } else {
      heartbeat = `無回應 ${ago}s`;
      heartbeatColor = "var(--color-loss)";
    }
  }

  const tradingLabel = isStarting
    ? S.STATUS_STARTING
    : isTrading
      ? S.STATUS_RUNNING
      : status === "error"
        ? S.STATUS_ERROR
        : S.STATUS_STOPPED;
  const tradingColor = isStarting
    ? "var(--color-warning)"
    : isTrading
      ? "var(--color-profit)"
      : status === "error"
        ? "var(--color-loss)"
        : "var(--color-ink-ghost)";

  return (
    <div
      className="flex items-center gap-6 shrink-0"
      style={{
        height: "var(--statusbar-height)",
        minHeight: "var(--statusbar-height)",
        padding: "0 24px",
        borderTop: "1px solid var(--border-hairline)",
        background: "var(--color-bg-sidebar)",
        fontFamily: "var(--font-mono)",
        fontSize: "11px",
        color: "var(--color-ink-muted)",
      }}
    >
      {/* Heartbeat */}
      <div className="flex items-center gap-2">
        <span
          style={{
            width: 5,
            height: 5,
            borderRadius: "50%",
            background: heartbeatColor,
          }}
        />
        <span>{heartbeat}</span>
      </div>

      {/* Separator */}
      <span style={{ color: "var(--color-ink-ghost)" }}>/</span>

      {/* MT5 */}
      <span>
        MT5{" "}
        <span style={{ color: connected ? "var(--color-profit)" : "var(--color-loss)" }}>
          {connected ? S.STATUS_CONNECTED : S.STATUS_DISCONNECTED}
        </span>
      </span>

      <span style={{ color: "var(--color-ink-ghost)" }}>/</span>

      {/* Price */}
      <span>
        {symbolName}{" "}
        <span style={{ color: "var(--color-gold)", fontWeight: 500 }}>
          {bid > 0 ? `${bid.toFixed(2)} / ${ask.toFixed(2)}` : "---"}
        </span>
      </span>

      <span style={{ color: "var(--color-ink-ghost)" }}>/</span>

      {/* Martingale */}
      <span>
        {S.CURRENT_LEVEL} {martingale.level + 1} ({martingale.lot_size}手)
      </span>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Status */}
      <span style={{ color: tradingColor }}>
        {tradingLabel}
      </span>

      <span style={{ color: "var(--color-ink-ghost)" }}>/</span>

      {/* Uptime */}
      <span>{startTime ? formatUptime(now - startTime) : "--:--:--"}</span>
    </div>
  );
}
