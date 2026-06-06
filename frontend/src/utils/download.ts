/**
 * Shared browser-download utilities.
 *
 * `downloadFromUrl` triggers a native browser streaming download by
 * creating a hidden anchor and clicking it.  The browser honors the
 * server's `Content-Disposition: attachment; filename=...` header for
 * the final filename; `fallbackName` is only used when the server
 * omits or fails that header.
 *
 * Trade-off: with anchor navigation a server error (403/401) is not
 * caught client-side and would download the error body.  This is
 * acceptable because export endpoints are only reachable by authorized
 * project members and the previous blob-buffering path's only error
 * handling was a thrown Error that most callers ignored anyway.
 */

/**
 * Extract the `filename=` parameter from a `Content-Disposition` header.
 * Handles RFC 5987 (`filename*=UTF-8''x`), quoted (`filename="x.json"`),
 * and bare (`filename=x.json`) forms.  Returns `null` when no usable
 * value is found.
 */
export function parseFilename(cd: string | null): string | null {
  if (!cd) return null;
  // RFC 5987 takes precedence (e.g. `filename*=UTF-8''project%20.json`)
  const star = cd.match(/filename\*\s*=\s*([^']*)'[^']*'([^;]+)/i);
  if (star && star[2]) {
    try {
      return decodeURIComponent(star[2].trim());
    } catch {
      return star[2].trim();
    }
  }
  const quoted = cd.match(/filename\s*=\s*"([^"]+)"/i);
  if (quoted && quoted[1]) return quoted[1];
  const bare = cd.match(/filename\s*=\s*([^;]+)/i);
  if (bare && bare[1]) return bare[1].trim();
  return null;
}

/**
 * Trigger a browser streaming download by navigating a hidden anchor.
 * The browser sends the session cookie automatically (same-origin GET),
 * streams the response directly to disk, and uses the server's
 * `Content-Disposition` filename when present.  `fallbackName` is the
 * `download` attribute backstop when the server omits the header.
 */
export function downloadFromUrl(url: string, fallbackName: string): void {
  if (typeof document === "undefined") return;
  const a = document.createElement("a");
  a.href = url;
  a.download = fallbackName;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}
