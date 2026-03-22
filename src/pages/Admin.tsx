import { useState, useEffect, useCallback } from "react";
import { S } from "../lib/constants";
import { useAuthStore } from "../stores/authStore";
import { sidecarCommands } from "../hooks/useSidecar";
import type { UserData, CreateUserData, SubscriptionPlan } from "../lib/auth";

const PLAN_OPTIONS: { value: SubscriptionPlan; label: string }[] = [
  { value: "trial", label: S.AUTH_PLAN_TRIAL },
  { value: "standard", label: S.AUTH_PLAN_STANDARD },
  { value: "premium", label: S.AUTH_PLAN_PREMIUM },
];

const STATUS_OPTIONS = [
  { value: "active", label: S.AUTH_STATUS_ACTIVE },
  { value: "expired", label: S.AUTH_STATUS_EXPIRED },
  { value: "revoked", label: S.AUTH_STATUS_REVOKED },
];

export default function Admin() {
  const currentUser = useAuthStore((s) => s.user);
  const [users, setUsers] = useState<UserData[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [editUser, setEditUser] = useState<UserData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchUsers = useCallback(async () => {
    if (!currentUser) return;
    setLoading(true);
    try {
      const result = await sidecarCommands.adminListUsers(currentUser.email);
      setUsers(result.users ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "載入失敗");
    } finally {
      setLoading(false);
    }
  }, [currentUser]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  if (!currentUser?.is_admin) {
    return (
      <div style={{ padding: "40px", textAlign: "center", color: "var(--color-ink-muted)" }}>
        {S.AUTH_NO_ADMIN_ACCESS}
      </div>
    );
  }

  function formatDate(iso: string) {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleDateString("zh-TW", {
        year: "numeric", month: "2-digit", day: "2-digit",
      });
    } catch {
      return iso;
    }
  }

  async function handleDelete(email: string) {
    if (!confirm(`${S.AUTH_CONFIRM_DELETE} ${email}?`)) return;
    try {
      await sidecarCommands.adminDeleteUser(currentUser!.email, email);
      fetchUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "刪除失敗");
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between stagger-1" style={{ marginBottom: "24px" }}>
        <h1 className="heading-lg" style={{ margin: 0 }}>{S.AUTH_ADMIN_TITLE}</h1>
        <button
          className="btn-gold"
          onClick={() => setShowCreate(true)}
          style={{ padding: "9px 20px", fontSize: "13px" }}
        >
          {S.AUTH_ADD_USER}
        </button>
      </div>

      {error && (
        <div
          style={{
            padding: "10px 14px", borderRadius: "6px",
            background: "var(--color-loss-bg)", color: "var(--color-loss)",
            fontSize: "12.5px", marginBottom: "16px",
          }}
        >
          {error}
          <button
            onClick={() => setError(null)}
            style={{ float: "right", background: "none", border: "none", cursor: "pointer", color: "inherit" }}
          >
            ×
          </button>
        </div>
      )}

      {/* Users table */}
      <div className="section stagger-2" style={{ padding: 0, overflow: "hidden" }}>
        <table className="data-table" style={{ width: "100%" }}>
          <thead>
            <tr>
              <th>{S.AUTH_EMAIL}</th>
              <th>{S.AUTH_DISPLAY_NAME}</th>
              <th>{S.AUTH_PLAN}</th>
              <th>{S.AUTH_STATUS}</th>
              <th>{S.AUTH_EXPIRES_AT}</th>
              <th style={{ textAlign: "right" }}>{S.AUTH_ACTIONS}</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={6} style={{ textAlign: "center", padding: "32px", color: "var(--color-ink-muted)" }}>
                  {S.AUTH_LOADING}
                </td>
              </tr>
            ) : users.length === 0 ? (
              <tr>
                <td colSpan={6} style={{ textAlign: "center", padding: "32px", color: "var(--color-ink-muted)" }}>
                  {S.AUTH_NO_USERS}
                </td>
              </tr>
            ) : (
              users.map((u) => (
                <tr key={u.email}>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: "12px" }}>{u.email}</td>
                  <td>{u.display_name}</td>
                  <td>
                    <span
                      style={{
                        display: "inline-block",
                        padding: "2px 10px",
                        borderRadius: "4px",
                        fontSize: "11px",
                        fontWeight: 600,
                        background: "var(--color-gold-dim)",
                        color: "var(--color-gold)",
                      }}
                    >
                      {PLAN_OPTIONS.find((p) => p.value === u.plan)?.label ?? u.plan}
                    </span>
                  </td>
                  <td>
                    <span
                      style={{
                        fontSize: "12px",
                        fontWeight: 500,
                        color: u.status === "active" ? "var(--color-profit)" : "var(--color-loss)",
                      }}
                    >
                      {STATUS_OPTIONS.find((s) => s.value === u.status)?.label ?? u.status}
                    </span>
                  </td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: "12px" }}>
                    {formatDate(u.expires_at)}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <button
                      className="btn-outline"
                      onClick={() => setEditUser(u)}
                      style={{ padding: "4px 12px", fontSize: "11px", marginRight: "6px" }}
                    >
                      {S.AUTH_EDIT}
                    </button>
                    <button
                      className="btn-danger-outline"
                      onClick={() => handleDelete(u.email)}
                      style={{ padding: "4px 12px", fontSize: "11px" }}
                    >
                      {S.AUTH_DELETE}
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Create user modal */}
      {showCreate && (
        <CreateUserModal
          adminEmail={currentUser.email}
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            fetchUsers();
          }}
        />
      )}

      {/* Edit user modal */}
      {editUser && (
        <EditUserModal
          adminEmail={currentUser.email}
          user={editUser}
          onClose={() => setEditUser(null)}
          onUpdated={() => {
            setEditUser(null);
            fetchUsers();
          }}
        />
      )}
    </div>
  );
}

