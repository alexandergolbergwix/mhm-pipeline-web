# HMO Wikibase Studio — Upload outcomes, single-item push, and upload-lifecycle AI verification

> Up: [HMO Wikibase Studio](README.md) · see also [how it works](how-it-works.md)

**Durable upload-outcome fields (review table).** `fetch_merged_hmo_items`
(`hmo_item_views.py`) joins the latest `wikibase_cloud_writes` row per
`source_uri` (channel `item_upload`, target kind `item`) via
`fetch_latest_wikibase_writes` (`wikibase_audit.py`) — a portable
Postgres/SQLite "latest row per `target_key`" query (`GROUP BY target_key,
MAX(created_at)` joined back, no Postgres-only `DISTINCT ON`) — and adds
three fields to every item dict: `upload_outcome` (`create`/`update`/
`adopt`/`skip`/`unchanged`/`failed`/`None` if never attempted),
`upload_message` (failure/adopt reason, `""` otherwise), `upload_at` (ISO
timestamp). The existing `status` (`would_create`/`created`) field, derived
only from `wikibase_entity_mappings` presence, is untouched — `status` says
"is there a mapping row", `upload_outcome` says "what actually happened the
last time we tried to push it".

`OPERATION_ADOPT` (`wikibase_cloud_write.py`) is a distinct audit-log
operation from `OPERATION_CREATE`: `hmo_item_upload.py`'s reconcile-match
branch records it when an entity links to an *already-existing* live
Wikibase item (found via `hmo_source_uri` SPARQL reconcile) instead of
creating a brand-new one, with `outcome_message` carrying the reconcile
match reason. Without this distinction "new entity" and "linked to a
pre-existing item" were indistinguishable in the audit log.

Frontend: `HmoStudioItem` (`frontend/src/api/hmoStudioItems.ts`) carries the
three fields; `HmoItemUploadOutcomeBadge.tsx` renders a color-coded pill
(create/adopt/update = success tone, skip/unchanged = muted, failed =
danger tone with the message as a tooltip, `—` when never attempted), used
by both `HmoItemTable.tsx`'s filterable "Last upload" column (`ColKey`
extended with `"upload_outcome"`) and `HmoItemDetailDrawer.tsx`.

**Single-item push.** `push_single_item(db, run_id, entity, *, writer,
audit_ctx, update_existing, reconcile_pid, existing_qid)`
(`hmo_item_upload.py`) is the per-entity create-or-update body extracted out
of pass 1's live path — bulk upload behaviour is unchanged, it just calls
this helper now, so there is exactly one place that writes an item live.
`POST /runs/{run_id}/hmo-studio/items/{local_id}/push`
(`hmo_studio_items.py`) builds a `ResolvedWikibaseEntity` from the item's
**override-merged** state (via `fetch_merged_hmo_items`, so a curator-applied
AI fix is included — the bulk upload path does NOT apply overrides, only
this single-item path does) and calls `push_single_item` with
`update_existing=True` always (create if new, update if already mapped).
Per Rule W-40 the endpoint commits its read transaction (run lookup, item
fetch, PID resolution) before the live Wikibase Cloud / SPARQL call.
Frontend: `HmoStudioItems.pushItem(runId, localId)`; the drawer's "Push to
Wikibase now" and combined "Apply fix & push" buttons close the audit → fix
→ publish loop without a full corpus re-upload.

**Upload-lifecycle AI verification.** Reuses the existing
`hmo_wikibase_item` (pre-upload audit) and `hmo_wikibase_item_autofix`
(post-upload live-QID compare) evaluators — no new evaluator was added.
`JOB_KIND_HMO_ITEM_VERIFY = "hmo_item_verify"` (`run_job.py`) is wired into
`verify_job.py::_open_verify_stream` (mirrors the wikidata-verify branch,
calling `hmo_item_actions.get_action` + `hmo_item_verify_event_stream`),
`VERIFY_JOB_CHANNELS` (`verify_session_store.py`), and the verify-params
validation branch in `run_job_params.py` — the same three places every
job-backed verify channel is wired (see the eval-agent block's "add a new
verify channel" skill).

In `ItemUploadPanel.tsx`, two curator-controlled, **default-off** checkboxes
wire this into the upload flow. Per the project's "AI verdicts are
advisory, curator always confirms" invariant, verification runs
automatically when opted in but a failed pre-upload audit never silently
blocks or silently proceeds:

- **"Verify with AI before upload"**: scopes to `would_create` local_ids,
  starts a `hmo_item_verify` job (`action_id: "audit_hmo_wikibase_item"`),
  shows an inline `AgentFlowDiagram` + `VerdictsTable`. Any `fail` verdict
  shows a confirm banner: "Review flagged items" (opens
  `HmoItemVerificationModal` scoped to the failed ids — near-instant, same
  cache) or "Upload anyway" — never a silent block or silent proceed. No
  fails ⇒ the upload starts automatically.
- **"Verify with AI after upload"**: when the live `hmo_item_upload` job
  succeeds, filters `result.outcomes` to `{created, updated, adopted}`,
  auto-starts a `hmo_item_verify` job with
  `action_id: "autofix_hmo_wikibase_item"` scoped to those ids. Verdicts +
  `suggested_fixes` persist automatically into
  `HmoStudioItemOverride.ai_verdict` via the existing
  `_persist_hmo_item_verdicts`, so the table/drawer light up with no
  further backend work.

Both checkboxes' tooltips note the per-item Gemini call is cached (caching
block, Rule W-25) and rate-limited by the action's existing
`rate_limit_rpm` (60 audit, 30 autofix) — no new cost controls were needed.
