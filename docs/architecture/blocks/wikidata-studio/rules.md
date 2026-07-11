# Wikidata Studio — Rules

> Up: [Wikidata Studio](README.md)

1. **R1 — Never re-implement builder logic in the web layer.** All item
   construction lives in `backend/converter/wikidata/` (byte-parity with the
   desktop; sync via `pipeline/scripts/sync_converter_to_web.sh`).
   *Why:* every desktop safety fix (Rules 23/38/42/47) must land here by copy, not by re-derivation.
2. **R2 — The reconcile that matters runs INSIDE `upload_items`.** The
   `/reconcile` endpoint is preview-only and MUST never be relied on to change
   upload behaviour. *Why:* a stale preview mutating in-memory items was the decorative-guard bug of the 2026-06-08 audit.
3. **R3 — Fail closed on lookup errors.** A `ReconciliationUnavailableError`
   MUST block the item (`status="blocked"`), never fall through to CREATE.
   *Why:* a transient WDQS outage read as "no existing item" mints duplicates at scale.
4. **R4 — Every creatable entity type MUST have a dedup path**: manuscripts by
   P3959→shelfmark, persons by conflict-checked identifiers, works by
   label+author (rejecting differing P50). *Why:* the raw first-match path caused the ~5,948-item bulk-deletion request.
5. **R5 — `validate_item` is a hard gate in the write path**, not just a build
   badge. Any ERROR-severity issue blocks create AND update, regardless of
   curator approval. *Why:* the moat closest to the write is the one that must never miss.
6. **R6 — Live wikidata.org writes NEVER run without `MORATORIUM_LIFTED=true`**
   (or `WIKIDATA_TEST_MODE=true` → test.wikidata.org). Enforced twice: web
   layer and uploader. *Why:* desktop Rule 25 — the moratorium is the standing consequence of the 2026-04 incidents.
7. **R7 — Never modify items we didn't create.** The uploader's four Rule-38
   guards (`_is_our_item` + three `_assert_modifiable`/conflict checks) MUST
   stay intact in `wikidata_upload.py`; `uploader.py` stays byte-identical to
   desktop. *Why:* community items were mass-damaged once; UPDATE semantics are gated on first-revision authorship.
8. **R8 — Every P/Q constant in `property_mapping.py` MUST be verified against
   the live Wikidata page before landing**; wrong constants removed get a
   `_KNOWN_BAD_*` entry + unit test. *Why:* Q179808 (Palme d'Or as "palimpsest") and P7416-as-count shipped silently from memory-derived constants.
9. **R9 — Curator edits persist ONLY as `WikidataItemOverride` diffs**, applied
   at rebuild time before validation; never mutate cached `result_items` or
   builder output directly. *Why:* items are rebuilt from source on every fingerprint change — un-persisted edits evaporate.
10. **R10 — QuickStatements export is gated by default.** `GET …/quickstatements.txt`
    runs `_prepare_for_upload` when `gated=true` (default): blocked items are
    excluded with comment headers; WDQS outage returns 503. Ungated export
    requires `gated=false&ack=raw` and is logged. *Why:* raw QS CREATE lines
    bypass reconcile, validator, moratorium, and Rule-38 guards (Rule W-30 residual).
11. **R11 — The build never runs inside an HTTP request when there is no cache
    row** — it goes through the `wikidata_studio_build` run-job (409 + attach).
    A stale cache is served with `cache_stale=true`, not silently rebuilt.
    *Why:* Heroku's 30 s router timeout, and passive page loads must not spawn surprise job-tray banners.
12. **R12 — Live uploads use the curator's own encrypted Wikidata token**
    (Settings → `_unwrap_user_secret`), never a shared credential. *Why:* attribution and the `_is_our_item` authorship check both depend on the acting user.
13. **R13 — Wikidata Studio parity with HMO review surface (Rule W-57).**
    Merged read model (`fetch_merged_wikidata_items`), durable
    `wikidata_upload` audit rows, global QID ledger + adopt semantics,
    AI-verdict persistence on overrides, single-item push, gated QS, and the
    table/drawer/upload-hub UI are the canonical curator paths. *Why:* the
    2026-04 duplicate-creation failure mode needed audit + ledger + surface
    parity, not guards alone.
14. **R14 — `page_size` on `GET /wikidata-studio` is capped at 500.** Bulk
    loads (modern items panel, pre-upload verify scope) MUST paginate via
    `fetchAllStudioItems` / `STUDIO_MAX_PAGE_SIZE` — never pass a larger
    `page_size` in one request. *Why:* a single `page_size=5000` request 422s
    and the UI shows 0 items.
15. **R15 — Background jobs surface in the global job tray.** `WikidataStudio.tsx`
    MUST subscribe to `run_job_update` WebSocket events and call
    `useRunJobs.upsertJob` (parity with HMO Studio). Verify modals use
    `useVerifyJob`, which also upserts on start and on every poll tick so the
    tray appears immediately — not only after the 2 s `listMine` poll. If the
    start request rejects before a job exists, it MUST roll back the modal to
    idle rather than display a cancellable phantom job.
    *Why:* without push + upsert, the verify modal shows Stop while the
    bottom-right tray stays empty; an H12 enqueue failure must not reproduce
    that same misleading state (Rule W-61).

16. **R16 — AI-verdict fingerprints MUST use the same canonical record IDs and
    MARC slice when verifying, persisting, cache-reading, and rendering.**
    Wikidata build artifacts use `records`; worker fixtures use `record_ids`;
    both resolve through `wikidata_verdict_cache` before stale-verdict
    sanitisation. Unmarked pre-fix keys are compatible only when their legacy
    fingerprint still matches. *Why:* a mismatched fingerprint hides a valid
    persisted verdict as stale, leaving the review table blank.
