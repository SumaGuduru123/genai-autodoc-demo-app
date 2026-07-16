/**
 * frontend-client/authHooks.ts
 *
 * React hooks for authentication state management.
 * Wraps the apiClient token-pair flow and exposes clean hook interfaces.
 */

import { useCallback, useEffect, useReducer } from "react";
import {
  post,
  setAccessToken,
  clearAccessToken,
  ApiError,
  NetworkTimeoutError,
  NetworkUnavailableError,
} from "./apiClient";

// ---------------------------------------------------------------------------
// API shapes
// ---------------------------------------------------------------------------

interface LoginRequest {
  username: string;
  password: string;
}

interface TokenPair {
  access_token: string;
  refresh_token: string;
  expires_at: number; // Unix timestamp (seconds)
}

interface AuthUser {
  user_id: string;
  username: string;
  roles: string[];
}

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

type AuthStatus = "idle" | "loading" | "authenticated" | "error";

interface AuthState {
  status: AuthStatus;
  user: AuthUser | null;
  errorCode: string | null;
  errorMessage: string | null;
}

type AuthAction =
  | { type: "LOGIN_START" }
  | { type: "LOGIN_SUCCESS"; payload: AuthUser }
  | { type: "LOGIN_FAILURE"; errorCode: string; errorMessage: string }
  | { type: "LOGOUT" }
  | { type: "REFRESH_SUCCESS"; payload: AuthUser }
  | { type: "REFRESH_FAILURE" };

const INITIAL_STATE: AuthState = {
  status: "idle",
  user: null,
  errorCode: null,
  errorMessage: null,
};

function authReducer(state: AuthState, action: AuthAction): AuthState {
  switch (action.type) {
    case "LOGIN_START":
      return { ...INITIAL_STATE, status: "loading" };
    case "LOGIN_SUCCESS":
      return { status: "authenticated", user: action.payload, errorCode: null, errorMessage: null };
    case "LOGIN_FAILURE":
      return { status: "error", user: null, errorCode: action.errorCode, errorMessage: action.errorMessage };
    case "LOGOUT":
      return INITIAL_STATE;
    case "REFRESH_SUCCESS":
      return { status: "authenticated", user: action.payload, errorCode: null, errorMessage: null };
    case "REFRESH_FAILURE":
      return INITIAL_STATE;
    default:
      return state;
  }
}

// ---------------------------------------------------------------------------
// Refresh token storage (HttpOnly cookie is preferred in production)
// ---------------------------------------------------------------------------

const REFRESH_TOKEN_KEY = "auth_refresh_token";

function storeRefreshToken(token: string): void {
  // In production, prefer an HttpOnly cookie set by the server.
  // For the demo, sessionStorage (cleared on tab close) is acceptable.
  sessionStorage.setItem(REFRESH_TOKEN_KEY, token);
}

function loadRefreshToken(): string | null {
  return sessionStorage.getItem(REFRESH_TOKEN_KEY);
}

function clearRefreshToken(): void {
  sessionStorage.removeItem(REFRESH_TOKEN_KEY);
}

// ---------------------------------------------------------------------------
// useAuth hook
// ---------------------------------------------------------------------------

export interface UseAuthReturn {
  /** Current auth status. */
  status: AuthStatus;
  /** Authenticated user or null. */
  user: AuthUser | null;
  /** Machine-readable error code (e.g. "INVALID_CREDENTIALS"). */
  errorCode: string | null;
  /** Human-readable error message. */
  errorMessage: string | null;
  /**
   * Log in with username + password.
   *
   * @param username - Account username.
   * @param password - Plain-text password (sent over TLS only).
   */
  login: (username: string, password: string) => Promise<void>;
  /** Log out the current user and clear all stored tokens. */
  logout: () => Promise<void>;
  /** Whether the user holds the given role. */
  hasRole: (role: string) => boolean;
}

/**
 * `useAuth` — primary authentication hook.
 *
 * Manages the full token lifecycle: login, logout, and silent token refresh
 * on mount (if a refresh token is stored in sessionStorage).
 *
 * @example
 * ```tsx
 * const { status, user, login, logout, hasRole } = useAuth();
 *
 * if (status === "loading") return <Spinner />;
 * if (!user) return <LoginForm onSubmit={login} />;
 * return <Dashboard user={user} onLogout={logout} />;
 * ```
 */
