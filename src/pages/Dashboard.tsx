import { useState } from "react";
import { S } from "../lib/constants";
import { useTradingStore } from "../stores/tradingStore";
import { useAuthStore } from "../stores/authStore";
import { getMaxWindows } from "../lib/auth";
import { sidecarCommands } from "../hooks/useSidecar";

export default function Dashboard() {
  const bid = useTradingStore((s) => s.bid);
  const ask = useTradingStore((s) => s.ask);
  const connected = useTradingStore((s) => s.connected);
  const account = useTradingStore((s) => s.account);
  const positions = useTradingStore((s) => s.positions);
  const martingale = useTradingStore((s) => s.martingale);
  const stats = useTradingStore((s) => s.stats);
  const isTrading = useTradingStore((s) => s.isTrading);
  const setIsTrading = useTradingStore((s) => s.setIsTrading);
  const setStartTime = useTradingStore((s) => s.setStartTime);
  const config = useTradingStore((s) => s.config);
  const symbolName = config?.symbol_name || "XAUUSD";

  const spread = bid > 0 ? Math.round((ask - bid) * 100) : 0;
  const profitColor = account.profit >= 0 ? "var(--color-profit)" : "var(--color-loss)";
  const profitSign = account.profit >= 0 ? "+" : "";

  const mgColor =
    martingale.level === 0
      ? "var(--color-ink)"
      : martingale.level <= 2
        ? "var(--color-warning)"
        : "var(--color-loss)";

  const dailyPnl = -stats.daily_loss;
  const pnlColor = dailyPnl >= 0 ? "var(--color-profit)" : "var(--color-loss)";
  const winRate = stats.daily_trades > 0 ? ((stats.wins / stats.daily_trades) * 100).toFixed(1) + "%" : "0%";

  async function handleStart() {
    if (isTrading || !config) return;
    try {
      await sidecarCommands.startTrading(config);
      setIsTrading(true);
      setStartTime(Date.now());
    } catch {
      setIsTrading(false);
      setStartTime(null);
    }
  }

  async function handleStop() {
    if (!isTrading) return;
    try {
      await sidecarCommands.stopTrading();
    } finally {
      setIsTrading(false);
      setStartTime(null);
    }
  }

  function handleResetMartingale() {
    if (confirm(S.CONFIRM_RESET_MARTINGALE)) {
      sidecarCommands.resetMartingale();
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Hero: Price + Status */}
      <div className="section stagger-1" style={{ padding: "24px 28px" }}>
        <div className="flex items-center justify-between">
          <div className="flex items-baseline gap-5">
            {/* Status pill */}
            <span
              className="flex items-center gap-2"
              style={{
                fontSize: "12px",
                fontFamily: "var(--font-mono)",
                color: isTrading ? "var(--color-profit)" : "var(--color-ink-muted)",
                padding: "5px 14px",
                borderRadius: "20px",
                background: isTrading ? "var(--color-profit-bg)" : "var(--color-bg-hover)",
              }}
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: isTrading ? "var(--color-profit)" : "var(--color-ink-ghost)",
                  display: "inline-block",
                }}
              />
              {isTrading ? S.STATUS_RUNNING : S.STATUS_STOPPED}
            </span>

            {/* Price display */}
            {connected && bid > 0 ? (
              <div className="flex items-baseline gap-3">
                <span
                  style={{
                    fontFamily: "var(--font-serif)",
                    fontSize: "11px",
                    fontWeight: 600,
                    letterSpacing: "0.12em",
                    textTransform: "uppercase",
                    color: "var(--color-gold)",
                  }}
                >
                  {symbolName}
                </span>
                <span className="num-lg" style={{ color: "var(--color-ink)" }}>
                  {bid.toFixed(2)}
                </span>
                <span style={{ color: "var(--color-ink-ghost)", fontSize: "18px", fontWeight: 300 }}>/</span>
                <span className="num-lg" style={{ color: "var(--color-ink)" }}>
                  {ask.toFixed(2)}
                </span>
                <span className="num-sm" style={{ color: "var(--color-ink-muted)" }}>
                  {spread} pts
                </span>
              </div>
            ) : (
              <span style={{ fontSize: "13px", color: "var(--color-ink-muted)" }}>
                非交易時段，即時價格暫停更新
              </span>
            )}
          </div>

          {/* Buttons */}
          <div className="flex gap-2">
            <button onClick={handleStart} disabled={isTrading} className="btn-primary" style={{ padding: "9px 20px", fontSize: "12px" }}>
              {S.BTN_START}
            </button>
            <button onClick={handleStop} disabled={!isTrading} className="btn-danger-outline" style={{ padding: "9px 20px" }}>
              {S.BTN_STOP}
            </button>
          </div>
        </div>
      </div>

      {/* Capture Windows */}
      <CaptureWindowsCard />

      {/* Account row */}
      <div className="section stagger-2">
        <div className="heading-sm" style={{ marginBottom: "16px" }}>{S.DASHBOARD_TITLE}</div>
        <div className="grid grid-cols-5 gap-0" style={{ borderTop: "1px solid var(--border-hairline)" }}>
          <AccountStat label={S.BALANCE} value={`$${account.balance.toLocaleString("en", { minimumFractionDigits: 2 })}`} color="var(--color-gold)" />
          <AccountStat label={S.EQUITY} value={`$${account.equity.toLocaleString("en", { minimumFractionDigits: 2 })}`} />
          <AccountStat label={S.MARGIN} value={`$${account.margin.toLocaleString("en", { minimumFractionDigits: 2 })}`} />
          <AccountStat label={S.FREE_MARGIN} value={`$${account.free_margin.toLocaleString("en", { minimumFractionDigits: 2 })}`} />
          <AccountStat label={S.PROFIT} value={`${profitSign}$${Math.abs(account.profit).toLocaleString("en", { minimumFractionDigits: 2 })}`} color={profitColor} />
        </div>
      </div>

      {/* Mid row */}
      <div className="grid grid-cols-2 gap-5 stagger-3">
        {/* Martingale */}
        <div
          className="section"
          style={martingale.level > 2 ? { borderLeft: `3px solid var(--color-loss)` } : {}}
        >
          <div className="flex items-center justify-between" style={{ marginBottom: "20px" }}>
            <span className="heading-md" style={{ fontFamily: "var(--font-serif)" }}>
              {S.MARTINGALE_STATUS}
            </span>
            <button onClick={handleResetMartingale} className="btn-outline" style={{ padding: "5px 12px", fontSize: "11px" }}>
              {S.BTN_RESET}
            </button>
          </div>
          <div className="grid grid-cols-3 text-center">
            <div>
              <div className="heading-sm" style={{ fontSize: "10px", marginBottom: "8px" }}>{S.CURRENT_LEVEL}</div>
              <div
                style={{
                  fontFamily: "var(--font-serif)",
                  fontSize: "42px",
                  fontWeight: 300,
                  lineHeight: 1,
                  color: mgColor,
                }}
              >
                {martingale.level + 1}
              </div>
            </div>
            <div>
              <div className="heading-sm" style={{ fontSize: "10px", marginBottom: "8px" }}>{S.LOT_SIZE}</div>
              <div className="num-lg" style={{ fontSize: "24px" }}>{martingale.lot_size.toFixed(2)}</div>
            </div>
            <div>
              <div className="heading-sm" style={{ fontSize: "10px", marginBottom: "8px" }}>{S.CONSECUTIVE_LOSSES}</div>
              <div className="num-lg" style={{ fontSize: "24px" }}>{martingale.consecutive_losses}</div>
            </div>
          </div>
        </div>

        {/* Today stats */}
        <div className="section">
          <span className="heading-md" style={{ fontFamily: "var(--font-serif)", display: "block", marginBottom: "16px" }}>
            {S.TODAY_STATS}
          </span>
          <div className="grid grid-cols-3 gap-x-4">
            <MiniStat label={S.TOTAL_TRADES} value={String(stats.daily_trades)} />
            <MiniStat label={S.WIN_COUNT} value={String(stats.wins)} color="var(--color-profit)" />
            <MiniStat label={S.LOSS_COUNT} value={String(stats.losses)} color="var(--color-loss)" />
            <MiniStat label={S.WIN_RATE} value={winRate} />
            <MiniStat label={S.DAILY_PNL} value={`${dailyPnl >= 0 ? "+" : ""}$${Math.abs(dailyPnl).toFixed(2)}`} color={pnlColor} />
            <MiniStat label={S.API_CALLS} value={String(stats.api_calls)} />
          </div>
        </div>
      </div>

      {/* Positions summary */}
      <div className="section stagger-4">
        <div className="flex items-center justify-between" style={{ marginBottom: "16px" }}>
          <span className="heading-md" style={{ fontFamily: "var(--font-serif)" }}>
            {S.NAV_POSITIONS}
          </span>
          <span className="num-sm" style={{ color: "var(--color-ink-muted)" }}>
            {positions.length} 筆
          </span>
        </div>

        {positions.length === 0 ? (
          <div
            className="text-center"
            style={{ padding: "40px 0", color: "var(--color-ink-ghost)", fontSize: "13px" }}
          >
            {S.EMPTY_POSITIONS}
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
                  <th>{S.POS_CURRENT_PRICE}</th>
                  <th>{S.POS_SL}</th>
                  <th>{S.POS_PROFIT}</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((pos) => {
                  const isBuy = pos.type === 0 || pos.type === "buy";
                  return (
                    <tr key={pos.ticket}>
                      <td>{pos.ticket}</td>
                      <td style={{ color: isBuy ? "var(--color-profit)" : "var(--color-loss)" }}>
                        {isBuy ? S.POS_BUY : S.POS_SELL}
                      </td>
                      <td>{pos.volume.toFixed(2)}</td>
                      <td>{pos.price_open.toFixed(2)}</td>
                      <td>{pos.price_current.toFixed(2)}</td>
                      <td>{pos.sl.toFixed(2)}</td>
                      <td style={{ color: pos.profit >= 0 ? "var(--color-profit)" : "var(--color-loss)", fontWeight: 500 }}>
                        {pos.profit >= 0 ? "+" : ""}{pos.profit.toFixed(2)}
                      </td>
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

function AccountStat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ padding: "16px 0", borderBottom: "1px solid var(--border-hairline)", textAlign: "center" }}>
      <div className="heading-sm" style={{ fontSize: "9px", marginBottom: "6px" }}>{label}</div>
      <div className="num-md" style={{ color: color || "var(--color-ink)", fontSize: "14px" }}>{value}</div>
    </div>
  );
}

function MiniStat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ padding: "10px 0", borderBottom: "1px solid var(--border-hairline)" }}>
      <div style={{ fontSize: "10px", color: "var(--color-ink-muted)", letterSpacing: "0.04em", marginBottom: "4px" }}>{label}</div>
      <div className="num-md" style={{ color: color || "var(--color-ink)", fontSize: "14px" }}>{value}</div>
    </div>
  );
}

