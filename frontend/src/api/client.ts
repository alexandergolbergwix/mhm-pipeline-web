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
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "include",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = (await res.json()) as { detail?: string };
      if (data?.detail) detail = data.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  // Guard against the FastAPI SPA-fallback "200 HTML" pathology: if a
  // backend route is misregistered, FastAPI serves the prebuilt
  // index.html with 200 and a text/html content-type. Without this
  // check the SDK would throw a confusing "Unexpected token '<'" deep
  // inside JSON.parse. Surfacing it here lets the caller show a clear
  // "endpoint missing or backend stale — restart uvicorn" message.
  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new ApiError(
      502,
      `Backend returned ${contentType || "no content-type"} instead of JSON for ` +
        `${path}. Likely the route isn't registered or the dev server is stale; ` +
        `restart uvicorn.`,
    );
  }
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};
