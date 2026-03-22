import { useState } from "react";
import { S } from "../lib/constants";
import { useAuthStore } from "../stores/authStore";
import { sidecarCommands } from "../hooks/useSidecar";
import { clearCache } from "../hooks/useAuth";

const PLAN_LABELS: Record<string, string> = {
  trial: S.AUTH_PLAN_TRIAL,
  standard: S.AUTH_PLAN_STANDARD,
  premium: S.AUTH_PLAN_PREMIUM,
};

const STATUS_LABELS: Record<string, string> = {
  active: S.AUTH_STATUS_ACTIVE,
  expired: S.AUTH_STATUS_EXPIRED,
  revoked: S.AUTH_STATUS_REVOKED,
};

function FormRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-4" style={{ padding: "10px 0" }}>
      <span style={{ minWidth: 150, fontSize: "13px", color: "var(--color-ink-light)" }}>
        {label}
      </span>
      {children}
    </div>
  );
}

export default function Profile() {
  const user = useAuthStore((s) => s.user);
  const daysRemaining = useAuthStore((s) => s.daysRemaining);
  const logout = useAuthStore((s) => s.logout);

  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [pwLoading, setPwLoading] = useState(false);
  const [pwMsg, setPwMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  if (!user) return null;

  const days = daysRemaining();

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault();
    if (!oldPw || !newPw) return;
    if (newPw !== confirmPw) {
      setPwMsg({ type: "err", text: S.AUTH_PW_MISMATCH });
      return;
    }
    if (newPw.length < 6) {
      setPwMsg({ type: "err", text: S.AUTH_PW_TOO_SHORT });
      return;
    }

    setPwLoading(true);
    setPwMsg(null);
    try {
      await sidecarCommands.changePassword(user!.email, oldPw, newPw);
      setPwMsg({ type: "ok", text: S.AUTH_PW_CHANGED });
      setOldPw("");
      setNewPw("");
      setConfirmPw("");
    } catch (err) {
      setPwMsg({ type: "err", text: err instanceof Error ? err.message : S.AUTH_PW_CHANGE_FAILED });
    } finally {
      setPwLoading(false);
    }
  }

  function handleLogout() {
    clearCache();
    logout();
  }

  function formatDate(iso: string) {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleDateString("zh-TW", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      });
    } catch {
      return iso;
    }
  }

  return (
    <div style={{ maxWidth: 640 }}>
      <h1 className="heading-lg stagger-1">{S.AUTH_PROFILE_TITLE}</h1>

      {/* 會員資料 */}
      <div className="section stagger-2" style={{ padding: "24px 28px", marginBottom: "20px" }}>
        <h3
          className="heading-sm"
          style={{ marginBottom: "16px", fontSize: "11px" }}
        >
          {S.AUTH_MEMBER_INFO}
        </h3>
        <FormRow label={S.AUTH_EMAIL}>
          <span style={{ fontSize: "13.5px", fontFamily: "var(--font-mono)", color: "var(--color-ink)" }}>
            {user.email}
          </span>
        </FormRow>
        <FormRow label={S.AUTH_DISPLAY_NAME}>
          <span style={{ fontSize: "13.5px", color: "var(--color-ink)" }}>
            {user.display_name}
          </span>
        </FormRow>
      </div>

      {/* 訂閱狀態 */}
      <div className="section stagger-3" style={{ padding: "24px 28px", marginBottom: "20px" }}>
        <h3
          className="heading-sm"
          style={{ marginBottom: "16px", fontSize: "11px" }}
        >
          {S.AUTH_SUBSCRIPTION_INFO}
        </h3>
        <FormRow label={S.AUTH_PLAN}>
          <span
            style={{
              display: "inline-block",
              padding: "3px 12px",
              borderRadius: "4px",
              fontSize: "12px",
              fontWeight: 600,
              letterSpacing: "0.03em",
              background: "var(--color-gold-dim)",
              color: "var(--color-gold)",
            }}
          >
            {PLAN_LABELS[user.plan] ?? user.plan}
          </span>
        </FormRow>
        <FormRow label={S.AUTH_STATUS}>
          <span
            style={{
              fontSize: "13px",
              color: user.status === "active" ? "var(--color-profit)" : "var(--color-loss)",
              fontWeight: 500,
            }}
          >
            {STATUS_LABELS[user.status] ?? user.status}
          </span>
        </FormRow>
        <FormRow label={S.AUTH_STARTS_AT}>
          <span style={{ fontSize: "13px", fontFamily: "var(--font-mono)", color: "var(--color-ink)" }}>
            {formatDate(user.starts_at)}
          </span>
        </FormRow>
        <FormRow label={S.AUTH_EXPIRES_AT}>
          <span style={{ fontSize: "13px", fontFamily: "var(--font-mono)", color: "var(--color-ink)" }}>
            {formatDate(user.expires_at)}
          </span>
        </FormRow>
        <FormRow label={S.AUTH_DAYS_REMAINING}>
          <span
            className="num-lg"
            style={{
              fontSize: "24px",
              color: days <= 7 ? "var(--color-loss)" : "var(--color-ink)",
            }}
          >
            {days}
          </span>
          <span style={{ fontSize: "12px", color: "var(--color-ink-muted)", marginLeft: "4px" }}>
            {S.AUTH_DAYS}
          </span>
        </FormRow>
      </div>

      {/* 修改密碼 */}
      <div className="section stagger-4" style={{ padding: "24px 28px", marginBottom: "20px" }}>
        <h3
          className="heading-sm"
          style={{ marginBottom: "16px", fontSize: "11px" }}
        >
          {S.AUTH_CHANGE_PASSWORD}
        </h3>
        <form onSubmit={handleChangePassword}>
          <FormRow label={S.AUTH_OLD_PASSWORD}>
            <input
              type="password"
              className="form-input"
              value={oldPw}
              onChange={(e) => setOldPw(e.target.value)}
              disabled={pwLoading}
              style={{ width: 240 }}
            />
          </FormRow>
          <FormRow label={S.AUTH_NEW_PASSWORD}>
            <input
              type="password"
              className="form-input"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              disabled={pwLoading}
              style={{ width: 240 }}
            />
          </FormRow>
          <FormRow label={S.AUTH_CONFIRM_PASSWORD}>
            <input
              type="password"
              className="form-input"
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
              disabled={pwLoading}
              style={{ width: 240 }}
            />
          </FormRow>

          {pwMsg && (
            <div
              style={{
                padding: "8px 14px",
                borderRadius: "6px",
                background: pwMsg.type === "ok" ? "var(--color-profit-bg)" : "var(--color-loss-bg)",
                color: pwMsg.type === "ok" ? "var(--color-profit)" : "var(--color-loss)",
                fontSize: "12.5px",
                marginTop: "8px",
              }}
            >
              {pwMsg.text}
            </div>
          )}

          <div style={{ marginTop: "16px" }}>
            <button
              type="submit"
              className="btn-primary"
              disabled={pwLoading || !oldPw || !newPw || !confirmPw}
              style={{ padding: "9px 24px" }}
            >
              {pwLoading ? S.AUTH_SAVING : S.AUTH_CHANGE_PASSWORD_BTN}
            </button>
          </div>
        </form>
      </div>

      {/* 登出 */}
      <div className="stagger-5" style={{ paddingTop: "8px" }}>
        <button
          className="btn-danger-outline"
          onClick={handleLogout}
          style={{ padding: "10px 32px" }}
        >
          {S.AUTH_LOGOUT}
        </button>
      </div>
    </div>
  );
}
