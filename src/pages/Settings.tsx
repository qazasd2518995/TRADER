import { useState, useEffect } from "react";
import { S } from "../lib/constants";
import { useTradingStore } from "../stores/tradingStore";
import { useAuthStore } from "../stores/authStore";
import { getMaxWindows } from "../lib/auth";
import { sidecarCommands } from "../hooks/useSidecar";
import type { TradingConfig } from "../lib/types";

const TABS = [S.SETTINGS_TRADING, S.SETTINGS_CAPTURE, S.SETTINGS_SAFETY, S.SETTINGS_MT5] as const;

function defaultConfig(): TradingConfig {
  return {
    default_lot_size: 0.01,
    symbol_name: "XAUUSD.s",
    auto_execute: true,
    cancel_pending_after_seconds: 1800,
    use_martingale: true,
    martingale_per_source: false,
    martingale_source_lots: {},
    martingale_lots: [0.01, 0.02, 0.04, 0.08, 0.16],
    martingale_max_level: 4,
    parser_mode: "regex",
    capture_interval: 2.0,
    capture_windows: [],
    ocr_confirm_count: 2,
    ocr_confirm_delay: 1.0,
    min_confidence: 0.85,
    max_price_deviation: 0.01,
    signal_dedup_minutes: 5,
    max_daily_loss: 500,
    max_open_positions: 5,
    mt5_files_dir: "",
    partial_close_ratios: [0.5, 0.3, 0.2],
  };
}

