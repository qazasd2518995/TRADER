import { create } from "zustand";
import type { AuthStatus, UserData } from "../lib/auth";

interface AuthState {
  // State
  status: AuthStatus;
  user: UserData | null;
  error: string | null;

  // Actions
  setStatus: (s: AuthStatus) => void;
  setUser: (u: UserData | null) => void;
  setError: (e: string | null) => void;
  logout: () => void;

  // Computed helpers (call as functions)
  isSubscriptionValid: () => boolean;
  daysRemaining: () => number;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  status: "loading",
  user: null,
  error: null,

  setStatus: (status) => set({ status }),
  setUser: (user) => set({ user, error: null }),
  setError: (error) => set({ error }),

  logout: () =>
    set({
      status: "unauthenticated",
      user: null,
      error: null,
    }),

  isSubscriptionValid: () => {
    const { user } = get();
    if (!user) return false;
    if (user.status !== "active") return false;
    if (!user.expires_at) return false;
    try {
      const exp = new Date(user.expires_at);
      return exp.getTime() > Date.now();
    } catch {
      return false;
    }
  },

  daysRemaining: () => {
    const { user } = get();
    if (!user?.expires_at) return 0;
    try {
      const exp = new Date(user.expires_at);
      const diff = exp.getTime() - Date.now();
      return Math.max(0, Math.ceil(diff / (1000 * 60 * 60 * 24)));
    } catch {
      return 0;
    }
  },
}));
