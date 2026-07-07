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
