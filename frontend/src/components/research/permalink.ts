/**
 * Stateless citable permalink helpers.
 *
 * A SPARQL query is encoded as a URL-safe base64 string (UTF-8 → base64url)
 * and stored in the `?q=` query parameter. No server round-trip needed;
 * the URL is self-contained and can be bookmarked, shared, or cited.
 *
 * encodePermalink(query) → base64url string (no padding characters)
 * decodePermalink(hash)  → original query string, or null on error
 */

export function encodePermalink(query: string): string {
  const bytes = new TextEncoder().encode(query);
  const binary = Array.from(bytes, (b) => String.fromCharCode(b)).join("");
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function decodePermalink(hash: string): string | null {
  if (!hash) return null;
  try {
    const padded = hash.replace(/-/g, "+").replace(/_/g, "/");
    const pad = padded.length % 4;
    const base64 = pad ? padded + "=".repeat(4 - pad) : padded;
    const binary = atob(base64);
    const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  } catch {
    return null;
  }
}
