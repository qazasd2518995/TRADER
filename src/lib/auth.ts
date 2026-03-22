/* Auth & subscription types */

export type AuthStatus = "loading" | "unauthenticated" | "authenticated" | "expired";

export type SubscriptionPlan = "trial" | "standard" | "premium";
export type SubscriptionStatus = "active" | "expired" | "revoked";

export interface UserData {
  email: string;
  display_name: string;
  plan: SubscriptionPlan;
  status: SubscriptionStatus;
  starts_at: string;
  expires_at: string;
  is_admin: boolean;
  created_at: string;
  notes?: string;
  subscription_valid?: boolean;
}

export interface VerifyResult {
  valid: boolean;
  plan: string;
  status: string;
  expires_at: string;
  error?: string;
}

export interface LoginResult {
  user?: UserData;
  error?: string;
}

export interface CreateUserData {
  email: string;
  password: string;
  display_name?: string;
  plan?: SubscriptionPlan;
  expires_at?: string;
  is_admin?: boolean;
  notes?: string;
}

/** localStorage key for cached session */
export const AUTH_CACHE_KEY = "gold_trader_auth";

/** Plan limits — max capture windows per plan */
export const PLAN_MAX_WINDOWS: Record<SubscriptionPlan, number> = {
  trial: 1,
  standard: 1,
  premium: Infinity,
};

/** Get max windows for a given plan string */
export function getMaxWindows(plan: string): number {
  return PLAN_MAX_WINDOWS[plan as SubscriptionPlan] ?? 1;
}
