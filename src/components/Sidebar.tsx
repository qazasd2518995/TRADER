import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Table2,
  Clock,
  Settings,
  ScrollText,
  BookOpen,
  Play,
  Square,
  User,
  Shield,
} from "lucide-react";
import { S } from "../lib/constants";
import { useTradingStore } from "../stores/tradingStore";
import { useAuthStore } from "../stores/authStore";
import { sidecarCommands } from "../hooks/useSidecar";

const tradingNav = [
  { to: "/", icon: LayoutDashboard, label: S.NAV_DASHBOARD },
  { to: "/positions", icon: Table2, label: S.NAV_POSITIONS },
  { to: "/history", icon: Clock, label: S.NAV_HISTORY },
];

const systemNav = [
  { to: "/settings", icon: Settings, label: S.NAV_SETTINGS },
  { to: "/logs", icon: ScrollText, label: S.NAV_LOG },
  { to: "/tutorial", icon: BookOpen, label: S.NAV_TUTORIAL },
];

export default function Sidebar() {
  const connected = useTradingStore((s) => s.connected);
  const isTrading = useTradingStore((s) => s.isTrading);
  const status = useTradingStore((s) => s.status);
  const setIsTrading = useTradingStore((s) => s.setIsTrading);
  const setStartTime = useTradingStore((s) => s.setStartTime);
  const setStatus = useTradingStore((s) => s.setStatus);
  const config = useTradingStore((s) => s.config);
  const authUser = useAuthStore((s) => s.user);
  const isStarting = status === "starting";
  const canStop = isStarting || isTrading;

  async function handleStart() {
    if (isTrading || isStarting || !config) return;
    setStatus("starting");
    try {
      await sidecarCommands.startTrading(config);
    } catch {
      setStatus("stopped");
      setIsTrading(false);
      setStartTime(null);
    }
  }

  async function handleStop() {
    if (!canStop) return;
    try {
      await sidecarCommands.stopTrading();
    } finally {
      setStatus("stopped");
      setIsTrading(false);
      setStartTime(null);
    }
  }

  return (
    <aside
      className="flex flex-col shrink-0"
      style={{
        width: "var(--sidebar-width)",
        minWidth: "var(--sidebar-width)",
        background: "var(--color-bg-sidebar)",
        borderRight: "1px solid var(--border-hairline)",
      }}
    >
      {/* Brand */}
      <div style={{ padding: "28px 24px 20px" }}>
        <h1
          className="leading-none"
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: "22px",
            fontWeight: 400,
            color: "var(--color-ink)",
            letterSpacing: "-0.02em",
            margin: 0,
          }}
        >
          {S.APP_TITLE}
        </h1>
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "10px",
            color: "var(--color-ink-ghost)",
            marginTop: "6px",
            letterSpacing: "0.08em",
          }}
        >
          v{S.APP_VERSION}
        </div>
      </div>

      <div style={{ height: "1px", background: "var(--border-hairline)", margin: "0 24px" }} />

      {/* Trading nav */}
      <div style={{ padding: "20px 0 8px" }}>
        <div
          className="heading-sm"
          style={{ padding: "0 24px", marginBottom: "8px", fontSize: "10px" }}
        >
          {S.NAV_GROUP_TRADING}
        </div>
        {tradingNav.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className="no-underline flex items-center gap-3 transition-all duration-150"
            style={({ isActive }) => ({
              padding: "10px 24px",
              color: isActive ? "var(--color-ink)" : "var(--color-ink-muted)",
              background: isActive ? "var(--color-bg-hover)" : "transparent",
              fontWeight: isActive ? 500 : 400,
              fontSize: "13.5px",
              borderLeft: isActive
                ? "2px solid var(--color-gold)"
                : "2px solid transparent",
            })}
          >
            <item.icon size={16} strokeWidth={1.5} />
            {item.label}
          </NavLink>
        ))}
      </div>

      {/* System nav */}
      <div style={{ padding: "8px 0" }}>
        <div
          className="heading-sm"
          style={{ padding: "0 24px", marginBottom: "8px", fontSize: "10px" }}
        >
          {S.NAV_GROUP_SYSTEM}
        </div>
        {systemNav.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className="no-underline flex items-center gap-3 transition-all duration-150"
            style={({ isActive }) => ({
              padding: "10px 24px",
              color: isActive ? "var(--color-ink)" : "var(--color-ink-muted)",
              background: isActive ? "var(--color-bg-hover)" : "transparent",
              fontWeight: isActive ? 500 : 400,
              fontSize: "13px",
              borderLeft: isActive
                ? "2px solid var(--color-gold)"
                : "2px solid transparent",
            })}
          >
            <item.icon size={16} strokeWidth={1.5} />
            {item.label}
          </NavLink>
        ))}
      </div>

      {/* Account nav */}
      <div style={{ padding: "8px 0" }}>
        <div
          className="heading-sm"
          style={{ padding: "0 24px", marginBottom: "8px", fontSize: "10px" }}
        >
          {S.NAV_GROUP_ACCOUNT}
        </div>
        <NavLink
          to="/profile"
          className="no-underline flex items-center gap-3 transition-all duration-150"
          style={({ isActive }) => ({
            padding: "10px 24px",
            color: isActive ? "var(--color-ink)" : "var(--color-ink-muted)",
            background: isActive ? "var(--color-bg-hover)" : "transparent",
            fontWeight: isActive ? 500 : 400,
            fontSize: "13px",
            borderLeft: isActive
              ? "2px solid var(--color-gold)"
              : "2px solid transparent",
          })}
        >
          <User size={16} strokeWidth={1.5} />
          {S.NAV_PROFILE}
        </NavLink>
        {authUser?.is_admin && (
          <NavLink
            to="/admin"
            className="no-underline flex items-center gap-3 transition-all duration-150"
            style={({ isActive }) => ({
              padding: "10px 24px",
              color: isActive ? "var(--color-ink)" : "var(--color-ink-muted)",
              background: isActive ? "var(--color-bg-hover)" : "transparent",
              fontWeight: isActive ? 500 : 400,
              fontSize: "13px",
              borderLeft: isActive
                ? "2px solid var(--color-gold)"
                : "2px solid transparent",
            })}
          >
            <Shield size={16} strokeWidth={1.5} />
            {S.NAV_ADMIN}
          </NavLink>
        )}
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* User display */}
      {authUser && (
        <div
          style={{
            padding: "10px 24px",
            borderTop: "1px solid var(--border-hairline)",
            fontSize: "12px",
            color: "var(--color-ink-muted)",
          }}
        >
          <div style={{ fontSize: "12px", color: "var(--color-ink-light)", fontWeight: 500 }}>
            {authUser.display_name}
          </div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: "10px", color: "var(--color-ink-faint)", marginTop: "2px" }}>
            {authUser.email}
          </div>
        </div>
      )}

      {/* MT5 Connection */}
      <div
        className="flex items-center gap-2"
        style={{
          padding: "12px 24px",
          borderTop: "1px solid var(--border-hairline)",
          fontSize: "12px",
          color: "var(--color-ink-muted)",
        }}
      >
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: connected ? "var(--color-profit)" : "var(--color-loss)",
            animation: "pulse-soft 2.5s ease infinite",
          }}
        />
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px" }}>
          MT5 {connected ? S.STATUS_CONNECTED : S.STATUS_DISCONNECTED}
        </span>
      </div>

      {/* Action buttons */}
      <div style={{ padding: "12px 16px 16px", display: "flex", flexDirection: "column", gap: "6px" }}>
        <button
          onClick={handleStart}
          disabled={isTrading || isStarting}
          className="btn-primary flex items-center justify-center gap-2"
          style={{
            width: "100%",
            padding: "11px 0",
            background: isTrading || isStarting ? "var(--color-ink)" : "var(--color-profit)",
          }}
        >
          <Play size={13} strokeWidth={2.5} />
          {isStarting ? S.BTN_STARTING : S.BTN_START}
        </button>
        <button
          onClick={handleStop}
          disabled={!canStop}
          className="btn-outline flex items-center justify-center gap-2"
          style={{
            width: "100%",
            padding: "10px 0",
            color: !canStop ? "var(--color-ink-ghost)" : "var(--color-loss)",
            borderColor: !canStop ? "var(--border-hairline)" : "var(--color-loss-bg)",
          }}
        >
          <Square size={11} strokeWidth={2.5} />
          {S.BTN_STOP}
        </button>
      </div>
    </aside>
  );
}