export function useAuth(): UseAuthReturn {
  const [state, dispatch] = useReducer(authReducer, INITIAL_STATE);

  // Silent refresh on mount
  useEffect(() => {
    const token = loadRefreshToken();
    if (!token) return;
    silentRefresh(token, dispatch);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    dispatch({ type: "LOGIN_START" });
    try {
      const tokenPair = await post<TokenPair>("/auth/login", {
        username,
        password,
      } satisfies LoginRequest);

      setAccessToken(tokenPair.access_token);
      storeRefreshToken(tokenPair.refresh_token);

      const user = await fetchCurrentUser();
      dispatch({ type: "LOGIN_SUCCESS", payload: user });
    } catch (err) {
      const { code, message } = classifyError(err);
      dispatch({ type: "LOGIN_FAILURE", errorCode: code, errorMessage: message });
    }
  }, []);

  const logout = useCallback(async () => {
    const refreshToken = loadRefreshToken();
    try {
      if (refreshToken) {
        await post("/auth/logout", { refresh_token: refreshToken });
      }
    } catch {
      // Best-effort: clear client state even if the server call fails
    } finally {
      clearAccessToken();
      clearRefreshToken();
      dispatch({ type: "LOGOUT" });
    }
  }, []);

  const hasRole = useCallback(
    (role: string): boolean => state.user?.roles.includes(role) ?? false,
    [state.user]
  );

  return {
    status: state.status,
    user: state.user,
    errorCode: state.errorCode,
    errorMessage: state.errorMessage,
    login,
    logout,
    hasRole,
  };
}

// ---------------------------------------------------------------------------
// useRequireRole hook
// ---------------------------------------------------------------------------

/**
 * `useRequireRole` — enforce role-based access inside a component.
 *
 * Redirects to `redirectTo` when the authenticated user does not hold
 * the required *role*. Pass the `navigate` function from React Router.
 *
 * @param role       - The role string to require (e.g. "admin").
 * @param user       - Current user from `useAuth`.
 * @param navigate   - React Router navigate function.
 * @param redirectTo - Fallback path; defaults to "/unauthorized".
 *
 * @example
 * ```tsx
 * const { user } = useAuth();
 * const navigate = useNavigate();
 * useRequireRole("admin", user, navigate);
 * ```
 */
export function useRequireRole(
  role: string,
  user: AuthUser | null,
  navigate: (path: string) => void,
  redirectTo = "/unauthorized"
): void {
  useEffect(() => {
    if (user !== null && !user.roles.includes(role)) {
      navigate(redirectTo);
    }
  }, [role, user, navigate, redirectTo]);
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

async function fetchCurrentUser(): Promise<AuthUser> {
  const { get } = await import("./apiClient");
  return get<AuthUser>("/auth/me");
}

async function silentRefresh(
  refreshToken: string,
  dispatch: React.Dispatch<AuthAction>
): Promise<void> {
  try {
    const tokenPair = await post<TokenPair>("/auth/refresh", {
      refresh_token: refreshToken,
    });
    setAccessToken(tokenPair.access_token);
    storeRefreshToken(tokenPair.refresh_token);
    const user = await fetchCurrentUser();
    dispatch({ type: "REFRESH_SUCCESS", payload: user });
  } catch {
    clearAccessToken();
    clearRefreshToken();
    dispatch({ type: "REFRESH_FAILURE" });
  }
}

function classifyError(err: unknown): { code: string; message: string } {
  if (err instanceof ApiError) {
    return { code: err.errorCode, message: err.message };
  }
  if (err instanceof NetworkTimeoutError) {
    return { code: "NETWORK_TIMEOUT", message: err.message };
  }
  if (err instanceof NetworkUnavailableError) {
    return { code: "NETWORK_UNAVAILABLE", message: err.message };
  }
  return { code: "UNKNOWN_ERROR", message: "An unexpected error occurred" };
}
