import { useAuthStore } from "../stores/authStore";
import { S } from "../lib/constants";
import Login from "../pages/Login";

interface AuthGuardProps {
  children: React.ReactNode;
}

export default function AuthGuard({ children }: AuthGuardProps) {
  const status = useAuthStore((s) => s.status);
  const user = useAuthStore((s) => s.user);

  if (status === "loading") {
    return <LoadingScreen />;
  }

  if (status === "unauthenticated") {
    return <Login />;
  }

  if (status === "expired") {
    return <ExpiredScreen email={user?.email ?? ""} plan={user?.plan ?? ""} />;
  }

  return <>{children}</>;
}

function LoadingScreen() {
  return (
    <div
      className="flex items-center justify-center"
      style={{ height: "100vh", background: "var(--color-bg-base)" }}
    >
      <div className="text-center">
        <h1
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: "28px",
            fontWeight: 300,
            color: "var(--color-ink)",
            marginBottom: "16px",
          }}
        >
          {S.APP_TITLE}
        </h1>
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "12px",
            color: "var(--color-ink-muted)",
          }}
        >
          {S.AUTH_LOADING}
        </div>
      </div>
    </div>
  );
}

function ExpiredScreen({ email, plan }: { email: string; plan: string }) {
  return (
    <div
      className="flex items-center justify-center"
      style={{ height: "100vh", background: "var(--color-bg-base)" }}
    >
      <div
        className="section text-center"
        style={{ maxWidth: 440, padding: "48px 40px" }}
      >
        <div
          style={{
            width: 56,
            height: 56,
            borderRadius: "50%",
            background: "var(--color-loss-bg)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            margin: "0 auto 20px",
            fontSize: "24px",
            color: "var(--color-loss)",
          }}
        >
          !
        </div>
        <h2
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: "22px",
            fontWeight: 400,
            color: "var(--color-ink)",
            marginBottom: "12px",
          }}
        >
          {S.AUTH_SUBSCRIPTION_EXPIRED}
        </h2>
        <p style={{ fontSize: "13px", color: "var(--color-ink-muted)", marginBottom: "8px" }}>
          {email}
        </p>
        <p style={{ fontSize: "13px", color: "var(--color-ink-muted)", marginBottom: "24px" }}>
          {S.AUTH_PLAN}: {plan}
        </p>
        <p style={{ fontSize: "13px", color: "var(--color-ink-light)", lineHeight: 1.6 }}>
          {S.AUTH_CONTACT_ADMIN}
        </p>
        <button
          className="btn-outline"
          onClick={() => {
            useAuthStore.getState().logout();
            localStorage.removeItem("gold_trader_auth");
          }}
          style={{ marginTop: "24px", padding: "10px 32px" }}
        >
          {S.AUTH_LOGOUT}
        </button>
      </div>
    </div>
  );
}
