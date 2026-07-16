/**
 * frontend-client/apiClient.ts
 *
 * Base HTTP client for all API calls.
 * Handles auth-header injection, response parsing, and typed error mapping.
 */

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const API_BASE_URL: string =
  process.env.REACT_APP_API_BASE_URL ?? "http://127.0.0.1:8000";

const DEFAULT_TIMEOUT_MS = 10_000;

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

/** Represents an error returned by the backend with a known HTTP status. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly errorCode: string,
    message: string,
    public readonly detail?: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Raised when the request times out before the server responds. */
export class NetworkTimeoutError extends Error {
  constructor(timeoutMs: number) {
    super(`Request timed out after ${timeoutMs}ms`);
    this.name = "NetworkTimeoutError";
  }
}

/** Raised when the network is unavailable (e.g. offline, DNS failure). */
export class NetworkUnavailableError extends Error {
  constructor(cause?: unknown) {
    super("Network unavailable — check connectivity");
    this.name = "NetworkUnavailableError";
    if (cause instanceof Error) this.cause = cause;
  }
}

// ---------------------------------------------------------------------------
// Request options
// ---------------------------------------------------------------------------

export interface RequestOptions {
  /** Override the default timeout for this request (ms). */
  timeoutMs?: number;
  /** Additional headers to merge with the default set. */
  headers?: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Attach an AbortController to a fetch call and cancel it after *timeoutMs*.
 *
 * @param timeoutMs - Milliseconds before the request is aborted.
 * @returns A tuple of [signal, cleanup]. Call cleanup() in finally blocks.
 */
function withTimeout(timeoutMs: number): [AbortSignal, () => void] {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  return [controller.signal, () => clearTimeout(id)];
}

/**
 * Extract a human-readable error from a non-2xx response body.
 * Falls back to `statusText` when the body cannot be parsed as JSON.
 */
async function parseErrorBody(
  response: Response
): Promise<{ message: string; errorCode: string; detail?: string }> {
  try {
    const body = await response.json();
    return {
      message: body?.message ?? body?.error ?? response.statusText,
      errorCode: body?.error_code ?? "API_ERROR",
      detail: body?.detail,
    };
  } catch {
    return { message: response.statusText, errorCode: "API_ERROR" };
  }
}

// ---------------------------------------------------------------------------
// Token storage (in-memory only — never persist tokens to localStorage)
// ---------------------------------------------------------------------------

let _accessToken: string | null = null;

/** Store the access token in memory. */
export function setAccessToken(token: string): void {
  _accessToken = token;
}

/** Clear the stored access token (e.g. on logout). */
export function clearAccessToken(): void {
  _accessToken = null;
}

// ---------------------------------------------------------------------------
// Core request function
// ---------------------------------------------------------------------------

/**
 * Send an HTTP request to the backend API.
 *
 * @param method  - HTTP verb (GET, POST, PUT, PATCH, DELETE).
 * @param path    - Path relative to `API_BASE_URL`, e.g. `/users/123`.
 * @param body    - Optional request payload; will be JSON-serialised.
 * @param options - Optional per-request overrides (timeout, extra headers).
 * @returns       Parsed JSON response body typed as `T`.
 *
 * @throws {ApiError}               On 4xx/5xx responses from the backend.
 * @throws {NetworkTimeoutError}    When the server does not respond in time.
 * @throws {NetworkUnavailableError} On network-level failures.
 */
export async function request<T = unknown>(
  method: string,
  path: string,
  body?: unknown,
  options: RequestOptions = {}
): Promise<T> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const [signal, cleanup] = withTimeout(timeoutMs);

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
    ...options.headers,
  };

  if (_accessToken) {
    headers["Authorization"] = `Bearer ${_accessToken}`;
  }

  const url = `${API_BASE_URL}${path}`;

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new NetworkTimeoutError(timeoutMs);
    }
    throw new NetworkUnavailableError(err);
  } finally {
    cleanup();
  }

  if (!response.ok) {
    const { message, errorCode, detail } = await parseErrorBody(response);
    throw new ApiError(response.status, errorCode, message, detail);
  }

  // 204 No Content — return empty object
  if (response.status === 204) {
    return {} as T;
  }

  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Convenience wrappers
// ---------------------------------------------------------------------------

/** Perform a GET request. */
export const get = <T>(path: string, opts?: RequestOptions) =>
  request<T>("GET", path, undefined, opts);

/** Perform a POST request with an optional JSON body. */
export const post = <T>(path: string, body?: unknown, opts?: RequestOptions) =>
  request<T>("POST", path, body, opts);

/** Perform a PUT request. */
export const put = <T>(path: string, body?: unknown, opts?: RequestOptions) =>
  request<T>("PUT", path, body, opts);

/** Perform a PATCH request. */
export const patch = <T>(path: string, body?: unknown, opts?: RequestOptions) =>
  request<T>("PATCH", path, body, opts);

/** Perform a DELETE request. */
export const del = <T>(path: string, opts?: RequestOptions) =>
  request<T>("DELETE", path, undefined, opts);
