import { useEffect, useRef, useState } from "react";
import { save } from "@tauri-apps/plugin-dialog";
import { writeTextFile } from "@tauri-apps/plugin-fs";
import { S, LOG_LEVELS } from "../lib/constants";
import { useTradingStore } from "../stores/tradingStore";

const LOG_LEVEL_COLORS: Record<string, string> = {
  DEBUG: "var(--color-ink-ghost)",
  INFO: "var(--color-ink-light)",
  WARNING: "var(--color-warning)",
  ERROR: "var(--color-loss)",
  CRITICAL: "#B33B3B",
};

const FILTER_OPTIONS = [
  { label: S.LOG_FILTER_ALL, minLevel: 0 },
  { label: S.LOG_FILTER_INFO, minLevel: 1 },
  { label: S.LOG_FILTER_WARNING, minLevel: 2 },
  { label: S.LOG_FILTER_ERROR, minLevel: 3 },
];

export default function Logs() {
  const logs = useTradingStore((s) => s.logs);
  const clearLogs = useTradingStore((s) => s.clearLogs);
  const [minLevel, setMinLevel] = useState(0);
  const [autoScroll, setAutoScroll] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  const filtered = logs.filter((l) => (LOG_LEVELS[l.level] ?? 0) >= minLevel);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [filtered.length, autoScroll]);

  async function handleExport() {
    const text = filtered.map((l) => `${l.timestamp} [${l.level}] ${l.message}`).join("\n");
    const filePath = await save({
      defaultPath: "copy_trader_log.txt",
      filters: [{ name: "文字檔案", extensions: ["txt"] }],
    });
    if (!filePath) return;
    await writeTextFile(filePath, text);
  }

  return (
    <div className="flex flex-col gap-4" style={{ height: "calc(100vh - var(--statusbar-height) - 80px)" }}>
      {/* Toolbar */}
      <div className="flex items-center gap-4 stagger-1 shrink-0">
        <span className="heading-md" style={{ fontFamily: "var(--font-serif)" }}>
          {S.LOG_TITLE}
        </span>

        <div className="flex-1" />

        <select
          className="form-input"
          style={{ minWidth: 130, padding: "5px 10px", fontSize: "12px" }}
          value={minLevel}
          onChange={(e) => setMinLevel(parseInt(e.target.value))}
        >
          {FILTER_OPTIONS.map((opt) => (
            <option key={opt.minLevel} value={opt.minLevel}>{opt.label}</option>
          ))}
        </select>

        <label
          className="flex items-center gap-2 cursor-pointer"
          style={{ fontSize: "12px", color: "var(--color-ink-muted)" }}
        >
          <input
            type="checkbox"
            checked={autoScroll}
            onChange={(e) => setAutoScroll(e.target.checked)}
            style={{ accentColor: "var(--color-gold)" }}
          />
          {S.LOG_AUTO_SCROLL}
        </label>

        <button onClick={clearLogs} className="btn-outline" style={{ padding: "5px 14px", fontSize: "11px" }}>
          {S.BTN_CLEAR}
        </button>
        <button onClick={handleExport} className="btn-outline" style={{ padding: "5px 14px", fontSize: "11px" }}>
          {S.BTN_EXPORT}
        </button>
      </div>

      {/* Log content */}
      <div ref={scrollRef} className="log-area flex-1 overflow-y-auto stagger-2">
        {filtered.map((entry, i) => (
          <div key={i} style={{ padding: "1px 0", color: LOG_LEVEL_COLORS[entry.level] ?? "var(--color-ink-light)" }}>
            <span style={{ color: "var(--color-ink-ghost)", marginRight: 10 }}>{entry.timestamp}</span>
            <span style={{ display: "inline-block", minWidth: 60, fontWeight: 500, marginRight: 8 }}>{entry.level}</span>
            {entry.message}
          </div>
        ))}
        {filtered.length === 0 && (
          <div className="text-center" style={{ padding: "48px 0", color: "var(--color-ink-ghost)", fontStyle: "italic" }}>
            尚無日誌紀錄
          </div>
        )}
      </div>
    </div>
  );
}
