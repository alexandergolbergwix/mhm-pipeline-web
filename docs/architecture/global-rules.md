# Global rules (system-wide invariants)

> Up: [System Design](system-design.md) · [AGENTS.md](../../AGENTS.md)

Block-specific rules live in each block's `rules.md`. These apply everywhere:

- **G1 — Trust boundaries are process boundaries.** Modal and the eval-agent
  are reached via HTTPS/subprocess only; the FastAPI backend never imports
  their code. *Why:* keeps deploys, dependencies, and failure domains
  independent.
- **G2 — `backend/converter/` is vendored, byte-identical to desktop.** Sync
  with `pipeline/scripts/sync_converter_to_web.sh`; never hand-edit unless
  the same edit lands upstream. *Why:* the desktop repo is the source of
  truth for shared pipeline logic.
- **G3 — No external inference call bypasses `cache_lookup_or_call`.**
  *Why:* cost, latency, and rate-limit protection are enforced in one place.
- **G4 — Nothing durable lives only on dyno disk.** Any on-disk build result
  needs a Postgres write-through counterpart. *Why:* Heroku wipes local disk
  on every deploy/restart (Rule W-39 incident).
- **G5 — Never hold an open DB transaction across a slow external call.**
  Commit before HTTP-with-retry / subprocess / SSE work. *Why:* the 2-minute
  idle-in-transaction timeout kills the connection mid-job (Rule W-40).
- **G6 — External writes are fail-closed.** Wikidata/Wikibase uploads pass
  reconcile + validator gates inside the write path; lookup errors block,
  never create. Live Wikidata writes additionally require
  `MORATORIUM_LIFTED=true`. *Why:* the April mass-merge / June mass-duplicate
  incidents.
- **G7 — Curator mutations are events first.** `apply_event(...)` before the
  read-model update, for all versioned entity types. *Why:* the event log is
  the authoritative history; read-models are caches.
- **G8 — Every user-visible UI surface gets an e2e spec** (Playwright with
  deterministically mocked backend), and any new public-endpoint or guard
  behaviour extends its pinning test suite. *Why:* tests are the regression
  contract; docs describe, tests enforce.

Full incident history: repo root [CLAUDE.md](../../CLAUDE.md) (W-1…W-60).
