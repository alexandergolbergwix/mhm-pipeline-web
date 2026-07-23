# HMO Wikibase Studio — Skills

> Up: [HMO Wikibase Studio](README.md)

### Skill: bootstrap a fresh Wikibase schema
1. Confirm server OAuth: `GET /api/hmo-wikibase-schema/verify` must return
   `ok: true` as the configured write user.
2. Dry-run: `POST /api/hmo-wikibase-schema/bootstrap` with `{"dry_run": true}` —
   review `would_create` entries (and optionally AI-verify them via the schema
   panel's verify modal).
3. Live: POST again with `{"dry_run": false, "run_id": <any run>}` → 201 with a
   `JOB_KIND_HMO_SCHEMA_BOOTSTRAP` job; poll `GET /runs/{run_id}/jobs/{job_id}`.
4. Re-running is idempotent — mapped URIs are skipped. Check
   `GET /api/hmo-wikibase-schema/status` for mapped vs. total counts.

### Skill: re-run HMO schema AI verify after ontology or rubric changes
1. Dry-run bootstrap (or `GET /api/hmo-wikibase-schema/last-report`) so the
   report reflects current datatype inference — `refresh_report_datatypes` patches
   stale `datatype` fields on skipped rows when the reader improves.
2. Open the schema panel → **Verify with AI**; pick a tier-1 judge; enable
   **override cache** so pre-W-47 verdicts (keys without `description`) re-judge.
3. Export verdicts JSON from the panel; expect a sharp drop in false "missing
   description" partials once description + OWL metadata reach the prompt.
4. If a property still fails on datatype, check OWL (`property_kind` +
   `rdfs:range`) before changing Wikibase — see **R22** and Rule W-47.

### Skill: re-run HMO item AI verify after CN/description/rubric fixes (Rule W-48)
1. **RDF rebuild** the run (description stamps live in `graph_builder.py`).
2. **HMO Studio → Rebuild (skip cache)** so `HmoStudioItemCache` picks up
   `control_numbers` + refreshed descriptions.
3. Open **Verify with AI** (or **Autofix with AI** for live-QID rows); enable
   **override cache** — rubric, evaluator, and build fields all changed.
4. Optional **Reupload (update existing)** only when live wiki labels/descriptions
   should change; verify alone does not need a wiki push.
5. Export verdicts JSON and compare to the prior export; expect a sharp drop in
   "no MARC context" fails and generic-description partials.

### Skill: run an item upload for a run
1. Build the RDF graph first (RDF Graph section), then
   `POST /runs/{id}/hmo-studio/build-items` (409 → schema bootstrap is behind
   the ontology; re-run it).
2. Review in the HMO Studio items panel; fix labels/statements via overrides,
   approve items, run the AI audit if desired.
3. Preview: `POST /runs/{id}/hmo-studio/upload-items` with `{"dry_run": true}`.
4. Live: `{"dry_run": false}` (add `"update_existing": true` to refresh
   already-uploaded items) → background job; progress shows
   `N/M items uploaded` then `N/M item links added`. Cancellation returns a
   partial result. Failures per item appear in `outcomes`/`link_outcomes` and
   the audit log.

### Skill: debug a stuck or repeating coverage report
1. A 409 `hmo_coverage_in_progress` is normal for up to ~15 minutes — poll the
   returned `job_id`.
2. If the same run keeps rebuilding after deploys, check the `hmo_coverage_cache`
   row: a fingerprint mismatch means the RDF TTL actually changed; a missing row
   means the job's durable write-through failed (see the
   `failed to persist durable coverage cache` warning in logs).
3. Stale/stuck job rows are reclaimed by the job maintenance loop (Rule W-38);
   check `run_jobs.claimed_by` to see which dyno owns it.

### Skill: rotate Wikibase Cloud credentials
1. Create/keep the `mhm-pipeline-web` wiki account on the Wikibase Cloud
   instance and register a new OAuth consumer **under that account**.
2. `heroku config:set WIKIBASE_CLOUD_OAUTH_CLIENT_ID=... WIKIBASE_CLOUD_OAUTH_CLIENT_SECRET=...`
   (or `WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN` for a direct bearer token);
   `WIKIBASE_CLOUD_WRITE_USER` must match the account name.
3. Verify with `GET /api/hmo-wikibase-schema/verify` — a username mismatch is a
   deliberate hard failure (R10). Bot passwords are a test-only legacy path.

