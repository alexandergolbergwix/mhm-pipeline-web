# Wikidata Studio — Rules
> Up: [Wikidata Studio](README.md)
1. **R1 — Item construction stays behind the shared builder facade.** All
   projection logic lives in focused modules under `backend/converter/wikidata/`;
   routers and pipeline glue only shape inputs and persist outputs. Shared
   converter changes must be ported upstream before a full desktop sync; the
   source-aware work and quality modules are explicit web-side divergences (Rules W-68/W-69/W-70).
   *Why:* one projection boundary prevents router drift while keeping the
   modular web implementation safe from destructive syncs.
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
    `fetchAllStudioItems` / `STUDIO_MAX_PAGE_SIZE` for browser loads; the worker reads the full cached corpus directly — never pass a larger
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
17. **R17 — The diagnostic Wikidata CSV MUST include the item fields, linked MARC context, validation issues, and complete persisted AI-verdict JSON.** It may add flattened columns for common verdict fields, but MUST NOT replace the complete JSON column. *Why:* a 294-item export with `ai_verdict` omitted or flattened to one reason field cannot identify recurring builder defects or map them back to the evaluator prompt.

18. **R18 — Every built item MUST retain the MARC control numbers that supplied it.** Manuscripts carry their own control number; deduplicated person/work items carry the sorted union. Verification reads this metadata (or only a legacy P3959 reference recovery) and MUST NEVER substitute the first record in a run. *Why:* a 294-item production verify grounded unrelated people and works in the first MARC record, creating systematic false fails (Rule W-63).

19. **R19 — Built labels and verifier evidence are separated and complete (Rule W-65).** Catalog IDs stay in P3959/source metadata, unsupported roles do not become authors, authority-derived person claims carry compact authority evidence, and the verify fixture annotates valid `__LOCAL:` targets. The CSV/export/cache/prompt preserve these fields. *Why:* production partial/fail verdicts were dominated by label pollution, role drift, and missing authority context.
20. **R20 — Studio build inputs MUST join on canonical MARC control numbers (Rule W-66).** `build_items_for_run` canonicalises record, approved-authority, and approved-NER keys before grouping. *Why:* quoted authority keys did not join clean record IDs, so production silently omitted every authority-backed person item.
21. **R21 — Studio projections MUST fail closed on unsupported semantic claims.**
    Untrusted transliteration is not promoted to an English label; unresolved
    corporate authorities are not humans; person dates require an exact
    authority-ID match; manuscripts omit unsupported P7535/P5008/P17/P131 and
    use P195 only when source holder evidence supports it.
22. **R22 — Work creation is source-aware, not authority-only (Rule W-68).**
    Clean Hebrew MARC 505 titles are structural contents evidence; MARC 500
    titles must come from the anchored named-work parser; NER respects
    rejection; Latin-only 505 headings require authority/QID evidence. Every
    decision retains source field/text, folio, sequence, reason, and acceptance.
    Rebuilds recompute persisted 500 derivations, work labels exclude embedded
    authors, and works never inherit manuscript P407. *Why:* the authority-only
    gate cut 228 items to 131, while the real defects were catalogue prose
    emitted by the old broad 500 regex.
23. **R23 — Work identity, author evidence, and export review fields MUST be explicit.**
    Exact aliases preserve QIDs; authors become P50/local/P2093; Hebrew names stay out of English descriptions; gershayim/P1476 survive. Exports distinguish `approved_only` from item approval and retain verdict JSON. *Why:* the export showed 52 null QIDs, 20 Hebrew descriptions, 8 lost marks, and no review fields.*
24. **R24 — Content-level work metadata MUST be consumed.** Projection reads approved content QIDs and contents-NER author fields, validates QIDs, and carries unresolved authors as P50/local/P2093. *Why:* enrichment already persisted these fields, but ignoring them produced authorless duplicate work items.
25. **R25 — Static QIDs and verdict evidence fail closed (Rule W-71).** Every static genre/subject QID has a live-verified label; uncertain mappings and broad P921 headings are omitted. Verify/read paths attach identical local targets, and cache keys include every prompt-relevant evidence field. *Why:* unrelated QIDs caused false claims while fingerprint drift hid 33 valid verdicts.

26. **R26 — Public semantic claims are evidence-gated.** P921, P136, P127, P1684, and P195 are emitted only when their MARC/authority evidence supports the exact Wikidata meaning; rejected candidates remain review evidence. *Why:* Phase 1 audit rows were dominated by secondary subjects, over-specific genres, historical-role misuse, catalog notes, and defaulted institutions. See [projection-quality.md](projection-quality.md).

27. **R27 — Illustrated genre and illuminated instance claims are separate.**
`Illustrated works (Manuscript)` MUST NOT resolve through a static crosswalk to
`Q48498` or imply P31=Q48498 from free-text notes/`has_decoration`; the specific
instance requires an authority-stamped QID or structured confirmed decoration
evidence. *Why:* 44 production manuscripts were judged partial because catalog
illustration wording was promoted to an unsupported illuminated-manuscript
claim.