// ── Create modal ────────────────────────────────────────────

function CreateUserModal({
  adminEmail,
  onClose,
  onCreated,
}: {
  adminEmail: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form, setForm] = useState<CreateUserData>({
    email: "",
    password: "",
    display_name: "",
    plan: "trial",
    expires_at: "",
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.email || !form.password) return;
    setLoading(true);
    setError(null);
    try {
      await sidecarCommands.adminCreateUser(adminEmail, form);
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "建立失敗");
    } finally {
      setLoading(false);
    }
  }

  return (
    <ModalOverlay onClose={onClose}>
      <div style={{ padding: "24px 28px" }}>
        <h3
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: "18px",
            fontWeight: 400,
            color: "var(--color-ink)",
            marginBottom: "24px",
          }}
        >
          {S.AUTH_ADD_USER}
        </h3>

        <form onSubmit={handleSubmit}>
          <ModalField label={S.AUTH_EMAIL}>
            <input
              type="text"
              className="form-input"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              required
              autoFocus
              style={{ width: "100%" }}
            />
          </ModalField>
          <ModalField label={S.AUTH_PASSWORD}>
            <input
              type="password"
              className="form-input"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              required
              style={{ width: "100%" }}
            />
          </ModalField>
          <ModalField label={S.AUTH_DISPLAY_NAME}>
            <input
              type="text"
              className="form-input"
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              style={{ width: "100%" }}
            />
          </ModalField>
          <ModalField label={S.AUTH_PLAN}>
            <select
              className="form-input"
              value={form.plan}
              onChange={(e) => setForm({ ...form, plan: e.target.value as SubscriptionPlan })}
              style={{ width: "100%" }}
            >
              {PLAN_OPTIONS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
          </ModalField>
          <ModalField label={S.AUTH_EXPIRES_AT}>
            <input
              type="date"
              className="form-input"
              value={form.expires_at ? form.expires_at.slice(0, 10) : ""}
              onChange={(e) => setForm({ ...form, expires_at: e.target.value ? e.target.value + "T23:59:59Z" : "" })}
              style={{ width: "100%" }}
            />
          </ModalField>

          {error && (
            <div
              style={{
                padding: "8px 14px", borderRadius: "6px",
                background: "var(--color-loss-bg)", color: "var(--color-loss)",
                fontSize: "12.5px", marginTop: "8px",
              }}
            >
              {error}
            </div>
          )}

          <div className="flex gap-2 justify-end" style={{ marginTop: "24px" }}>
            <button type="button" className="btn-outline" onClick={onClose} style={{ padding: "9px 20px" }}>
              {S.AUTH_CANCEL}
            </button>
            <button
              type="submit"
              className="btn-gold"
              disabled={loading || !form.email || !form.password}
              style={{ padding: "9px 20px" }}
            >
              {loading ? S.AUTH_SAVING : S.AUTH_CREATE}
            </button>
          </div>
        </form>
      </div>
    </ModalOverlay>
  );
}

