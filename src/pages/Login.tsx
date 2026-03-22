import { useState } from "react";
import { S } from "../lib/constants";
import { useAuthStore } from "../stores/authStore";
import { sidecarCommands } from "../hooks/useSidecar";
import { saveCache } from "../hooks/useAuth";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || !password) return;

    setLoading(true);
    setError(null);

    try {
      const result = await sidecarCommands.login(email.trim(), password);
      if (result.user) {
        useAuthStore.getState().setUser(result.user);
        saveCache(result.user);

        if (result.user.subscription_valid === false) {
          useAuthStore.getState().setStatus("expired");
        } else {
          useAuthStore.getState().setStatus("authenticated");
        }
      } else {
        setError(result.error ?? S.AUTH_LOGIN_FAILED);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : S.AUTH_LOGIN_FAILED);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      className="flex items-center justify-center"
      style={{ height: "100vh", background: "var(--color-bg-base)" }}
    >
      <div style={{ width: 400 }}>
        {/* Brand */}
        <div className="text-center" style={{ marginBottom: "40px" }}>
          <h1
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: "32px",
              fontWeight: 300,
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
              fontSize: "11px",
              color: "var(--color-ink-ghost)",
              marginTop: "8px",
              letterSpacing: "0.08em",
            }}
          >
            v{S.APP_VERSION}
          </div>
        </div>

        {/* Login card */}
        <div className="section" style={{ padding: "36px 32px" }}>
          <h2
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: "20px",
              fontWeight: 400,
              color: "var(--color-ink)",
              marginBottom: "28px",
              textAlign: "center",
            }}
          >
            {S.AUTH_LOGIN_TITLE}
          </h2>

          <form onSubmit={handleSubmit}>
            <div style={{ marginBottom: "18px" }}>
              <label
                style={{
                  display: "block",
                  fontSize: "12px",
                  color: "var(--color-ink-muted)",
                  marginBottom: "6px",
                  fontWeight: 500,
                }}
              >
                {S.AUTH_EMAIL}
              </label>
              <input
                type="text"
                className="form-input"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="帳號或 email"
                autoFocus
                disabled={loading}
                style={{ width: "100%" }}
              />
            </div>

            <div style={{ marginBottom: "24px" }}>
              <label
                style={{
                  display: "block",
                  fontSize: "12px",
                  color: "var(--color-ink-muted)",
                  marginBottom: "6px",
                  fontWeight: 500,
                }}
              >
                {S.AUTH_PASSWORD}
              </label>
              <input
                type="password"
                className="form-input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={loading}
                style={{ width: "100%" }}
              />
            </div>

            {error && (
              <div
                style={{
                  padding: "10px 14px",
                  borderRadius: "6px",
                  background: "var(--color-loss-bg)",
                  color: "var(--color-loss)",
                  fontSize: "12.5px",
                  marginBottom: "18px",
                }}
              >
                {error}
              </div>
            )}

            <button
              type="submit"
              className="btn-gold"
              disabled={loading || !email.trim() || !password}
              style={{
                width: "100%",
                padding: "12px 0",
                fontSize: "14px",
                opacity: loading ? 0.6 : 1,
              }}
            >
              {loading ? S.AUTH_LOGGING_IN : S.AUTH_LOGIN_BTN}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