function CaptureWindowsCard() {
  const config = useTradingStore((s) => s.config);
  const setConfig = useTradingStore((s) => s.setConfig);
  const userPlan = useAuthStore((s) => s.user?.plan ?? "trial");
  const maxWindows = getMaxWindows(userPlan);

  const [showModal, setShowModal] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [detectedWindows, setDetectedWindows] = useState<string[]>([]);
  const [manualName, setManualName] = useState("");

  if (!config) return null;
  const windows = config.capture_windows ?? [];

  function addWindow(title: string) {
    if (!config) return;
    if (windows.some((w) => w.window_name === title)) return;
    if (windows.length >= maxWindows) {
      alert(`您的方案最多可監控 ${maxWindows} 個群組，請升級方案。`);
      return;
    }
    const newWindows = [...windows, { window_name: title, app_name: "LINE", name: `win_${windows.length}` }];
    const updated = { ...config, capture_windows: newWindows };
    setConfig(updated as import("../lib/types").TradingConfig);
  }

  function removeWindow(idx: number) {
    if (!config) return;
    const newWindows = windows.filter((_, i) => i !== idx);
    const updated = { ...config, capture_windows: newWindows };
    setConfig(updated as import("../lib/types").TradingConfig);
  }

  async function openPicker() {
    setShowModal(true);
    setDetecting(true);
    setDetectedWindows([]);
    try {
      const res = await sidecarCommands.detectWindows();
      setDetectedWindows(res.windows ?? []);
    } catch { /* empty */ } finally {
      setDetecting(false);
    }
  }

  return (
    <div className="section stagger-2">
      <div className="flex items-center justify-between" style={{ marginBottom: "16px" }}>
        <span className="heading-md" style={{ fontFamily: "var(--font-serif)" }}>
          監控群組
        </span>
        <span className="num-sm" style={{ color: "var(--color-ink-muted)" }}>
          {windows.length}/{maxWindows === Infinity ? "∞" : maxWindows}
        </span>
      </div>

      {windows.length === 0 ? (
        <div style={{ padding: "20px 0", color: "var(--color-ink-ghost)", fontSize: "13px", fontStyle: "italic", textAlign: "center" }}>
          尚未選擇任何監控群組
        </div>
      ) : (
        <div style={{ border: "1px solid var(--border-hairline)", borderRadius: "6px", overflow: "hidden", marginBottom: "12px" }}>
          {windows.map((w, i) => (
            <div key={i} className="flex items-center justify-between"
              style={{ padding: "10px 14px", fontSize: "13px", borderBottom: i < windows.length - 1 ? "1px solid var(--border-hairline)" : undefined }}>
              <div className="flex items-center gap-3">
                <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--color-profit)", display: "inline-block" }} />
                <span style={{ color: "var(--color-ink-light)" }}>{w.window_name}</span>
              </div>
              <button onClick={() => removeWindow(i)}
                style={{ background: "none", border: "none", color: "var(--color-loss)", cursor: "pointer", fontSize: "14px" }}>
                &times;
              </button>
            </div>
          ))}
        </div>
      )}

      <button onClick={openPicker} className="btn-outline" style={{ fontSize: "12px" }}>
        + 新增監控群組
      </button>

      {/* Modal */}
      {showModal && (
        <div style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)", display: "flex", alignItems: "center", justifyContent: "center" }}
          onClick={(e) => { if (e.target === e.currentTarget) setShowModal(false); }}>
          <div style={{ background: "var(--bg-card, #111)", border: "1px solid var(--border-hairline)", borderRadius: "10px", width: 480, maxHeight: "70vh", display: "flex", flexDirection: "column", boxShadow: "0 20px 60px rgba(0,0,0,0.5)" }}>
            <div className="flex items-center justify-between" style={{ padding: "16px 20px", borderBottom: "1px solid var(--border-hairline)" }}>
              <span style={{ fontSize: "15px", fontWeight: 600 }}>選取監控群組</span>
              <button onClick={() => setShowModal(false)} style={{ background: "none", border: "none", color: "var(--color-ink-muted)", cursor: "pointer", fontSize: "18px" }}>&times;</button>
            </div>
            <div className="flex gap-2" style={{ padding: "12px 20px", borderBottom: "1px solid var(--border-hairline)" }}>
              <input type="text" className="form-input" style={{ flex: 1 }} placeholder="手動輸入視窗名稱" value={manualName}
                onChange={(e) => setManualName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && manualName.trim()) { addWindow(manualName.trim()); setManualName(""); }}} />
              <button onClick={() => { if (manualName.trim()) { addWindow(manualName.trim()); setManualName(""); }}} className="btn-outline" disabled={!manualName.trim()}>加入</button>
            </div>
            <div style={{ flex: 1, overflowY: "auto", padding: "8px 0" }}>
              {detecting ? (
                <div style={{ padding: "30px 0", textAlign: "center", color: "var(--color-ink-muted)", fontSize: "13px" }}>偵測中...</div>
              ) : detectedWindows.length === 0 ? (
                <div style={{ padding: "30px 0", textAlign: "center", color: "var(--color-ink-ghost)", fontSize: "13px" }}>未偵測到視窗，請手動輸入</div>
              ) : (
                <>
                  <div style={{ padding: "4px 20px 8px", fontSize: "11px", color: "var(--color-ink-ghost)" }}>偵測到 {detectedWindows.length} 個視窗</div>
                  {detectedWindows.map((title, i) => {
                    const already = windows.some((w) => w.window_name === title);
                    return (
                      <div key={i} onClick={() => { if (!already) addWindow(title); }}
                        style={{ padding: "9px 20px", fontSize: "13px", cursor: already ? "default" : "pointer", opacity: already ? 0.4 : 1, color: "var(--color-ink-light)" }}
                        onMouseEnter={(e) => { if (!already) e.currentTarget.style.background = "rgba(255,255,255,0.04)"; }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>
                        <span>{title}</span>
                        {already && <span style={{ marginLeft: "8px", fontSize: "11px", color: "var(--color-ink-ghost)" }}>已加入</span>}
                      </div>
                    );
                  })}
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