// ── Edit modal ──────────────────────────────────────────────

function EditUserModal({
  adminEmail,
  user,
  onClose,
  onUpdated,
}: {
  adminEmail: string;
  user: UserData;
  onClose: () => void;
  onUpdated: () => void;
}) {
  const [plan, setPlan] = useState(user.plan);
  const [status, setStatus] = useState(user.status);
  const [expiresAt, setExpiresAt] = useState(user.expires_at?.slice(0, 10) ?? "");
  const [notes, setNotes] = useState(user.notes ?? "");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await sidecarCommands.adminUpdateUser(adminEmail, user.email, {
        plan,
        status,
        expires_at: expiresAt ? expiresAt + "T23:59:59Z" : "",
        notes,
      } as Partial<UserData>);
      onUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新失敗");
    } finally {
      setLoading(false);
    }
  }

  return (
    <ModalOverlay onClose={onClose}>
      <div style={{ padding: "24px 28px" }}>
        <h3
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: "18px",
            fontWeight: 400,
            color: "var(--color-ink)",
            marginBottom: "8px",
          }}
        >
          {S.AUTH_EDIT_USER}
        </h3>
        <p style={{ fontSize: "12px", color: "var(--color-ink-muted)", fontFamily: "var(--font-mono)", marginBottom: "24px" }}>
          {user.email}
        </p>

        <form onSubmit={handleSubmit}>
          <ModalField label={S.AUTH_PLAN}>
            <select
              className="form-input"
              value={plan}
              onChange={(e) => setPlan(e.target.value as SubscriptionPlan)}
              style={{ width: "100%" }}
            >
              {PLAN_OPTIONS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
          </ModalField>
          <ModalField label={S.AUTH_STATUS}>
            <select
              className="form-input"
              value={status}
              onChange={(e) => setStatus(e.target.value as "active" | "expired" | "revoked")}
              style={{ width: "100%" }}
            >
              {STATUS_OPTIONS.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </ModalField>
          <ModalField label={S.AUTH_EXPIRES_AT}>
            <input
              type="date"
              className="form-input"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
              style={{ width: "100%" }}
            />
          </ModalField>
          <ModalField label={S.AUTH_NOTES}>
            <input
              type="text"
              className="form-input"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              style={{ width: "100%" }}
            />
          </ModalField>

          {error && (
            <div
              style={{
                padding: "8px 14px", borderRadius: "6px",
                background: "var(--color-loss-bg)", color: "var(--color-loss)",
                fontSize: "12.5px", marginTop: "8px",
              }}
            >
              {error}
            </div>
          )}

          <div className="flex gap-2 justify-end" style={{ marginTop: "24px" }}>
            <button type="button" className="btn-outline" onClick={onClose} style={{ padding: "9px 20px" }}>
              {S.AUTH_CANCEL}
            </button>
            <button
              type="submit"
              className="btn-gold"
              disabled={loading}
              style={{ padding: "9px 20px" }}
            >
              {loading ? S.AUTH_SAVING : S.BTN_SAVE}
            </button>
          </div>
        </form>
      </div>
    </ModalOverlay>
  );
}

// ── Shared components ───────────────────────────────────────

function ModalOverlay({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        background: "rgba(0,0,0,0.4)", backdropFilter: "blur(4px)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        style={{
          background: "var(--color-bg-surface)",
          border: "1px solid var(--border-hairline)",
          borderRadius: "10px",
          width: 480,
          maxHeight: "80vh",
          overflow: "auto",
          boxShadow: "0 20px 60px rgba(0,0,0,0.15)",
        }}
      >
        {children}
      </div>
    </div>
  );
}

function ModalField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: "14px" }}>
      <label
        style={{
          display: "block",
          fontSize: "12px",
          color: "var(--color-ink-muted)",
          marginBottom: "6px",
          fontWeight: 500,
        }}
      >
        {label}
      </label>
      {children}
    </div>
  );
}
