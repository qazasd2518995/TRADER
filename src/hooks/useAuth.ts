import { useEffect, useRef } from "react";
import { useAuthStore } from "../stores/authStore";
import { sidecarCommands } from "./useSidecar";
import { AUTH_CACHE_KEY } from "../lib/auth";
import type { UserData } from "../lib/auth";

const VERIFY_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes

export function useAuth() {
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const status = useAuthStore((s) => s.status);
  const user = useAuthStore((s) => s.user);

  useEffect(() => {
    initAuth();
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  async function initAuth() {
    const { setStatus, setUser } = useAuthStore.getState();

    // Try cached session
    const cached = loadCache();
    if (cached) {
      setUser(cached);
      // Verify with server
      try {
        const result = await sidecarCommands.verifySubscription(cached.email);
        if (result.valid) {
          const updated = { ...cached, plan: result.plan, status: result.status, expires_at: result.expires_at } as UserData;
          setUser(updated);
          saveCache(updated);
          setStatus("authenticated");
        } else {
          setStatus("expired");
        }
      } catch {
        // Network failure — use cache if still valid
        if (isCacheValid(cached)) {
          setStatus("authenticated");
        } else {
          setStatus("expired");
        }
      }
    } else {
      setStatus("unauthenticated");
    }

    // Periodic re-verification
    intervalRef.current = setInterval(() => periodicVerify(), VERIFY_INTERVAL_MS);
  }

  async function periodicVerify() {
    const { user, setStatus, setUser } = useAuthStore.getState();
    if (!user) return;

    try {
      const result = await sidecarCommands.verifySubscription(user.email);
      if (result.valid) {
        const updated = { ...user, plan: result.plan, status: result.status, expires_at: result.expires_at } as UserData;
        setUser(updated);
        saveCache(updated);
        setStatus("authenticated");
      } else {
        setStatus("expired");
      }
    } catch {
      // Silently ignore network failures during periodic check
    }
  }

  return { status, user };
}

// ── Cache helpers ──────────────────────────────────────────

function loadCache(): UserData | null {
  try {
    const raw = localStorage.getItem(AUTH_CACHE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as UserData;
  } catch {
    return null;
  }
}

export function saveCache(user: UserData) {
  try {
    localStorage.setItem(AUTH_CACHE_KEY, JSON.stringify(user));
  } catch {
    // localStorage full or disabled
  }
}

export function clearCache() {
  try {
    localStorage.removeItem(AUTH_CACHE_KEY);
  } catch {
    // ignore
  }
}

function isCacheValid(user: UserData): boolean {
  if (user.status !== "active") return false;
  if (!user.expires_at) return false;
  try {
    return new Date(user.expires_at).getTime() > Date.now();
  } catch {
    return false;
  }
}
