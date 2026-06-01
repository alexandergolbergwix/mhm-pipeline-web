/**
 * Tiny fetch wrapper. Always includes credentials so the HTTP-only
 * session cookie travels with every request.
 */

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
    this.name = "ApiError";
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
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "include",
    cache:       "no-store",
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = (await res.json()) as { detail?: unknown };
      detail = coerceErrorDetail(data?.detail, res.statusText);
    } catch {
      /* response wasn't JSON — keep statusText */
    }
    throw new ApiError(res.status, detail);
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
  const msg = typeof r.msg === "string" ? r.msg : "";
  const loc = Array.isArray(r.loc) ? r.loc.join(".") : "";
  if (msg && loc) return `${loc}: ${msg}`;
  if (msg)        return msg;
  if (loc)        return loc;
  try { return JSON.stringify(r); } catch { return "<error>"; }
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};