export default function Settings() {
  const storeConfig = useTradingStore((s) => s.config);
  const setConfig = useTradingStore((s) => s.setConfig);
  const [activeTab, setActiveTab] = useState(0);
  const [cfg, setCfg] = useState<TradingConfig>(storeConfig ?? defaultConfig());
  const [dirty, setDirty] = useState(false); // Track if user has unsaved edits

  // Only sync from store when user has NOT made local edits
  useEffect(() => {
    if (storeConfig && !dirty) setCfg(storeConfig);
  }, [storeConfig, dirty]);

  function update<K extends keyof TradingConfig>(key: K, value: TradingConfig[K]) {
    setDirty(true);
    setCfg((prev) => ({ ...prev, [key]: value }));
  }

  function updateLot(index: number, value: number) {
    setDirty(true);
    const lots = [...cfg.martingale_lots];
    lots[index] = value;
    setCfg((prev) => ({ ...prev, martingale_lots: lots }));
  }

  function updateRatio(index: number, value: number) {
    setDirty(true);
    const ratios = [...(cfg.partial_close_ratios ?? [0.5, 0.3, 0.2])];
    ratios[index] = Math.max(0, Math.min(1, value));
    setCfg((prev) => ({ ...prev, partial_close_ratios: ratios }));
  }

  function addRatio() {
    const ratios = [...(cfg.partial_close_ratios ?? [])];
    const used = ratios.reduce((sum, r) => sum + r, 0);
    const remaining = Math.max(0, Math.round((1 - used) * 100) / 100);
    ratios.push(remaining || 0.1);
    setCfg((prev) => ({ ...prev, partial_close_ratios: ratios }));
  }

  function removeRatio() {
    const ratios = [...(cfg.partial_close_ratios ?? [])];
    if (ratios.length > 1) {
      setCfg((prev) => ({ ...prev, partial_close_ratios: ratios.slice(0, -1) }));
    }
  }

  function addLevel() {
    const lots = [...cfg.martingale_lots];
    const last = lots[lots.length - 1] ?? 0.01;
    lots.push(Math.round(last * 2 * 100) / 100);
    setCfg((prev) => ({ ...prev, martingale_lots: lots }));
  }

  function removeLevel() {
    if (cfg.martingale_lots.length > 1) {
      setCfg((prev) => ({ ...prev, martingale_lots: prev.martingale_lots.slice(0, -1) }));
    }
  }

  async function handleSave() {
    setConfig(cfg);
    await sidecarCommands.saveConfig(cfg);
    setDirty(false);
    alert(S.SETTINGS_SAVED);
  }

  function handleReset() {
    if (confirm(S.CONFIRM_RESET)) {
      const def = defaultConfig();
      setCfg(def);
      setConfig(def);
      setDirty(false);
    }
  }

  const [showWindowModal, setShowWindowModal] = useState(false);
  const [detectedWindows, setDetectedWindows] = useState<string[]>([]);
  const [detecting, setDetecting] = useState(false);
  const [manualWindowName, setManualWindowName] = useState("");

  async function openWindowPicker() {
    setShowWindowModal(true);
    setDetecting(true);
    setDetectedWindows([]);
    try {
      const res = await sidecarCommands.detectWindows();
      setDetectedWindows(res.windows ?? []);
    } catch {
      // modal stays open with empty list, user can still type manually
    } finally {
      setDetecting(false);
    }
  }

  const userPlan = useAuthStore((s) => s.user?.plan ?? "trial");
  const maxWindows = getMaxWindows(userPlan);

  function addWindow(title: string) {
    if (cfg.capture_windows.some((w) => w.window_name === title)) return;
    if (cfg.capture_windows.length >= maxWindows) {
      alert(`您的方案（${userPlan}）最多可監控 ${maxWindows} 個群組，請升級方案以新增更多群組。`);
      return;
    }
    const newWindows = [
      ...cfg.capture_windows,
      { window_name: title, app_name: "LINE", name: `win_${cfg.capture_windows.length}` },
    ];
    const updated = { ...cfg, capture_windows: newWindows };
    setCfg(updated);
    setConfig(updated); // persist to store so it survives navigation
  }

  function handleAddManualWindow() {
    const name = manualWindowName.trim();
    if (!name) return;
    addWindow(name);
    setManualWindowName("");
  }

  const [testing, setTesting] = useState(false);
  const [detectingMt5, setDetectingMt5] = useState(false);

  async function handleDetectMt5Dir() {
    setDetectingMt5(true);
    try {
      // Try dedicated RPC first, fallback to get_config (which has auto-detected path)
      let path = "";
      try {
        const res = await sidecarCommands.detectMt5Dir();
        path = res.path;
      } catch {
        // Fallback: reload config from sidecar — Python auto-detects the path
        const configRes = await sidecarCommands.getConfig() as unknown as TradingConfig;
        path = configRes?.mt5_files_dir ?? "";
      }
      if (path) {
        update("mt5_files_dir", path);
        alert(`已偵測到 MT5 路徑：\n${path}`);
      } else {
        alert("未找到 MT5 安裝路徑，請手動輸入");
      }
    } catch {
      alert("自動偵測失敗，請手動輸入路徑");
    } finally {
      setDetectingMt5(false);
    }
  }

  async function handleTestConnection() {
    setTesting(true);
    try {
      const res = await sidecarCommands.testMt5Connection();
      if (res.connected) {
        alert(`MT5 連線正常（資料延遲 ${res.age} 秒）`);
      } else {
        alert("MT5 未連線 — 請確認 MT5 及 Bridge EA 已啟動");
      }
    } catch {
      alert("連線測試失敗");
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      {/* Tabs */}
      <div className="flex gap-0" style={{ borderBottom: "1px solid var(--border-hairline)" }}>
        {TABS.map((tab, i) => (
          <button
            key={tab}
            onClick={() => setActiveTab(i)}
            style={{
              padding: "12px 20px",
              fontSize: "13px",
              fontFamily: "var(--font-sans)",
              fontWeight: activeTab === i ? 600 : 400,
              color: activeTab === i ? "var(--color-ink)" : "var(--color-ink-muted)",
              background: "transparent",
              border: "none",
              borderBottom: activeTab === i ? "2px solid var(--color-gold)" : "2px solid transparent",
              cursor: "pointer",
              transition: "all 0.15s",
            }}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Trading tab */}
      {activeTab === 0 && (
        <>
          <div className="section stagger-1">
            <div className="heading-md" style={{ fontFamily: "var(--font-serif)", marginBottom: "20px" }}>
              {S.SETTINGS_TRADING}
            </div>
            <div className="divider" style={{ marginBottom: "16px" }} />

            <FormRow label={S.DEFAULT_LOT_SIZE}>
              <input type="number" className="form-input" style={{ width: 120 }} step="0.01" min="0.01" max="10"
                value={cfg.default_lot_size} onChange={(e) => update("default_lot_size", parseFloat(e.target.value) || 0.01)} />
            </FormRow>
            <FormRow label={S.SYMBOL_NAME}>
              <input type="text" className="form-input" style={{ width: 200 }}
                value={cfg.symbol_name} onChange={(e) => update("symbol_name", e.target.value)} />
            </FormRow>
            <FormRow label={S.AUTO_EXECUTE}>
              <Toggle checked={cfg.auto_execute} onChange={(v) => update("auto_execute", v)} />
            </FormRow>
            <FormRow label={S.CANCEL_TIMEOUT}>
              <input type="number" className="form-input" style={{ width: 120 }} min="60" max="86400"
                value={cfg.cancel_pending_after_seconds} onChange={(e) => update("cancel_pending_after_seconds", parseInt(e.target.value) || 1800)} />
            </FormRow>
          </div>

          <div className="section stagger-2">
            <div className="heading-md" style={{ fontFamily: "var(--font-serif)", marginBottom: "20px" }}>
              {S.MARTINGALE_STATUS}
            </div>
            <div className="divider" style={{ marginBottom: "16px" }} />

            <FormRow label={S.USE_MARTINGALE}>
              <Toggle checked={cfg.use_martingale} onChange={(v) => update("use_martingale", v)} />
            </FormRow>

            {cfg.use_martingale ? (
              <>
                <FormRow label="馬丁模式">
                  <select className="form-input" style={{ width: 220 }}
                    value={cfg.martingale_per_source ? "per_source" : "global"}
                    onChange={(e) => update("martingale_per_source", e.target.value === "per_source")}>
                    <option value="global">全域共用（所有群合併計算）</option>
                    <option value="per_source">各群獨立（每群各自計算）</option>
                  </select>
                </FormRow>

                {/* Global lot table (always shown) */}
                <div style={{ fontSize: "12px", color: "var(--color-ink-muted)", margin: "12px 0 8px" }}>
                  {cfg.martingale_per_source ? "全域預設手數（未單獨設定的群使用此值）" : "每層手數設定"}
                </div>

                <table className="data-table" style={{ maxWidth: 320 }}>
                  <thead>
                    <tr>
                      <th>{S.MARTINGALE_COL_LEVEL}</th>
                      <th>{S.MARTINGALE_COL_LOT}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cfg.martingale_lots.map((lot, i) => (
                      <tr key={i}>
                        <td style={{ textAlign: "center" }}>{i + 1}</td>
                        <td>
                          <input type="number" className="form-input" style={{ width: 80, padding: "4px 8px", textAlign: "center" }}
                            step="0.01" min="0.01" value={lot}
                            onChange={(e) => updateLot(i, parseFloat(e.target.value) || 0.01)} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                <div className="flex gap-2" style={{ marginTop: "12px" }}>
                  <button onClick={addLevel} className="btn-outline">{S.BTN_ADD}層級</button>
                  <button onClick={removeLevel} className="btn-danger-outline">{S.BTN_REMOVE}最後</button>
                </div>

                {/* Per-source lot tables (only in per_source mode) */}
                {cfg.martingale_per_source && cfg.capture_windows.length > 0 && (
                  <>
                    {cfg.capture_windows.map((w) => {
                      const sourceLots: number[] = (cfg.martingale_source_lots ?? {})[w.window_name] ?? [];
                      const updateSourceLot = (idx: number, val: number) => {
                        const newLots = [...sourceLots];
                        newLots[idx] = val;
                        const newMap = { ...(cfg.martingale_source_lots ?? {}), [w.window_name]: newLots };
                        const updated = { ...cfg, martingale_source_lots: newMap };
                        setCfg(updated);
                        setConfig(updated);
                        setDirty(true);
                      };
                      const addSourceLevel = () => {
                        const last = sourceLots.length > 0 ? sourceLots[sourceLots.length - 1] * 2 : 0.01;
                        const newLots = [...sourceLots, Math.round(last * 100) / 100];
                        const newMap = { ...(cfg.martingale_source_lots ?? {}), [w.window_name]: newLots };
                        const updated = { ...cfg, martingale_source_lots: newMap };
                        setCfg(updated);
                        setConfig(updated);
                        setDirty(true);
                      };
                      const removeSourceLevel = () => {
                        if (sourceLots.length <= 0) return;
                        const newLots = sourceLots.slice(0, -1);
                        const newMap = { ...(cfg.martingale_source_lots ?? {}), [w.window_name]: newLots };
                        const updated = { ...cfg, martingale_source_lots: newMap };
                        setCfg(updated);
                        setConfig(updated);
                        setDirty(true);
                      };

                      return (
                        <div key={w.window_name} style={{ marginTop: "16px", padding: "12px", border: "1px solid var(--border-hairline)", borderRadius: "8px" }}>
                          <div style={{ fontSize: "12px", color: "var(--color-ink-muted)", marginBottom: "8px" }}>
                            {w.window_name} {sourceLots.length === 0 && "（使用全域預設）"}
                          </div>
                          {sourceLots.length > 0 && (
                            <table className="data-table" style={{ maxWidth: 280 }}>
                              <thead><tr><th>層級</th><th>手數</th></tr></thead>
                              <tbody>
                                {sourceLots.map((lot, i) => (
                                  <tr key={i}>
                                    <td style={{ textAlign: "center" }}>{i + 1}</td>
                                    <td>
                                      <input type="number" className="form-input" style={{ width: 80, padding: "4px 8px", textAlign: "center" }}
                                        step="0.01" min="0.01" value={lot}
                                        onChange={(e) => updateSourceLot(i, parseFloat(e.target.value) || 0.01)} />
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                          <div className="flex gap-2" style={{ marginTop: "8px" }}>
                            <button onClick={addSourceLevel} className="btn-outline" style={{ fontSize: "11px", padding: "4px 10px" }}>新增層級</button>
                            <button onClick={removeSourceLevel} className="btn-danger-outline" style={{ fontSize: "11px", padding: "4px 10px" }}>移除最後</button>
                          </div>
                        </div>
                      );
                    })}
                  </>
                )}
              </>
            ) : (
              <FormRow label="固定下單手數">
                <input type="number" className="form-input" style={{ width: 120 }} step="0.01" min="0.01" max="10"
                  value={cfg.default_lot_size} onChange={(e) => update("default_lot_size", parseFloat(e.target.value) || 0.01)} />
                <span style={{ fontSize: "12px", color: "var(--color-ink-muted)", marginLeft: "8px" }}>手</span>
              </FormRow>
            )}
          </div>

          <div className="section stagger-3">
            <div className="heading-md" style={{ fontFamily: "var(--font-serif)", marginBottom: "20px" }}>
              {S.PARTIAL_CLOSE_TITLE}
            </div>
            <div className="divider" style={{ marginBottom: "16px" }} />

            <div style={{ fontSize: "12px", color: "var(--color-ink-muted)", marginBottom: "12px", lineHeight: 1.6 }}>
              當訊號包含多個 TP 時，到達每個 TP 會依比例分批平倉。
            </div>

            {(() => {
              const ratios = cfg.partial_close_ratios ?? [0.5, 0.3, 0.2];
              const totalRatio = ratios.reduce((s, r) => s + r, 0);
              const overLimit = totalRatio > 1.001;

              return (
                <>
                  <table className="data-table" style={{ maxWidth: 420 }}>
                    <thead>
                      <tr>
                        <th>{S.PARTIAL_CLOSE_TP}</th>
                        <th>{S.PARTIAL_CLOSE_RATIO}</th>
                        <th>{S.PARTIAL_CLOSE_PREVIEW}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ratios.map((ratio, i) => {
                        let preview = ratio;
                        for (let j = 0; j < i; j++) {
                          preview = preview; // each ratio is of remaining, but stored as absolute
                        }
                        return (
                          <tr key={i}>
                            <td style={{ textAlign: "center", fontFamily: "var(--font-mono)" }}>TP{i + 1}</td>
                            <td>
                              <div className="flex items-center gap-2">
                                <input
                                  type="number"
                                  className="form-input"
                                  style={{ width: 80, padding: "4px 8px", textAlign: "center" }}
                                  step="0.05"
                                  min="0.05"
                                  max="1"
                                  value={ratio}
                                  onChange={(e) => updateRatio(i, parseFloat(e.target.value) || 0.1)}
                                />
                                <span style={{ fontSize: "11px", color: "var(--color-ink-muted)" }}>
                                  ({Math.round(ratio * 100)}%)
                                </span>
                              </div>
                            </td>
                            <td style={{ fontFamily: "var(--font-mono)", fontSize: "12px" }}>
                              {i < ratios.length - 1 ? (
                                <span>平倉 {Math.round(preview * 100)}%</span>
                              ) : (
                                <span style={{ color: "var(--color-ink-muted)" }}>MT5 自動平倉</span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>

                  {overLimit && (
                    <div style={{
                      padding: "8px 12px", borderRadius: "6px", marginTop: "8px",
                      background: "var(--color-loss-bg)", color: "var(--color-loss)", fontSize: "12px",
                    }}>
                      {S.PARTIAL_CLOSE_TOTAL_ERROR}（目前 {Math.round(totalRatio * 100)}%）
                    </div>
                  )}

                  <div style={{ fontSize: "11px", color: "var(--color-ink-faint)", marginTop: "8px" }}>
                    {S.PARTIAL_CLOSE_LAST_TP_NOTE}
                  </div>

                  <div className="flex gap-2" style={{ marginTop: "12px" }}>
                    <button onClick={addRatio} className="btn-outline">{S.BTN_ADD} TP</button>
                    <button onClick={removeRatio} className="btn-danger-outline" disabled={ratios.length <= 1}>{S.BTN_REMOVE}最後</button>
                  </div>
                </>
              );
            })()}
          </div>
        </>
      )}

      {/* Capture tab — moved to Dashboard, show redirect */}
      {activeTab === 1 && (
        <>
          <div className="section stagger-1">
            <div className="heading-md" style={{ fontFamily: "var(--font-serif)", marginBottom: "20px" }}>
              {S.CAPTURE_WINDOWS}
            </div>
            <div className="divider" style={{ marginBottom: "16px" }} />

            <div style={{ fontSize: "13px", color: "var(--color-ink-muted)", padding: "20px 0", textAlign: "center" }}>
              監控群組設定已移至「儀表板」頁面，方便即時調整。
            </div>

            {cfg.capture_windows.length === 0 ? (
              <div style={{ padding: "20px 0", color: "var(--color-ink-ghost)", fontSize: "13px", fontStyle: "italic", textAlign: "center" }}>
                尚未選擇任何視窗
              </div>
            ) : (
              <div style={{ border: "1px solid var(--border-hairline)", borderRadius: "6px", overflow: "hidden", marginBottom: "12px" }}>
                {cfg.capture_windows.map((w, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between"
                    style={{
                      padding: "10px 14px",
                      fontSize: "13px",
                      borderBottom: i < cfg.capture_windows.length - 1 ? "1px solid var(--border-hairline)" : undefined,
                    }}
                  >
                    <span style={{ color: "var(--color-ink-light)" }}>{w.window_name} | {w.app_name}</span>
                    <button
                      onClick={() => {
                        const newWindows = cfg.capture_windows.filter((_, j) => j !== i);
                        const updated = { ...cfg, capture_windows: newWindows };
                        setCfg(updated);
                        setConfig(updated);
                      }}
                      style={{ background: "none", border: "none", color: "var(--color-loss)", cursor: "pointer", fontSize: "14px" }}
                    >
                      &times;
                    </button>
                  </div>
                ))}
              </div>
            )}

            <button onClick={openWindowPicker} className="btn-outline">
              + 新增監控視窗
            </button>

            {/* Window picker modal */}
            {showWindowModal && (
              <div
                style={{
                  position: "fixed", inset: 0, zIndex: 1000,
                  background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}
                onClick={(e) => { if (e.target === e.currentTarget) setShowWindowModal(false); }}
              >
                <div style={{
                  background: "var(--bg-card, #111)", border: "1px solid var(--border-hairline)",
                  borderRadius: "10px", width: 480, maxHeight: "70vh", display: "flex", flexDirection: "column",
                  boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
                }}>
                  {/* Header */}
                  <div className="flex items-center justify-between" style={{ padding: "16px 20px", borderBottom: "1px solid var(--border-hairline)" }}>
                    <span style={{ fontSize: "15px", fontWeight: 600, color: "var(--color-ink)" }}>選取監控視窗</span>
                    <button
                      onClick={() => setShowWindowModal(false)}
                      style={{ background: "none", border: "none", color: "var(--color-ink-muted)", cursor: "pointer", fontSize: "18px" }}
                    >&times;</button>
                  </div>

                  {/* Manual input */}
                  <div className="flex gap-2" style={{ padding: "12px 20px", borderBottom: "1px solid var(--border-hairline)" }}>
                    <input
                      type="text" className="form-input" style={{ flex: 1 }}
                      placeholder="手動輸入視窗名稱（部分關鍵字即可）"
                      value={manualWindowName}
                      onChange={(e) => setManualWindowName(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") handleAddManualWindow(); }}
                    />
                    <button onClick={handleAddManualWindow} className="btn-outline" disabled={!manualWindowName.trim()}>
                      加入
                    </button>
                  </div>

                  {/* Detected windows list */}
                  <div style={{ flex: 1, overflowY: "auto", padding: "8px 0" }}>
                    {detecting ? (
                      <div style={{ padding: "30px 0", textAlign: "center", color: "var(--color-ink-muted)", fontSize: "13px" }}>
                        偵測中...
                      </div>
                    ) : detectedWindows.length === 0 ? (
                      <div style={{ padding: "30px 0", textAlign: "center", color: "var(--color-ink-ghost)", fontSize: "13px" }}>
                        未偵測到視窗，請手動輸入
                      </div>
                    ) : (
                      <>
                        <div style={{ padding: "4px 20px 8px", fontSize: "11px", color: "var(--color-ink-ghost)" }}>
                          偵測到 {detectedWindows.length} 個視窗 — 點擊加入
                        </div>
                        {detectedWindows.map((title, i) => {
                          const already = cfg.capture_windows.some((w) => w.window_name === title);
                          return (
                            <div
                              key={i}
                              onClick={() => { if (!already) { addWindow(title); } }}
                              className="flex items-center justify-between"
                              style={{
                                padding: "9px 20px", fontSize: "13px",
                                cursor: already ? "default" : "pointer",
                                opacity: already ? 0.4 : 1,
                                color: "var(--color-ink-light)",
                                transition: "background 0.1s",
                              }}
                              onMouseEnter={(e) => { if (!already) (e.currentTarget.style.background = "rgba(255,255,255,0.04)"); }}
                              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                            >
                              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, marginRight: "12px" }}>{title}</span>
                              {already
                                ? <span style={{ fontSize: "11px", color: "var(--color-ink-ghost)", flexShrink: 0 }}>已加入</span>
                                : <span style={{ fontSize: "11px", color: "var(--color-teal)", flexShrink: 0 }}>+ 加入</span>
                              }
                            </div>
                          );
                        })}
                      </>
                    )}
                  </div>

                  {/* Footer */}
                  <div style={{ padding: "12px 20px", borderTop: "1px solid var(--border-hairline)", textAlign: "right" }}>
                    <button onClick={() => setShowWindowModal(false)} className="btn-outline">完成</button>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="section stagger-3">
            <div className="heading-md" style={{ fontFamily: "var(--font-serif)", marginBottom: "20px" }}>
              OCR 設定
            </div>
            <div className="divider" style={{ marginBottom: "16px" }} />

            <FormRow label={S.CAPTURE_INTERVAL}>
              <input type="number" className="form-input" style={{ width: 120 }} step="0.5" min="0.5" max="10"
                value={cfg.capture_interval} onChange={(e) => update("capture_interval", parseFloat(e.target.value) || 2)} />
            </FormRow>
            <FormRow label={S.OCR_CONFIRM_COUNT}>
              <input type="number" className="form-input" style={{ width: 80 }} min="1" max="5"
                value={cfg.ocr_confirm_count} onChange={(e) => update("ocr_confirm_count", parseInt(e.target.value) || 2)} />
            </FormRow>
            <FormRow label={S.OCR_CONFIRM_DELAY}>
              <input type="number" className="form-input" style={{ width: 120 }} step="0.5" min="0.5" max="5"
                value={cfg.ocr_confirm_delay} onChange={(e) => update("ocr_confirm_delay", parseFloat(e.target.value) || 1)} />
            </FormRow>
          </div>
        </>
      )}

      {/* Safety tab */}
      {activeTab === 2 && (
        <div className="section stagger-1">
          <div className="heading-md" style={{ fontFamily: "var(--font-serif)", marginBottom: "20px" }}>
            {S.SETTINGS_SAFETY}
          </div>
          <div className="divider" style={{ marginBottom: "16px" }} />

          <FormRow label={S.MIN_CONFIDENCE}>
            <input type="number" className="form-input" style={{ width: 120 }} step="0.05" min="0.5" max="1"
              value={cfg.min_confidence} onChange={(e) => update("min_confidence", parseFloat(e.target.value) || 0.85)} />
          </FormRow>
          <FormRow label={S.MAX_PRICE_DEVIATION}>
            <input type="number" className="form-input" style={{ width: 120 }} step="0.005" min="0.001" max="0.1"
              value={cfg.max_price_deviation} onChange={(e) => update("max_price_deviation", parseFloat(e.target.value) || 0.01)} />
          </FormRow>
          <FormRow label={S.SIGNAL_DEDUP_MINUTES}>
            <input type="number" className="form-input" style={{ width: 80 }} min="1" max="60"
              value={cfg.signal_dedup_minutes} onChange={(e) => update("signal_dedup_minutes", parseInt(e.target.value) || 5)} />
          </FormRow>
          <FormRow label={S.MAX_DAILY_LOSS}>
            <input type="number" className="form-input" style={{ width: 120 }} step="50" min="10" max="100000"
              value={cfg.max_daily_loss} onChange={(e) => update("max_daily_loss", parseFloat(e.target.value) || 500)} />
          </FormRow>
          <FormRow label={S.MAX_OPEN_POSITIONS}>
            <input type="number" className="form-input" style={{ width: 80 }} min="1" max="50"
              value={cfg.max_open_positions} onChange={(e) => update("max_open_positions", parseInt(e.target.value) || 5)} />
          </FormRow>
        </div>
      )}

      {/* MT5 tab */}
      {activeTab === 3 && (
        <div className="section stagger-1">
          <div className="heading-md" style={{ fontFamily: "var(--font-serif)", marginBottom: "20px" }}>
            {S.SETTINGS_MT5}
          </div>
          <div className="divider" style={{ marginBottom: "16px" }} />

          <FormRow label={S.MT5_FILES_DIR}>
            <input type="text" className="form-input" style={{ flex: 1 }}
              placeholder="點擊自動偵測或手動輸入路徑"
              value={cfg.mt5_files_dir} onChange={(e) => update("mt5_files_dir", e.target.value)} />
          </FormRow>

          <div className="flex gap-2" style={{ marginTop: "16px" }}>
            <button onClick={handleDetectMt5Dir} className="btn-outline" disabled={detectingMt5}>
              {detectingMt5 ? "偵測中..." : "自動偵測路徑"}
            </button>
            <button onClick={handleTestConnection} className="btn-outline" disabled={testing}>
              {testing ? "測試中..." : S.BTN_TEST_CONNECTION}
            </button>
          </div>
        </div>
      )}

      {/* Bottom actions */}
      <div className="flex justify-end gap-3 stagger-4" style={{ paddingBottom: "8px" }}>
        <button onClick={handleReset} className="btn-danger-outline" style={{ padding: "9px 20px" }}>
          {S.BTN_RESET_DEFAULTS}
        </button>
        <button onClick={handleSave} className="btn-gold" style={{ padding: "9px 24px" }}>
          {S.BTN_SAVE}
        </button>
      </div>
    </div>
  );
}

/* Helpers */
function FormRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-4" style={{ padding: "10px 0" }}>
      <span style={{ minWidth: 150, fontSize: "13px", color: "var(--color-ink-light)" }}>{label}</span>
      {children}
    </div>
  );
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      style={{
        width: 40,
        height: 22,
        borderRadius: 11,
        border: "none",
        cursor: "pointer",
        background: checked ? "var(--color-profit)" : "var(--color-ink-ghost)",
        position: "relative",
        transition: "background 0.2s",
        padding: 0,
      }}
    >
      <span
        style={{
          position: "absolute",
          top: 2,
          left: checked ? 20 : 2,
          width: 18,
          height: 18,
          borderRadius: "50%",
          background: "white",
          boxShadow: "0 1px 3px rgba(0,0,0,0.15)",
          transition: "left 0.2s",
        }}
      />
    </button>
  );
}
