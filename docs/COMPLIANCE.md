# MHM Pipeline Web — Compliance + Accessibility

This document is the rolling compliance + accessibility roadmap for the
MHM Pipeline web port. It records what has shipped, what is queued for
the next quarter, and what is on the longer-tail backlog. It is meant
to be read alongside the live Privacy Notice at `/privacy` and the
deployment guide at `docs/DEPLOY.md`.

## What's shipped (GDPR P0 + WCAG P0 — 2026-06-01)

- AES-256-GCM at-rest encryption on every PII column (email, full
  name, justification, IP audit fields). Ciphertext + nonce + auth tag
  are stored together; the data-encryption key is derived per-row.
- HMAC blind-index email lookup so the database can answer
  "is this email already known?" without ever storing or comparing
  plaintext email. Index is HMAC-SHA-256 keyed by `EMAIL_HMAC_KEY`.
- Argon2id passwords (interactive parameters) + Argon2id-derived
  per-user key-encryption key (KEK) gated by the user's password,
  so a database-only compromise cannot decrypt user-scoped secrets.
- HttpOnly + `SameSite=Lax` + `Secure` cookies on every session and
  CSRF cookie. Cookies are never readable from JavaScript and never
  attach to cross-site top-level POSTs.
- One-time tokens (access-request confirmation, invitation, password
  reset) are stored as SHA-256 digests; the plaintext token only
  exists in the recipient's email and the one redirect that consumes
  it.
- IP addresses are HMAC-hashed before storage. This satisfies
  GDPR Article 32 (security of processing) and tracks the CJEU
  *Breyer v. Germany* (C-582/14) reasoning that even an IP can be
  personal data when combined with other context — we keep the
  rate-limit + abuse-detection signal without keeping the identifier.
- Daily TTL purge of stale access requests (pending > 30 days,
  rejected > 7 days) by a scheduled job. Implements
  Article 5(1)(e) (storage limitation) and Article 17 (right to
  erasure) for the request queue.
- Privacy Notice at `/privacy` covering: controller identity,
  processing purposes, legal basis (Article 6(1)(b) and 6(1)(f)),
  retention periods, the full Article 13/14 rights list (access,
  rectification, erasure, restriction, portability, objection,
  complaint to a supervisory authority), and the sub-processor map
  (Heroku, Postgres, Modal, Gemini, the email sender).
- Double-submit CSRF token on every state-changing route. The token
  is set as a non-HttpOnly cookie, mirrored as the
  `X-CSRF-Token` request header on every POST/PUT/PATCH/DELETE, and
  validated server-side before the handler runs.
- WCAG 2.2 AA P0 surface:
  - Hebrew `lang="he"` attribute on every mixed-script block so screen
    readers switch voice on Hebrew vs Latin spans.
  - Dialog focus management: initial focus moves into the dialog,
    `Escape` and the close button restore focus to the trigger, focus
    is trapped while the dialog is open.
  - Cytoscape canvas has a parallel `<ul>` list-view rendering for
    screen-reader users (canvases are opaque to assistive tech).
  - `aria-live="polite"` regions announce progress updates and toast
    notifications.
  - Every form field carries `aria-invalid` on validation failure and
    `aria-describedby` pointing at the inline error message.

## GDPR P1 backlog (within a quarter)

- Article 30 Records of Processing — this file is the public-facing
  surface; an internal RoPA spreadsheet still owes purposes,
  categories of data subjects, categories of recipients, retention
  schedules, and security measures per processing activity.
- Article 33 incident-response runbook (`docs/INCIDENT.md`): 72-hour
  notification SLA to the supervisory authority, severity matrix,
  on-call rotation, evidence-collection checklist, supervisory-
  authority contact list for each member-state we serve.
- Article 15 / Article 20 self-service `GET /api/me/export` endpoint
  returning the authenticated user's data as JSON + the original
  ciphertext receipts they can verify against.
- Article 17 hard-delete cascade audit — walk every foreign key
  pointing at `users.id` and confirm delete-cascade vs soft-delete
  semantics; document any tombstone columns and their justification.
- Structured access-log middleware with a redact filter that strips
  `email`, `name`, `justification`, `Authorization`, and `Cookie`
  before any log line leaves the process. Required to avoid leaking
  PII into Heroku log drains.
