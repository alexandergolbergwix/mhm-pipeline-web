/// <reference types="vite/client" />

// App-specific env vars that the build reads via import.meta.env.
//
// Vite only exposes vars prefixed with VITE_ to client code. Adding
// the explicit declaration here gets us autocomplete + tsc safety
// instead of `any` from the broad `vite/client` reference above.
interface ImportMetaEnv {
  /** Cloudflare Turnstile site key (public; safe to ship to clients). */
  readonly VITE_TURNSTILE_SITE_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
