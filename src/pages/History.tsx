import { useState, useMemo } from "react";
import { S } from "../lib/constants";
import {
  addDaysToDateInput,
  endOfMonthDateInput,
  formatTaipeiTradeTime,
  getMondayDateInput,
  getTaipeiDateInput,
  getTaipeiDateRangeUnix,
  normalizeMt5Timestamp,
  startOfMonthDateInput,
} from "../lib/time";
import { useTradingStore } from "../stores/tradingStore";

type FilterMode = "today" | "week" | "last_week" | "month" | "last_month" | "all" | "custom";

export default function History() {
  const trades = useTradingStore((s) => s.trades);
  const mt5TimeSkewSeconds = useTradingStore((s) => s.mt5TimeSkewSeconds);
  const [filterMode, setFilterMode] = useState<FilterMode>("all");
  const [dateFrom, setDateFrom] = useState(() => addDaysToDateInput(getTaipeiDateInput(), -30));
  const [dateTo, setDateTo] = useState(() => getTaipeiDateInput());

  const filtered = useMemo(() => {
    let tStart = 0;
    let tEnd = Infinity;

    if (filterMode === "all") {
      // show everything
    } else {
      tStart = getTaipeiDateRangeUnix(dateFrom).start;
      tEnd = getTaipeiDateRangeUnix(dateTo).end;
    }

    return trades
      .filter((t) => {
        const closeTimestamp = normalizeMt5Timestamp(t.close_timestamp, mt5TimeSkewSeconds);
        return closeTimestamp >= tStart && closeTimestamp <= tEnd;
      })
      .sort(
        (a, b) =>
          normalizeMt5Timestamp(a.close_timestamp, mt5TimeSkewSeconds) -
          normalizeMt5Timestamp(b.close_timestamp, mt5TimeSkewSeconds),
      );
  }, [trades, filterMode, dateFrom, dateTo, mt5TimeSkewSeconds]);

  const totalProfit = filtered.reduce((sum, t) => sum + t.profit, 0);
  const wins = filtered.filter((t) => t.profit >= 0).length;
  const losses = filtered.filter((t) => t.profit < 0).length;
  const total = wins + losses;
  const wr = total > 0 ? ((wins / total) * 100).toFixed(1) : "0.0";
  const pnlColor = totalProfit >= 0 ? "var(--color-profit)" : "var(--color-loss)";

  function selectFilter(mode: FilterMode) {
    setFilterMode(mode);
    const todayStr = getTaipeiDateInput();
    if (mode === "today") {
      setDateFrom(todayStr);
      setDateTo(todayStr);
    } else if (mode === "week") {
      setDateFrom(getMondayDateInput(todayStr));
      setDateTo(todayStr);
    } else if (mode === "last_week") {
      const thisMonday = getMondayDateInput(todayStr);
      setDateFrom(addDaysToDateInput(thisMonday, -7));
      setDateTo(addDaysToDateInput(thisMonday, -1));
    } else if (mode === "month") {
      setDateFrom(startOfMonthDateInput(todayStr));
      setDateTo(todayStr);
    } else if (mode === "last_month") {
      setDateFrom(startOfMonthDateInput(todayStr, -1));
      setDateTo(endOfMonthDateInput(todayStr, -1));
    } else if (mode === "all") {
      setDateFrom(addDaysToDateInput(todayStr, -365));
      setDateTo(todayStr);
    }
  }

  const filters: { mode: FilterMode; label: string }[] = [
    { mode: "today", label: S.FILTER_TODAY },
    { mode: "week", label: S.FILTER_THIS_WEEK },
    { mode: "last_week", label: "上週" },
    { mode: "month", label: "本月" },
    { mode: "last_month", label: "上月" },
    { mode: "all", label: S.FILTER_ALL },
  ];

  return (
    <div className="flex flex-col gap-6">
      {/* Summary */}
      <div className="section stagger-1">
        <div className="heading-sm" style={{ marginBottom: "16px" }}>交易摘要</div>
        <div
          className="grid grid-cols-5"
          style={{ borderTop: "1px solid var(--border-hairline)" }}
        >
          <SumStat label="總交易" value={String(total)} />
          <SumStat label="勝場" value={String(wins)} color="var(--color-profit)" />
          <SumStat label="敗場" value={String(losses)} color="var(--color-loss)" />
          <SumStat label="勝率" value={`${wr}%`} />
          <SumStat
            label="總盈虧"
            value={`${totalProfit >= 0 ? "+" : ""}$${Math.abs(totalProfit).toFixed(2)}`}
            color={pnlColor}
          />
        </div>
      </div>

      {/* Filter */}
      <div className="flex items-center gap-4 stagger-2">
        <div className="flex gap-1">
          {filters.map((f) => (
            <button
              key={f.mode}
              onClick={() => selectFilter(f.mode)}
              style={{
                padding: "7px 16px",
                fontSize: "12px",
                fontFamily: "var(--font-sans)",
                fontWeight: filterMode === f.mode ? 600 : 400,
                color: filterMode === f.mode ? "var(--color-ink)" : "var(--color-ink-muted)",
                background: filterMode === f.mode ? "var(--color-bg-surface)" : "transparent",
                border: filterMode === f.mode ? "1px solid var(--border-light)" : "1px solid transparent",
                borderRadius: "6px",
                cursor: "pointer",
                transition: "all 0.15s",
              }}
            >
              {f.label}
            </button>
          ))}
        </div>

        <div style={{ width: "1px", height: "20px", background: "var(--border-hairline)" }} />

        <div className="flex items-center gap-2">
          <span style={{ fontSize: "12px", color: "var(--color-ink-muted)" }}>{S.FILTER_FROM}</span>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            className="form-input"
            style={{ padding: "5px 10px", fontSize: "12px", fontFamily: "var(--font-mono)" }}
          />
          <span style={{ fontSize: "12px", color: "var(--color-ink-muted)" }}>{S.FILTER_TO}</span>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            className="form-input"
            style={{ padding: "5px 10px", fontSize: "12px", fontFamily: "var(--font-mono)" }}
          />
          <button onClick={() => setFilterMode("custom")} className="btn-outline" style={{ padding: "5px 14px", fontSize: "11px" }}>
            {S.FILTER_APPLY}
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="section stagger-3">
        <div className="heading-md" style={{ fontFamily: "var(--font-serif)", marginBottom: "16px" }}>
          {S.HISTORY_TITLE}
        </div>

        {filtered.length === 0 ? (
          <div className="text-center" style={{ padding: "48px 0", color: "var(--color-ink-ghost)", fontSize: "13px", fontStyle: "italic" }}>
            {S.EMPTY_HISTORY}
          </div>
        ) : (
          <div style={{ overflow: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>{S.POS_TICKET}</th>
                  <th>{S.POS_DIRECTION}</th>
                  <th>{S.POS_VOLUME}</th>
                  <th>{S.POS_ENTRY_PRICE}</th>
                  <th>{S.HISTORY_EXIT_PRICE}</th>
                  <th>{S.POS_PROFIT}</th>
                  <th>{S.HISTORY_CHANGE}</th>
                  <th>訊號來源</th>
                  <th>下單時間</th>
                  <th>{S.HISTORY_CLOSE_TIME}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((trade) => {
                  const isBuy = trade.type === 0 || trade.type === "buy";
                  const openTime = formatTaipeiTradeTime(trade.open_timestamp, mt5TimeSkewSeconds);
                  const closeTime = formatTaipeiTradeTime(trade.close_timestamp, mt5TimeSkewSeconds);
                  return (
                    <tr key={trade.ticket}>
                      <td>{trade.ticket}</td>
                      <td style={{ color: isBuy ? "var(--color-profit)" : "var(--color-loss)" }}>
                        {isBuy ? S.POS_BUY : S.POS_SELL}
                      </td>
                      <td>{trade.volume.toFixed(2)}</td>
                      <td>{trade.entry_price.toFixed(2)}</td>
                      <td>{trade.exit_price.toFixed(2)}</td>
                      <td style={{ color: trade.profit >= 0 ? "var(--color-profit)" : "var(--color-loss)", fontWeight: 500 }}>
                        {trade.profit >= 0 ? "+" : ""}{trade.profit.toFixed(2)}
                      </td>
                      <td style={{ color: trade.profit >= 0 ? "var(--color-profit)" : "var(--color-loss)" }}>
                        {trade.change_percent >= 0 ? "+" : ""}{trade.change_percent.toFixed(2)}%
                      </td>
                      <td style={{ fontSize: "11px", color: "var(--color-ink-muted)", maxWidth: "120px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {trade.source_window || "—"}
                      </td>
                      <td>{openTime}</td>
                      <td>{closeTime}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function SumStat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ padding: "14px 0", borderBottom: "1px solid var(--border-hairline)", textAlign: "center" }}>
      <div style={{ fontSize: "10px", letterSpacing: "0.04em", color: "var(--color-ink-muted)", marginBottom: "6px", textTransform: "uppercase" }}>{label}</div>
      <div className="num-md" style={{ color: color || "var(--color-ink)", fontSize: "14px" }}>{value}</div>
    </div>
  );
}