### Skill: adopt an item that already exists on the wiki
Use `POST /runs/{id}/hmo-studio/items/{local_id}/reconcile` — it SPARQL-matches
by `hmo_source_uri` and records the mapping (`status: adopted`) without writing
to the wiki; 503 means the SPARQL endpoint is down (fail closed, retry later).

### Skill: apply an AI-suggested fix and push it live without a full re-upload
1. Run **Autofix with AI** on the Wikibase Items toolbar (or "Verify with AI
   after upload", or the row drawer's **Autofix with AI**) so
   `HmoStudioItemOverride.ai_verdict.suggested_fix` is populated.
2. In `HmoItemDetailDrawer`, click "Apply AI fix" to merge the suggestion into
   the override, or "Apply fix & push" to merge AND immediately call
   `POST /runs/{id}/hmo-studio/items/{local_id}/push` in one action.
3. The push endpoint always resolves override-merged state, so a curator's
   own manual edit works the same way — "Push to Wikibase now" alone
   (no fix) re-publishes the current override state for one item.
4. Check the item's "Last upload" badge / the drawer's upload line for the
   fresh `upload_outcome`/`upload_message`/`upload_at`.

### Skill: enable AI verification during a live upload
1. In the ItemUploadPanel lifecycle bar (directly above the Wikibase Items
   review table), tick "Verify with AI before upload"
   and/or "Verify with AI after upload" — both default off.
2. Before-upload: a `fail` verdict shows a confirm banner; choose "Review
   flagged items" to open the verify modal scoped to just those ids, or
   "Upload anyway" to proceed regardless. No fails ⇒ upload starts on its own.
3. After-upload: verdicts land automatically on
   `HmoStudioItemOverride.ai_verdict` for every created/updated/adopted item —
   no manual re-verify step needed before using the "apply fix" skill above.
4. Cost note: both actions are cached (Rule W-25) and rate-limited
   (`rate_limit_rpm`: 60 audit / 30 autofix) — safe to leave on for routine
   uploads of a few dozen items; for a multi-thousand-item corpus consider
   running "before" only, and "after" as a separate scoped follow-up.

### Skill: verify place authority links

When a place looks mislinked, inspect `wikibase_id` separately from RDF
`hm:external_wikidata_uri`. Normalize the MARC label, verify the KIMA row's
Wikidata URI, and rebuild the HMO items before judging the export.

The table labels `Wikibase QID (local)` explicitly. External Wikidata/VIAF
links are shown in a separate authority column; never interpret the local QID
as a public Wikidata identifier.

HMO item links must use `https://mhm-hmo.wikibase.cloud/wiki/Item:<local QID>`.
The `w3id.org/mhm/ontology#…` source URI is provenance metadata and must not be
rendered as the live entity link.

### Skill: inspect persisted authority enrichment

Open an item drawer and inspect **Authority enrichment**. It lists only
accepted claims already attached to the HMO item, grouped by Mazal, KIMA,
VIAF, and Wikidata, with external links where available. Candidate matches
that were ambiguous or failed the false-positive guard are intentionally not
shown; use the Authority review surface to investigate those candidates.


### Skill: rebuild, upload, and read back a production run

For an explicitly authorized migration of an existing run, use
`backend/.venv/bin/python -m scripts.rebuild_run_rdf_and_items <run-id> --upload`
from `backend/`. The command rebuilds RDF and HMO drafts, updates existing
Wikibase items, reads each live item back, and persists the canonical snapshots.
Without `--upload` it remains a local rebuild only.

### Skill: audit/backfill canonical entities

Run `backend/.venv/bin/python backend/scripts/backfill_hmo_canonical_entities.py` for a read-only report. It scans persisted live read-backs, rejects malformed or duplicate snapshots, and writes nothing by default. After reviewing the report, rerun with `--apply` (optionally `--run-id <uuid>`) to idempotently replace that run's canonical rows.


### Skill: enforce the canonical gate

Run `backend/.venv/bin/python backend/scripts/check_hmo_canonical_gate.py --run-id <uuid>` before enabling canonical RDF or Wikidata projections. The command exits non-zero unless every built entity has a valid durable canonical row and no duplicates or missing read-backs exist.


### Skill: staged canonical-first rollout

Set `HMO_CANONICAL_FIRST=true` only after running the backfill and canonical gate for the target runs. Canonical reconciliation then refuses cache-only fallback and requires durable HMO rows. Leave it false during migration and shadow comparison.
