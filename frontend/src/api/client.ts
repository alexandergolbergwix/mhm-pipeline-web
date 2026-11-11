/**
 * Tiny fetch wrapper. Always includes credentials so the HTTP-only
 * session cookie travels with every request.
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    public payload: unknown = null,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

/** Wait before retry 1, 2 and 3 of a failed read. Total extra wait: about 7 s. */
export const RETRY_DELAYS_MS: readonly number[] = [1_000, 2_000, 4_000];

/** Gateway statuses that a read request may safely repeat (Heroku H12 → 503). */
const RETRYABLE_STATUSES: ReadonlySet<number> = new Set([502, 503, 504]);

function isRepeatSafe(method: string): boolean {
  const m = method.toUpperCase();
  return m === "GET" || m === "HEAD";
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

/**
 * ``fetch`` with automatic retry for network failures ("Failed to fetch")
 * and gateway errors (502/503/504). Only GET and HEAD retry. A write that
 * failed on the network may still have reached the server, so it is never
 * repeated here. Each retry waits longer, with jitter.
 */
export async function fetchWithRetry(
  url: string,
  init: RequestInit,
  method: string,
  delays: readonly number[] = RETRY_DELAYS_MS,
): Promise<Response> {
  return readWithRetry(url, init, method, async (response) => response, delays);
}

async function readWithRetry<T>(
  url: string,
  init: RequestInit,
  method: string,
  read: (response: Response) => Promise<T>,
  delays: readonly number[] = RETRY_DELAYS_MS,
): Promise<T> {
  const budget = isRepeatSafe(method) ? delays : [];
  for (let attempt = 0; ; attempt += 1) {
    const last = attempt >= budget.length;
    try {
      const res = await fetch(url, init);
      if (last || !RETRYABLE_STATUSES.has(res.status)) return await read(res);
      await res.body?.cancel();
    } catch (err) {
      if (isAbort(err) || err instanceof ApiError) throw err;
      if (last) {
        if (err instanceof SyntaxError) {
          throw new ApiError(502, "The server returned incomplete JSON. Reload the page to try again.");
        }
        throw err;
      }
    }
    await wait(budget[attempt] * (0.75 + Math.random() * 0.5));
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  // Force-bypass any browser / service-worker cache. The SPA's index
  // fallback (text/html) can otherwise stick to API URLs forever if a
  // previous response got cached during a dev-server hiccup. The
  // ``cache: "no-store"`` directive tells Fetch to skip both the
  // HTTP cache AND ServiceWorker cache; ``Cache-Control: no-cache``
  // covers any intermediate proxy.
  const headers: Record<string, string> = {
    "Cache-Control": "no-cache",
    "Pragma":        "no-cache",
  };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  // CSRF double-submit: for state-changing requests, echo the
  // ``mhm_csrf`` cookie value back as an ``X-CSRF-Token`` header.
  // The backend rejects with 403 when the header is missing or does
  // not match the cookie. GET/HEAD are exempt (safe methods).
  const upperMethod = method.toUpperCase();
  if (upperMethod !== "GET" && upperMethod !== "HEAD") {
    const csrf = _readCookie("mhm_csrf");
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  return readWithRetry(`/api${path}`, {
    method,
    credentials: "include",
    cache:       "no-store",
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  }, method, async (res) => {
    if (!res.ok) {
      let detail = res.statusText;
      let payload: unknown = null;
      try {
        const data = (await res.json()) as {detail?: unknown};
        payload = data?.detail ?? null;
        detail = coerceErrorDetail(data?.detail, res.statusText);
      } catch {
        /* response wasn't JSON — keep statusText */
      }
      throw new ApiError(res.status, detail, payload);
    }
    if (res.status === 204) return undefined as T;
    // Guard against the FastAPI SPA-fallback "200 HTML" pathology: if a
    // backend route is misregistered OR the browser served a stale
    // cached SPA index for this URL (we've seen Vite proxy hiccups do
    // this), FastAPI / Vite return index.html with 200 + text/html.
    // Without this check the SDK would throw a confusing "Unexpected
    // token '<'" deep inside JSON.parse.
    //
    // Recovery: text/html on an API URL almost always means the user's
    // session expired (cookie auth bounced through the SPA fallback)
    // OR the browser cached an old fallback response. We force a hard
    // navigate to /login so the SPA re-authenticates, which clears
    // both states without the user having to debug their browser cache.
    const contentType = res.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      if (typeof window !== "undefined" &&
          !window.location.pathname.startsWith("/login")) {
        // Push the user to /login. Their session probably expired or
        // their browser cached the SPA fallback for this API URL.
        window.location.assign("/login?next=" + encodeURIComponent(
          window.location.pathname + window.location.search,
        ));
      }
      throw new ApiError(
        401,
        "Session expired or stale browser cache. Reloading to log in…",
      );
    }
    return (await res.json()) as T;
  });
}


/** Render whatever ``response.json().detail`` carried into a single
 *  human-readable string. FastAPI returns three shapes:
 *    - string (HTTPException.detail) — use directly
 *    - object {msg, loc, type, …} — single Pydantic validation error
 *    - array of objects — list of Pydantic validation errors
 *
 *  Without this coercion the array case ended up rendered as
 *  ``[object Object],[object Object],...`` because React's JSX
 *  stringifies an array of objects with Array.prototype.toString().
 */
function coerceErrorDetail(detail: unknown, fallback: string): string {
  if (detail === undefined || detail === null) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => stringifyValidationError(d)).join("; ") || fallback;
  }
  if (typeof detail === "object") return stringifyValidationError(detail);
  return String(detail);
}

function stringifyValidationError(d: unknown): string {
  if (!d) return "";
  if (typeof d === "string") return d;
  if (typeof d !== "object") return String(d);
  const r = d as Record<string, unknown>;
  const message = typeof r.message === "string" ? r.message : "";
  const msg = typeof r.msg === "string" ? r.msg : "";
  const loc = Array.isArray(r.loc) ? r.loc.join(".") : "";
  if (message) return message;
  if (msg && loc) return `${loc}: ${msg}`;
  if (msg)        return msg;
  if (loc)        return loc;
  try { return JSON.stringify(r); } catch { return "<error>"; }
}

/** Read a cookie value from ``document.cookie`` by name. Returns the
 *  decoded value or an empty string when the cookie is absent.
 *  Used by the CSRF double-submit logic to echo the ``mhm_csrf``
 *  cookie back as an ``X-CSRF-Token`` request header.
 */
function _readCookie(name: string): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie.match(
    new RegExp("(?:^|;\\s*)" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "=([^;]*)"),
  );
  if (!match) return "";
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return match[1];
  }
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

/**
 * Return the headers that any state-changing request needs to clear
 * the backend's CSRF double-submit check. GET/HEAD return ``{}``.
 *
 * Exported so call sites that bypass the typed ``api.*`` wrapper —
 * raw ``fetch()`` for FormData uploads, SSE streams, etc. — can
 * stitch the header into their own request init without re-reading
 * ``document.cookie`` themselves.
 */
export function csrfHeaders(method: string): Record<string, string> {
  const m = method.toUpperCase();
  if (m === "GET" || m === "HEAD") return {};
  const token = _readCookie("mhm_csrf");
  return token ? { "X-CSRF-Token": token } : {};
}