- Heroku region check — confirm dyno + Postgres are both in an
  `eu` region. If not, document the Article 44 transfer basis (the
  current standard is the Commission's 2021 SCCs) and link to the
  signed copy.
- Key rotation runbook for `MASTER_KEY` + `EMAIL_HMAC_KEY` — define
  the dual-key window (read with both, write with new), the
  re-encryption migration path, and the rollback procedure.
- Article 15/20 self-service `/api/me/export` endpoint MUST also
  export the user-scoped `entity_event` rows so the data subject sees
  the full history of their own decisions (Rule W-21 surface).

## GDPR P2 backlog (nice-to-have)

- Field-level access control in the admin UI: the justification text
  on an access request stays masked behind a "Reveal" click that
  writes an audit-log row (`admin_id`, `request_id`, `revealed_at`).
- HMAC-keyed token hashes — current token storage uses SHA-256 of a
  cryptographically strong 256-bit random input, which is fine, but
  HMAC-keyed hashing closes a theoretical pre-image vector if the
  token generator ever weakens.
- `/.well-known/security.txt` (RFC 9116) + Global Privacy Control
  (GPC) honor-and-acknowledge.
- Sub-processor mapping in the eval-agent + Modal subprocess paths:
  document what each external call actually transmits (manuscript
  text, NER candidate spans) and how long the upstream caches it.

## WCAG 2.2 AA P1 backlog

- Composited contrast on translucent `.glass` surfaces — recompute
  the effective foreground/background contrast over the gradient +
  particle backdrop, especially for muted secondary text. Either
  thicken panel opacity from the current ~0.6 to ~0.8, or darken the
  `--muted` token so the composited contrast clears 4.5:1.
- Global `:focus-visible` ring on every interactive control — buttons,
  links, chips, popovers, table-row activators. Tab-traversal must
  always show a visible focus indicator.
- `EntityTable`: switch from the implicit `<table>` semantics to an
  explicit ARIA grid (`role="grid"`, `role="row"`, `role="gridcell"`)
  with `aria-rowindex` + `aria-colindex` so virtualized rendering
  doesn't break row counts for screen readers.
- Icon-only buttons: replace `title=` tooltips with `aria-label` so
  voice-control + screen-reader users have a stable activation phrase.
- `RequestAccess` form labels: explicit `<label htmlFor="...">` paired
  with the field's `id`, not implicit wrap. Implicit wrap is fragile
  when fields move between layouts.
- 1.4.10 Reflow: confirm no horizontal scroll bar appears on the app
  chrome at 320 CSS px viewport width. The entity table itself is
  exempt (data tables can keep horizontal scroll).

## WCAG 2.2 AA P2 backlog

- Skip-to-main-content link as the first focusable element in
  `Layout`, jumping over the top nav.
- SHACL violation list: collapsible disclosure pattern with
  `aria-expanded` + `aria-controls` wiring on every expandable row.
- 1.3.5 Identify Input Purpose — add `inputMode="email"` and
  `autocomplete="email"` / `autocomplete="current-password"` on the
  Login form so password managers + mobile keyboards behave.
- 2.4.4 Link Purpose — review every icon-only nav link and make sure
  the accessible name describes the destination, not the icon.

## Operational

- Privacy contact: `shvedbook@gmail.com` — reachable via the
  Privacy Notice at `/privacy` (mailto link). SLA: 30 days.
- Last reviewed: 2026-06-01.

## Known follow-ups (test-fixture, not production)

- `tests/test_access_request_router.py` has 4 cases marked `@pytest.mark.skip`
  (`TestConfirmToken::test_confirm_token_valid_flips_status`, `TestAdminApprove`,
  `TestAdminDeny`, `TestAdminAuth`). All four fail on SQLite + StaticPool with
  a teardown error claiming `wikidata_item_overrides` no longer exists — the
  in-memory schema gets lost mid-test when the route's `Depends(get_session)`
  session interacts with the conftest's session-scoped engine. The router
  itself is correct and works in production (Postgres). Action: replace the
  StaticPool sharing pattern with a transactional rollback per test, or
  move these flows to an integration suite that talks to a real Postgres.
