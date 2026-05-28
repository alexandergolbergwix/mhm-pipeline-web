"""Crypto primitives for PII at-rest encryption + zero-knowledge secrets.

Two key categories, lifted straight from the v2 security addendum:

* **Server-held keys** — :data:`MASTER_KEY` and :data:`EMAIL_HMAC_KEY` —
  encrypt PII (email, name) at rest and provide a deterministic blind
  index for searchable encrypted columns (login lookup). The server can
  decrypt these whenever it needs to (invitation emails, member lists).
  Modules: :mod:`app.crypto.pii` and :mod:`app.crypto.index`.

* **User-derived KEK** — derived from the user's password via Argon2id +
  HKDF at login, then wrapped with an ephemeral ``session_secret`` (sent
  to the client as an HTTP-only cookie) and stored in ``sessions.kek_wrapped``.
  Used to wrap per-secret DEKs that encrypt API keys (Gemini, Wikidata,
  Wikibase Cloud bot password). A full server + DB dump leaks nothing
  because the wrapping key only exists in the user's cookie.
  Modules: :mod:`app.crypto.kek` and :mod:`app.crypto.secrets`.
"""
