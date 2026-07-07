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

### Skill: apply an AI-suggested fix and push it live without a full re-upload
1. Run "Verify with AI after upload" (or open the item's AI-verify modal
   manually) so `HmoStudioItemOverride.ai_verdict.suggested_fix` is populated.
2. In `HmoItemDetailDrawer`, click "Apply AI fix" to merge the suggestion into
   the override, or "Apply fix & push" to merge AND immediately call
   `POST /runs/{id}/hmo-studio/items/{local_id}/push` in one action.
3. The push endpoint always resolves override-merged state, so a curator's
   own manual edit works the same way — "Push to Wikibase now" alone
   (no fix) re-publishes the current override state for one item.
4. Check the item's "Last upload" badge / the drawer's upload line for the
   fresh `upload_outcome`/`upload_message`/`upload_at`.

### Skill: enable AI verification during a live upload
1. In the ItemUploadPanel (collapsible "Show build & upload" section on the
   Wikibase Items page), tick "Verify with AI before upload"
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
