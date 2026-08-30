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
6. **R6 — Live wikidata.org writes require an explicit curator `upload_target=live`**
   (or legacy `MORATORIUM_LIFTED=true`). Default is `dry_run`. `upload_target=test`
   writes to test.wikidata.org. Enforced in the web layer and `WikidataUploader`
   (`allow_live`). *Why:* Rule W-103 — UI choice with dry-run default replaces
   env-only moratorium while keeping fail-closed defaults.
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
    row, and force-rebuild always starts `wikidata_studio_build`** — cache miss
    → 409 + attach; Rebuild with skip-cache → `ensureRunJob` + `JobProgressInline`
    (Rule W-106).
    A stale cache is served with `cache_stale=true`, not silently rebuilt.
    *Why:* Heroku's 30 s router timeout, and passive page loads must not spawn surprise job-tray banners.
12. **R12 — Live/test uploads use the curator's own encrypted Wikidata token**
    (Settings → `_unwrap_user_secret`), never a shared credential. Target
    `test` unwraps `wikidata_test` (test.wikidata.org bot); `live` unwraps
    `wikidata`. Both `POST /jobs` (`wikidata_upload`) and
    `POST …/wikidata-studio/upload` enqueue only — never run `upload_items`
    on the request path (Rule W-107 / W-178).
    *Why:* attribution / authorship plus Heroku H12 on sequential writes;
    production bot passwords do not authenticate on test.wikidata.org.
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

28. **R28 — Date parsing is record-isolated and punctuation-aware.** Hebrew
geresh/gershayim in MARC 260/264 dates MUST be treated as punctuation, and a
malformed century token MUST fall through to year parsing rather than aborting
the record or batch. *Why:* the full 123,621-record corpus exposed 5,129
normalization crashes from tokens such as `כ'` being parsed as 20,000.

29. **R29 — P195 requires verified current-holder evidence.** A manuscript MUST
NOT receive the NLI collection QID merely because no other holder was resolved;
P195 requires a current-owner/holding-institution match with a verified
organization QID. *Why:* 43 production partials carried an unsupported default
collection claim.

30. **R30 — Hebrew gershayim are label content, not wrapper noise.** Validation
MUST ignore a quote between Hebrew letters when checking ISBD wrapper balance,
while continuing to flag surrounding/doubled wrapper quotes. *Why:* five work
partials were caused by legitimate abbreviation marks such as `פע"ח`.

31. **R31 — Explicit catalog semantics must be retained without broad inference.**
Verified canonical holders/subjects may project directly; an explicit MARC 100
author plus real 245 title may create a P1574 work chain; explicit printed
facsimile wording may use the printed-book P31. Provenance/photo-credit text
and ambiguous roles remain evidence only. *Why:* the fifth export exposed both
lossy omission and one manuscript/facsimile type failure.

32. **R32 — Placeholder institutions and authority forms MUST remain explicit.**
Unknown-library catalog placeholders are omitted from public descriptions,
authority names retain inverted and Latin forms as aliases, and partial or
chapter-qualified work titles never inherit a canonical QID unless the exact
title is verified. *Why:* the sixth verdict export still contained placeholder
institution text, incomplete person names, and an unsafe Mishneh Torah match.

33. **R33 — Verification exports MUST expose semantic refinements and local-target context.**
Printed facsimiles retain the stable manuscript entity type for upload but
expose a `printed_facsimile` semantic subtype; verified institution mappings
may emit P195; and `__LOCAL` targets include descriptions, aliases, records,
and authority evidence. *Why:* the seventh verdict export still reported a
facsimile type contradiction, an unresolved Israel Museum holder, and an
under-specified local author target.

34. **R34 — Diagnostic statements MUST carry display labels.** Static labels
cover every emitted property and verified institution QID, and local target
values receive a deterministic `value_label`; unsupported correspondence and
catalog-provenance holder claims remain omitted without explicit evidence.
*Why:* post-deploy verdicts were semantically correct but still partial because
null P195/P3959 labels, unresolved local display values, and a provenance
placeholder were visible to the judge.

26. **R26 — Manuscript shelfmark labels use the current holder when known.**
   Ktiv/NLI catalog provenance is distinct from MARC 710 current ownership;
   quote wrappers are removed before both labels and P217.


35. **R35 — Legacy and canonical Studio caches are disjoint.** Cache reads,
fingerprints, and writes MUST include the projection source (`legacy` or
`canonical`). A canonical shadow build must never reuse a legacy row, and
QuickStatements must be compared as part of the rollout audit. *Why:* a
source-blind lookup could make a supposedly canonical comparison display the
legacy payload and hide projection drift.

36. **R36 — Projection follows the WikiProject Manuscripts data model fail-closed (Rule W-98).**
No manuscript-side P50 (including somevalue); no discouraged P31 (e.g. codex);
required P3959; no invented P17/P131; P1574 carries P1932 catalog title form;
annotator/commissioner use P11105/P88; export quality gate runs full
`validate_item` ERRORS. Contract: `docs/wikidata-manuscripts-data-model.md`.
*Why:* DS/WPM modelling gaps and prior MS-side author claims produced
non-conformant entities that the upload moat had to catch late.

37. **R37 — Smart existence + own-or-accept modify (Rule W-99).** Upload
prepare confirms reconciled QIDs via Action API `wbgetentities`, classifies
ownership with the curator token, and by default only CREATE or UPDATE own
items. Foreign QIDs require per-item `accept_foreign_modify` bound to
`accepted_foreign_qid`. Contract: `docs/wikidata-data-access.md`.
*Why:* silent community UPDATE and duplicate CREATE are the two April failure
modes; Rule-38 alone does not surface an explicit curator accept for rare
legitimate foreign edits.

38. **R38 — Project Wikibase P/Q map through ontology, never by numeric identity (Rule W-100).**
`hmo_wikidata_pq_mapper` is the only bridge from HMO Cloud / ontology claims
to public Wikidata PIDs/QIDs. Local `P…`/`Q…` without an ontology URI or
explicit Wikidata evidence are dropped. *Why:* conflating namespaces minted
false public identities (Rule W-83 class).

39. **R39 — AI verify injects WikiProject Manuscripts skill context (Rule W-104 / W-124).**
Wikidata item/autofix judges receive the curated
`eval-agent/config/skills/wikidata_manuscripts/` pack (entity + claim-aware;
Data Model material/creation/content/housing slices).
Verdict salt `WIKIDATA_VERDICT_SCHEMA=w124_v1`. *Why:* builders already
followed WPM; judges must evaluate toward the same public-item contract,
especially for HMO→Wikidata readiness.

40. **R40 — Review tables MUST update mid-run without full-table flicker.**
Live Wikidata upload streams `item_outcome` / `recent_item_outcomes` (with
`label`, `entity_type`, and rolling `outcome_counts` for the progress modal);
the panel patches matching rows via `studioUploadProgress` (parity with HMO R51 /
frontend R14 / Rule W-110). Job-tray **View** on `wikidata_upload` reopens
`WikidataUploadProgressModal` via `?job=` (Rule W-141). Bulk-approve and verify
may silent-reload (throttled) but MUST keep the table mounted when items are
already loaded. Studio **build** still refreshes only at finish (cache written
at end). *Why:* mid-run `setLoading(true)` unmounted the table and flickered
Publication status during publish.

41. **R41 — Related works are evidence-gated (Rule W-114).** Manuscript
`related_works` MUST run `assess_work_candidate`, retain rejected evidence, and
emit P1574 to a verified known QID (or a curator-approved local work with
stamped evidence). Bare titles MUST NOT mint `__LOCAL:work:…` CREATE items.
*Why:* Bible/Tanakh/Haggadah/Tikkun Chatzot related-works rows blocked Studio
rebuild and AI verify via `WORK_WITHOUT_SOURCE_EVIDENCE`.

42. **R42 — AI verify MUST load the same Studio `source` as the table (Rule W-115).**
Verify jobs pass `source` (`legacy`|`canonical`) and `approved_only` with
`item_ids`; the worker MUST NOT hard-code legacy while the UI shows canonical.
*Why:* mismatched local IDs produced “no Wikidata Studio items in scope” for
1608 visible rows.

43. **R43 — Verify scope MUST use the Studio cache and skip WDQS reconcile (Rule W-116).**
    `_fetch_wikidata_verify_items` prefers the cached Studio corpus and rebuilds
    with `reconcile=False` on miss. *Why:* 1608-item verify hammered WDQS and
    looked stuck with zero verdicts.

44. **R44 — Canonical Studio emits only WPM public item types; summarized HMO
    nodes roll up (Rule W-117).** `uploadable_entities_from_hmo` keeps
    `manuscript` / `person` / `work` only. `summarized_in_wikidata` classes
    (Production, CU, Expression, …) contribute claims onto the parent item by
    shared control number via `hmo_wikidata_pq_mapper` + strategy allowlists.
    Verify/export MUST skip non-public `entity_type` rows. Build summary exposes
    `rolled_up_entities` and `summarized_hmo_nodes`. *Why:* a 1608-item verify
    export judged HMO ontology classes (`Codicological_Unit`, views, …) with the
    Wikidata rubric — 93% fail from channel mismatch, not projection defects.

45. **R45 — Merged Studio read paths MUST filter public items; stale canonical
    caches rebuild (Rule W-118).** `filter_public_wikidata_items` /
    `is_public_wikidata_studio_item` in `hmo_canonical_wikidata.py` is the
    single gate for `fetch_merged_wikidata_items`, verify scope, and export.
    Pre-W-117 canonical cache rows whose `entity_type` is an HMO class name
    (`Codicological_Unit`, `E21_Person`, …) are dropped at read time;
    `execute_studio_build` refuses a canonical cache hit when
    `studio_cache_has_non_public_items` is true. GET marks `cache_stale` so
    curators force-rebuild. *Why:* verify alone was not enough — export and the
    review table still served 1204+ HMO rows from stale Postgres cache.

46. **R46 — Studio build jobs skip WDQS reconcile (Rule W-119).**
    `wikidata_studio_build` always calls `execute_studio_build(...,
    reconcile=False)`; canonical assembly runs in `run_in_threadpool`. Live
    reconcile stays on upload / gated QS / preview only. *Why:* force-rebuild
    hammered WDQS, blocked the web dyno (H12 job polls), and tripped asyncpg
    while holding an open DB session.

47. **R47 — Canonical labels + work evidence match legacy hygiene (Rule W-120).**
    `_wikidata_labels_and_aliases` uninverts person names, shelves Hebrew out of
    `en`, and stamps `work_candidate_evidence` for CREATE works (or drops them).
    Fingerprint salt `hmo-wikidata-v5`. *Why:* export (11) had 237/258 blocking
    validator errors after public-type filtering alone.

48. **R48 — Recover MARC 245 / known-QID work evidence (Rule W-121).**
    Canonical CREATE works join prepared MARC 245 titles and exact known-work
    QIDs so main HMO `F1_Work` nodes are not dropped. Fingerprint `hmo-wikidata-v6`.
    *Why:* W-120 fail-closed ~68 valid main works that only had 245 grounding.

49. **R49 — P2888/P973 are browseable Item:Q only (Rule W-122).**
    Drop/rewrite ontology IRIs and dead `/wiki/MS_` slugs; emit only
    `mhm-hmo.wikibase.cloud/wiki/Item:Q…`. Catalog placeholders
    (`רשומה זמנית`) never become P1684; inscription is manuscript-only.
    Fingerprint `hmo-wikidata-v8`.
    *Why:* Wikidata→Wikibase clicks hit w3id LFS pointers / 404 MS_ pages;
    NLI temporary-record markers were projected as inscriptions.

50. **R50 — AI verify receives all evidence channels (Rule W-124).**
    `_fetch_wikidata_verify_items` canonicalises CNs; attaches
    `verify_evidence` (MARC + VIAF + Mazal + Wikidata + HMO Wikibase);
    eval-agent merges multi-CN MARC; skill/rubric `w124_v1` treat every
    pack as first-class and accept browseable HMO Item:Q bridges.
    *Why:* export (12) failed with “No MARC context” while authority/HMO
    evidence was present but invisible to the judge.

51. **R51 — Canonical Studio merges full MARC/authority enrichment (Rule W-125).**
    `execute_studio_build(source=canonical)` merges legacy
    `build_items_for_run` claims onto HMO-rooted items via
    `wikidata_canonical_enrichment.merge_legacy_into_canonical`. Fingerprint
    `hmo-wikidata-v9`. Provenance events, P106, and P1680 participate.
    *Why:* PhD RQ2/RQ4 need research-grade claims; canonical-only emit was
    an identity shell.

52. **R52 — Incomplete Wikidata AI verify is partial, not complete (Rule W-126).**
    `_wikidata_verify_event_stream` merges TRACE + checkpoint verdicts, sets
    `outcome` via `verify_outcome.resolve_verify_session_outcome`, and
    write-throughs fresh TRACE rows when `results.jsonl` is missing.
    *Why:* a hung DeepSeek run reported 54/313 as a successful complete pass.

53. **R53 — Large-scope Wikidata verify MUST stay alive under dyno pressure (Rule W-127).**
    Spawn forces OpenAI-compat `--parallel 1`; synthesize `runner_error` when
    `runner.exit` never arrives; rely on throttled job snapshots + mid-HTTP
    `[STEP]` heartbeats + incremental `results.jsonl`.
    *Why:* job 06b44db0 stopped at 52/313 with no exit/checkpoint.

54. **R54 — Wikidata verify job polls MUST stay light (Rule W-128).**
    Mid-run counters only; slim terminal `session_snapshot`; UI hydrates the
    VerdictsTable from the job result when polls starved mid-run.
    *Why:* job ecfdcf29 looked stuck at 53/313 with VERDICTS (0) after R14/H12.

55. **R55 — Studio list payloads and verify heaps MUST stay Basic-dyno-safe (Rule W-131).**
    `list_view=true` on `GET /wikidata-studio`; full item via
    `GET …/items/{local_id}`; no mid-verify `fetchAllStudioItems` reload;
    lean cached-verdict candidates, compact fixtures, batched incremental
    persist. *Why:* Studio 500-page GET + verify worker + mid-reload R14'd
    the 512 MB dyno during production verify.

56. **R56 — Wikidata verify MUST scope MARC and release in-memory payloads (Rule W-132).**
    `load_run_marc_records_scoped` for verify scope only;
    `release_wikidata_verify_heap` after fixture write; no duplicate verdict
    lists in the worker; `GET …/jobs/{id}?include_session_snapshot=` defaults
    false; verify modal polls every 5 s. *Why:* post-W-131 runs still died at
    ~131/313 with R14 while holding the full run MARC + 313 full items.

57. **R57 — Wikidata verify persist MUST NOT block eval-agent stdout (Rule W-133).**
    `WikidataVerdictPersistBatch.enqueue()` background flush; trace append via
    `asyncio.to_thread`; slim/throttled session GET; UI session reload ≤1/8 s
    mid-run. *Why:* awaited incremental persist caused pipe backpressure →
    ~1 entity/minute judge throughput on Basic.
58. **R58 — Verdict fingerprints MUST be invariant under verify-heap slimming (Rule W-136).**
    `fingerprint_statements` / `fingerprint_verify_evidence` in
    `wikidata_verdict_cache.py` are the single statement/evidence projection used
    by keys, eval-agent fixtures, and `slim_item_for_verdict_persist`; read paths
    (`fetch_merged_wikidata_items`, and `fetch_merged_wikidata_item` which
    delegates to it) attach `local_reference_targets` + `verify_evidence` over the
    whole merged corpus *before* comparing a stored verdict; a verdict keyed
    without the derived evidence packs is still accepted and its `cache_key`
    rewritten forward. *Why:* Rule W-132 slims scope items before persisting, so
    a fingerprint that hashed the full shape produced a `cache_key` no reader
    could reproduce — a 313-item run showed 194/99/20 in the modal and `—` in
    every AI-verdict cell.

59. **R59 — Verify evidence must be complete; manuscript identity is its own (Rule W-137).**
    `marc_verify_context.RAW_TAG_FALLBACK` / `raw_tag_slice()` fill slice names
    the normalised keys cannot (collapsed-key runs store `300$a`, `852$j`,
    `540$a`, `008`); `wikidata_verify_evidence.build_claim_sources()` adds
    per-claim PID → source-field provenance and `build_statement_value_labels()`
    adds offline PID/QID glosses. Manuscript identity (labels, shelfmark,
    description, `records`, the legacy MARC join, one `P3959`) uses
    `identity_control_number()` — the CN in the entity's own source URI, never a
    propagated one. `dedupe_statements()` keeps one claim per (property, value).
    Manuscript descriptions are generated, never `rdfs:comment` catalog notes,
    and `_description_language_slot` keeps Hebrew out of `en`. Hard gates:
    `MANUSCRIPT_SHARED_IDENTITY`, `LABEL_SHELFMARK_MISMATCH`,
    `MANUSCRIPT_MULTIPLE_CATALOG_IDS`, `MANUSCRIPT_MULTIPLE_SHELFMARKS`.
    *Why:* export (13) judged 43 partial / 21 fail — all manuscripts. The judge
    saw four MARC keys, three items shared one shelfmark, all 68 emitted P3959
    4–5×, and 27 descriptions were catalog notes.

60. **R60 — MARC values reach the handlers unwrapped; every claim names its channel (Rule W-138).**
    `marc_ingest._unwrap_marc_value` strips balanced surrounding quotes per
    subfield value *before* multi-value splitting (internal gershayim survives) —
    quoted `041$a` had defeated the 3-char language-code chunker, so 48
    manuscripts carried no `P407`. `_parse_dimensions` handles decimal cm/mm
    pairs (`9.5X14.5 cm` was read as `50` mm). `build_claim_sources` is
    channel-aware (`marc.<slice>`, `authority.*`, `hmo_wikibase`,
    `work_candidate_evidence`, `structural` for P31/P3959) so no emitted PID
    reaches the judge without provenance. Person dates require
    `identifier_only=True` authority matches; works are never indexed by a
    `P1476` value and keep one canonical title; `resolve_local_references`
    degrades orphaned `__LOCAL:` work links to `Q234460` + `P1932`.
    New hard gates: `WORK_MULTIPLE_TITLES`, `DANGLING_LOCAL_REFERENCE`.
    *Why:* export (14) — 89 partial / 33 fail across 308 judged items, 121 of
    122 non-full items explained by these seven defects.

    *Export (15) follow-up:* gate went 589 → 32 → 0 blocking. Added
    `P1922`/`P655`/`P9046`/`P5816` provenance rows, labelled unmapped PIDs
    `unmapped` (not `structural`), applied the attesting-shelfmark work
    description on the legacy path too, and fixed two checker bugs
    (mm-vs-cm dimension comparison; person dates read from
    `authority_evidence`).

    *Export (16) follow-up:* `090$a`/`099$a` (not `952$a–d`) carry the NLI
    shelfmark — 952 is rights/audit text; a work's attestation cites its
    control number (verifiable via `record_ids`/P3959); `P1476` provenance is
    entity-aware (only a 100/245 work cites `marc.title`); a known-QID work is
    never minted as a local CREATE; `P1476`/`P1680`/`P1932` are sanitised like
    labels. New gate codes: `attestation_not_in_evidence`,
    `title_claim_has_quote_wrappers`.

61. **R61 — Verify probes Wikidata for an existing item before CREATE (Rule W-139).**
    `wikidata_duplicate_probe` batches `haswbstatement:` CirrusSearch queries over
    the Action API (never WDQS — Rules W-116/W-119), throttled ≥1.1 s with
    `Retry-After`/`maxlag` handling and content-addressed caching, and stamps
    `verify_evidence.wikidata_existing.duplicate_check`. Identifier matches only
    (P3959 / P214 / P8189 / P244 / P227); `unavailable`/`skipped` means unknown,
    never safe. Gate code: `create_would_duplicate_existing_item`.
    *Why:* 12 of 206 probeable CREATE candidates in run 48ba6c13 already exist on
    Wikidata (Q66439 Yom-Tov Lipmann Heller, Q467161 Samuel ibn Naghrillah, …).

62. **R62 — Recover MARC metadata deterministically; generate only for prose (Rule W-140).**
    A head-to-head against `Q134603946` (an NLI manuscript hand-created on
    Wikidata in 2025) showed 23 statements to our 15 — and measurement showed
    most of the gap was data we already had: `856$u` present on 63/68 with
    `P953` on 1, `300$a` on 62 with `P1104` on 45. `converter/transformer/extent.py`
    is now the single extent parser (sums leaf sequences, page + gematria units,
    `None` for unnameable units, folio references and bare numbers); `856$u`
    yields `P953` behind an `http(s)` gate at the emit site; `materials_in_text`
    keeps `P186` on the closed vocabulary (`בכתיבות אחדות` is hands, not
    material); and the `he` description takes its language from the record.
    `marc_llm_extract` sends 500/541/561/563/583 prose to a tier-1 model
    (DeepSeek V4 Flash on Qubrid) and keeps a proposal only when its `span` is
    verbatim in the record and its property is an audited constant
    (P127/P1071/P186). Proposals surface as `verify_evidence.llm_proposals` —
    advisory, never claims, and barred by the rubric from moving any verdict axis.
    *Why:* generation is not an evidence channel (Rules W-72/W-67/W-138), so the
    only safe LLM output is a span-grounded candidate a curator confirms.

63. **R63 — Projection must not lose what the build already knows (Rule W-142).**
    Auditing export (18) found four lost-information bugs: 32 of 42 degraded
    `P1574` claims named a work present in the same build (now relinked by title
    — `P1932` "stated as" and `P1476` the work's own title are distinct, and a
    self-relink is refused); the MARC `856$u` catalogue URL was suppressed
    because `P973` was already occupied by the HMO bridge (only an identical
    *value* is a duplicate now, and `P31` stays strict per W-98); a printed
    facsimile kept `P31=Q87167` because only the legacy path reads `דפוס צלום`;
    and the `he` label hardcoded `ספרייה לאומית`. LLM proposals were mined after
    the cache row was written and so never persisted.
    *Also:* a judge verdict is a hypothesis — it claimed `tat` maps to `Q4927`,
    which is live-verified as *"Category:History of Dominica"*, not Tatar.
64. **R64 — Subset AI verification MUST retain freshly persisted verdicts.**
    Verdict persistence stores a stable item-state fingerprint alongside the
    strict prompt/evidence fingerprint. The merged review path may use the
    stable key when only scope-dependent MARC/evidence enrichment differs;
    semantic item edits still invalidate the verdict.
    *Why:* re-verifying the `unknown` rows persisted known verdicts, but the
    read-time full evidence pack changed the strict fingerprint and hid them
    from the main table.

65. **R65 — Verify fingerprints MUST use override-merged Studio items.**
    The verify scope loader applies the same curator label, description,
    alias, and statement overrides as the merged review view before building
    verdict fingerprints. Raw cache payloads and the visible table must never
    represent different judged item states.
    *Why:* 56 freshly judged rows remained `unknown` because the verifier keyed
    raw cache items while the table keyed their override-merged counterparts.

66. **R66 — Verdict reads MUST validate against the pre-derived item projection.**
    The merged review view retains the override-merged cache projection used
    by verification and uses it for stable-key validation; ledger QIDs and
    local-reference display labels are derived presentation data.
    *Why:* the verifier and table agreed on semantic input, but post-verify
    enrichment hid 55 freshly persisted verdicts.

67. **R67 — Read validation MUST reuse the persist-slim projection.**
    Stable-key validation uses `slim_item_for_verdict_persist` on the retained
    cache-plus-override projection, exactly as write-through persistence does.
    *Why:* 27 latest verdicts still had valid stored bodies but were keyed
    after heap slimming, while the read path compared the unslimmed item.

68. **R68 — Stable verdict keys MUST ignore subset-derived local labels.**
    The durable fingerprint omits `value_label` generated for `__LOCAL:` targets;
    those labels depend on which rows were selected for a subset verification.
    *Why:* verifying only the 27 unknown rows produced different labels than
    the full 364-row table and hid every persisted verdict.

69. **R69 — Identifierless person cache rows MUST be stale.** A Studio cache
    containing a person with an ERROR-level `NO_IDENTIFIER` validation issue
    MUST fail the cache-hit guard and be returned with `cache_stale=true` until
    a rebuild writes the current projection. Build schema `v5` invalidates
    pre-gate rows across legacy and canonical sources.
    *Why:* run 48ba6c13 displayed an old `mazal:987007257211705171` person row
    with a notability error even though the current builder emits P214/P8189;
    the unchanged cache fingerprint kept the bad row reviewable.*

70. **R70 — Canonical merges MUST not emit identifierless persons.** After
    canonical HMO items and the fresh legacy enrichment are matched, recover
    trusted VIAF/NLI/QID from HMO and legacy evidence shapes (Rule W-188),
    then every person retained MUST have an existing QID or a non-empty
    P214/P8189/P244/P227/P213/P268 statement. Identifierless canonical or
    unmatched legacy persons are omitted by the merge and by the final
    canonical assembly after overrides/reconciliation, before cache
    persistence and validation. Never CREATE a name-only person.
    *Why:* the rebuild job still persisted an old `mazal:*` person after
    the legacy notability gate had skipped it; its only P8189 was buried
    in a reference snak.*

71. **R71 — Canonical public claims MUST be finalized after filtering and remain source-scoped.**
    Apply overrides, remove identifierless or authority-date-conflicted people,
    sanitize broad subjects/current-holder claims, and only then resolve every
    `__LOCAL:` target. Person authority hard-reject flags suppress both
    biographical dates and the public person projection; work evidence records
    the MARC control number that accepted it. *Why:* the first post-W-154
    rebuild still left unsupported local edges, broad `P921`, stale `P195`,
    chronologically impossible people, and work evidence whose source record
    was invisible to the judge.

72. **R72 — Review-table verdict reads MUST replay verify's adoption and live labels (Rule W-169).**
    `fetch_merged_wikidata_items` runs cached duplicate-QID adoption (Rule W-168)
    and, when any stored verdict exists, `attach_live_value_labels` on both the
    display rows and the pre-derived stable rows *before* slim + stale
    sanitisation. *Why:* verifying only the 19 unknowns on run 48ba6c13 persisted
    correct verdicts, but the table still showed unknown — CREATE-shaped cache
    rows and unglossed statement QIDs hashed differently than the verify scope.

73. **R73 — Non-passing projection buckets MUST stay fail-closed (Rule W-170).**
    Canonical soft-reject strips identity PIDs on crosscheck / heading_mismatch /
    same-family·different-given conflation (including `אל חמדי`↔`אלחמדי`) and on
    date-gated patronymic father-ID (Person_91); clears bad `existing_qid`;
    adoption blocks on heading or same-script conflict; cross-script adopts with a
    trusted Latin form **or** a fail-closed HE→Latin given/surname map
    (hyphen-aware; secondary tokens); on refuse, strip the matched identifier and
    rewrite duplicate status to `absent`; manuscripts skip the label gate; P1559
    re-aligns to `labels.he` after merge and is label-backed in claim_sources;
    subset verify attaches `__LOCAL:` targets from the full Studio catalog;
    person subjects do not ground bible-book P921; catalog-note P1684 is dropped;
    weak editor/commentator descriptions without dates stay generic;
    `manuscript_record_label` names the holder or says `catalog record`, never
    invents `NLI record`; MARC role wrappers normalise before ROLE_TO_PID so
    `(מעתיק)` emits P11603; P1071 is production-place only (bare 751 and
    `$e=related place` stay on P7153; Amran city = Q172597, never Kufra
    Q48200); manuscript P1476 stays MARC 245 (no shelfmark swap); orphan
    bare-QID P3342 drops; quantity `unit` reaches the judge fixture (mm);
    `(מוזכר)` strips from aliases.
    Regression tests live in `test_wikidata_nonpassing_buckets.py`.
    *Why:* a blanket preferred strip would have dropped 16 full-passing persons;
    bare HE↔EN adopt would have linked Maurizio→Kagel and Philippson;
    refuse-without-strip left CREATE+live P8189 as W-139 fails; subset verify
    emptied `local_reference_targets` and partialled true work links; export-29
    P1476→shelfmark without rewriting claim_sources cost 19 full manuscripts;
    export-31 Amran→Kufra and mm-without-unit were judge-visible projection
    defects.*

74. **R74 — Corpus-scale Mode-α predicates + Mode-β sticky-full (Rule W-171).**
    Projection fixes are MARC/role/heading predicates (`isbd_title`,
    `work_link_specificity`, `identity_compatible` at person-link emit,
    leaf-unit extent, incipit gate, work-title alias subtraction, century
    year vs embedded CE). Verify: rubric/skill `w171_v1` Mode-β hygiene;
    `partition_wikidata_verify_cache` sticky-full on matching
    `claims_fingerprint`; override merge carries `ai_verdict`. No CN
    allowlists. Regression:
    `test_wikidata_corpus_scale_w171.py`.
    *Why:* export-32 mixed real projection defects with schema-bump
    re-judge noise; hardcoding the 18 canary IDs would not hold on ~123k
    filtered MARC rows.*

75. **R75 — Judge fixture keeps quantity `unit` + export-33 hygiene (Rule W-172).**
    `eval-agent` `compact_statements` MUST pass through `unit`/`value_type`.
    Hebrew MS descriptions omit shelfmark; P1684 rejects scholarly catalog
    attribution; person aliases require `heading_matches`; facsimile works
    get facsimile descriptions; schema/skill `w172_v1`. Regression:
    `test_wikidata_export33_w172.py`.
    *Why:* export-33 false-partialled nine manuscripts whose P1104 already
    had leaf unit, because the judge never saw `unit`.*

76. **R76 — RELATED_WORKS P1574 ladder + Hebrew preferred identity (Rule W-173).**
    `related_works` emit path runs `refine_exemplar_work_qid`; record-side
    piyyut evidence blocks bare Bible/Tanakh aliases; ladder QIDs including
    Esther **Q131068** stay in `QID_LABELS` (else `MISSING_VALUE_LABEL`);
    `is_incipit_text` rejects `בשנת`/`בעמוד` chronology; Hebrew preferred
    mismatch strips P8189; schema/skill `w173_v1`. Regression:
    `test_wikidata_export34_w173.py`.
    *Why:* export-34 left Bible on a piyyut MS via RELATED_WORKS and three
    persons with wrong Mazal P8189 that same-family conflict missed.*

77. **R77 — Catalogue P973 shelfmark gate + Hebrew brackets + holder gloss (Rule W-174).**
    Drop external P973 whose embedded ref ≠ P217; expand Hebrew
    `מע[וצ']ה` brackets before note-strip; `_enrich_snak` uses `qid_label`
    (audited holding table); P195 only via `institution_qid`; work-title
    alias elaborations stripped on legacy+canonical; schema/skill
    `w174_v1`. Regression: `test_wikidata_export35_w174.py`.
    *Why:* export-35 partialled a BL cross-record 856 URL, a Leeds holder
    the judge misread as Robarts, work-title aliases, and a mangled Hebrew
    personal name.*

78. **R78 — Sticky-full survives schema bumps + non-passing verify default (Rule W-175).**
    `sanitise_stale_wikidata_verdict` reuses sticky-full; cache keys omit
    presentation `value_label`/`property_label` (fixtures keep them);
    schema salt only for Mode-β rubric/skill changes; Studio primary
    button is Verify non-passing; schema/skill `w175_v1`.
    *Why:* each W-171…W-174 salt bump blanked every pill and forced
    full-corpus re-verify for unchanged fulls.*

79. **R79 — Designation labels keep only the 245 as alias (Rule W-176).**
    Under holder+shelfmark / `כתב יד…` labels, drop non-245 aliases
    (variant titles, 500-note works); re-apply after merge. Regression:
    `test_wikidata_export36_w176.py`.
    *Why:* export-36 Mode-β partialled seven MSS solely for second aliases
    naming contained works.*

80. **R80 — Upload natives MUST carry build-adopted QIDs (Rule W-177).**
    `_build_native_items` ends with `_apply_cached_qid_adoption_to_native`
    (Studio-cache `existing_qid` overlay + probe-cache adoption). Regression:
    `test_wikidata_upload_qid_hydrate.py`.
    *Why:* export-37 showed 13 on-Wikidata UPDATEs while upload SPARQL-
    reconciled them as CREATE and fail-closed on WDQS.*

81. **R81 — Test vs live Wikidata Settings secrets (Rule W-178).**
    `wikidata_test` for `upload_target=test`; `wikidata` for live. Settings
    UI stores both; upload/push fail closed with a target-specific message
    when the matching secret is missing.
    *Why:* production OAuth/bot logged into test as anonymous
    (`Login failed. An anonymous token was returned`).*
82. **R82 — Upload jobs log in once; abort on auth failure (Rule W-179).**
    One shared `WikidataUploader` + `ensure_authenticated` before the loop;
    abort remaining items on login/rate-limit failures. Regression:
    `test_wikidata_upload_login_once.py`.
    *Why:* export-38 turned 3 bad logins into 113 rate-limit failures.*
83. **R83 — WBI writes use `is_bot` kwarg, default false (Rules W-180 / W-181).**
    `wbi_item.write(summary=…, is_bot=mark_as_bot)`; never bare `bot=`.
    Default `mark_as_bot=False` unless `WIKIDATA_MARK_AS_BOT=true`.
    Regression: `test_wikidata_upload_is_bot.py`.
    *Why:* export-39 TypeError on `bot=`; export-40 hard-fail without bot right.*
84. **R84 — Upload natives = Studio table; test clears missing/foreign QIDs (Rule W-181).**
    `_build_native_items` prefers Studio cache → `studio_dict_to_native_item`;
    on test, missing QIDs, foreign-but-alive Q-numbers, and WDQS outages become
    CREATE (never UPDATE community test items). Regression:
    `test_studio_dict_to_native.py`, `test_wikidata_upload_guards.py`,
    `test_wikidata_existence.py`.
    *Why:* export-40 — 100× never (HMO-only rebuild) + 14× missing-on-test;
    job 5ea88249 skipped foreign Q209579.*
85. **R85 — Test uploads remap; leftovers refuse the write (Rules W-182 / W-183 / W-186).**
    `test_wiki_compat` matches **exact** English label + datatype, keys maps by
    `(live_pid, value_type)`, `WikidataUploader._ensure_test_maps_for_item`
    searches or stub-CREATEs (adopt-on-conflict), rewrites snaks, then
    `filter_item_for_test_wiki` lists leftovers. A leftover blocks the item.
    Live keeps the full WPM set. Remaining `Bad value type` fails once.
    Regression: `test_wikidata_test_wiki_compat.py`.
    *Why:* test.wikidata.org P/Q numbers are not public Wikidata entities;
    smoke-path claim drops would fail expert review.*
86. **R86 — Ownership before UPDATE (Rule W-184).**
    `_is_our_item` triple-check on live **and** test; `register_foreign_accept`
    live-only; ownership before test adapt; SPARQL false on test falls back to
    Action API; test Q remap uses bot-owned, session-stub, or MHM-description
    targets only; skipped foreign QIDs do not enter `created_qids` on test.
    On test, foreign-alive UPDATE targets clear to CREATE instead of skip.
    Regression:
    `test_wikidata_upload_guards.py`, `test_wikidata_test_wiki_compat.py`,
    `frontend/e2e/wikidata-item-table.spec.ts`.
    *Why:* foreign-accept cache priming bypassed Rule-38; test WDQS false-negatives.*
87. **R87 — Test upload reliability (Rule W-185).**
    Batch/throttled existence prefetch; quantity unit alias → wiki-local URI
    (never dimensionless when a unit was projected — W-186); upload rehydrate
    recovers trusted IDs then omits identifierless persons (skipped, not
    blocked) with P2093 rollup (W-188);
    session-death aborts job. Regression:
    `test_wikidata_existence.py`, `test_wikidata_upload_guards.py`,
    `test_wikidata_test_wiki_compat.py`, `test_wikidata_canonical_enrichment.py`,
    `test_wikidata_upload_is_bot.py`.
    *Why:* job c51fc5e6 — 150 existence blocks, 6 mm unit failures, 3 NO_IDENTIFIER.*
88. **R88 — Test write quality matches live (Rule W-186).**
    Remap must preserve every projected claim/qualifier/reference/unit or the
    item is `blocked`. Millimetre/centimetre/metre Q-ids are in `QID_LABELS`
    for stub CREATE. Regression: `test_wikidata_test_wiki_compat.py`.
    *Why:* leftover-drop CREATE produced items that would fail expert review.*
89. **R89 — Test remap adopt-on-conflict + datatype-keyed maps (Rule W-187).**
    `_wbeditentity_new` adopts conflicting Q/P ids; `_test_pid_map` keys
    `(live_pid, value_type)` with exact-label search + datatype confirm;
    MHM stub descriptions (`MHM test stub for live {qid}`) are reusable claim
    values; foreign-but-alive test QIDs clear for CREATE (W-181). Regression:
    `test_wikidata_test_wiki_compat.py`, `test_wikidata_existence.py`,
    `test_wikidata_upload_guards.py`.
    *Why:* job 5ea88249 — 179 W-186 blocks from CREATE races and wrong-type P reuse.*
90. **R90 — Recover trusted person identifiers before the omit gate (Rule W-188).**
    Stamp P214/P8189/existing_qid from HMO and legacy evidence *before*
    W-154 drop (assembler and merge keep). `accepted is False` and W-166
    untrusted flags still forbid recovery; name-only people stay omitted.
    Regression: `test_wikidata_canonical_enrichment.py`,
    `test_hmo_canonical_wikidata.py`, `test_authority_evidence.py`.
    *Why:* test upload skipped people who already had VIAF/NLI in evidence.*
91. **R91 — Test remap stubs every live Q and typed P; item CREATE adopts (Rule W-189).**
    Holder/runtime QIDs use `institution_label` then live English labels then
    `MHM live {qid}` — never skip stub CREATE. Wrong-datatype label collisions
    CREATE `{label} (MHM {datatype})`. Item-write label conflicts UPDATE our
    test Q or uniquify the EN description. Regression:
    `test_wikidata_test_wiki_compat.py`.
    *Why:* job fbe9ad8c — 43 leftover blocks (JTS/P1680) and 2 CREATE fails.*
92. **R92 — Person live-identity is bidirectional leftover-token coverage (Rule W-190).**
    Keep a person `existing_qid` only when HE and EN cover each other.
    Clash at upload prepare: own → `blocked`; foreign/absent identity
    PIDs are stripped and the QID cleared — then **skip** if the person
    is still identifierless (W-195), never CREATE.
    Regression: `test_wikidata_person_identity_w190.py`.
    *Why:* catalog people adopted Savoy/Sultan/Ben-Shaul live QIDs.*
93. **R93 — Writes land every native statement (Rule W-191).**
    Session `created_qids`, works→persons→manuscripts, refuse unbuilt
    snaks and unresolved `__LOCAL:`; test `exists` only if claim-complete.
    `_build_claim` accepts canonical `wikibase-item`. Regression:
    `test_wikidata_upload_claim_complete_w191.py`.
    *Why:* one-item job loop dropped `__LOCAL:` and thin `exists` rows.*
94. **R94 — Deferred `__LOCAL:` connections write on pass 2 (Rule W-192).**
    Pass 1 omits unresolved local snaks; pass 2 MERGE-UPDATEs them once
    both QIDs exist. Uncreated targets stay `unresolved`. The upload modal
    shows both steps with bars, Now, and ETA. Regression:
    `test_wikidata_upload_deferred_links.py`,
    `wikidataUploadProgressModal.spec.ts`.
    *Why:* works-first order blocked every work on P50 `__LOCAL:`.*
95. **R95 — Exists-check matches Wikibase quantity amounts and repaired times (Rule W-193).**
    `11` = `+11` = `11.0`; `format_wikidata_time` / `_build_claim` never
    emit `+-199`. Regression: `test_wikidata_upload_claim_exists_w193.py`.
    *Why:* job 248a8ecf blocked 59 manuscripts that already had folios/mm.*
96. **R96 — Live natives must be identity-safe and `__LOCAL:`-resolved before judge/upload (Rule W-194).**
    `sanitize_studio_items_for_live` runs at Studio build and again in
    `audit_test_wikidata_upload` / `judge_test_wikidata_live_ready` with live
    labels. Person clashes (W-190) and forbidden work UPDATE QIDs (Tikkun
    Chatzot Q2740944) clear `existing_qid`. In-corpus `__LOCAL:` rewrites to
    the target's remaining live Q; dangling P1574 degrades to Q234460;
    in-batch CREATE leftovers stay for W-192. The live-ready rubric must not
    fail those leftovers, and the CLI judge defaults to
    `deepseek-ai/DeepSeek-V4-Flash`. Regression:
    `test_wikidata_live_native_hygiene.py`.
    *Why:* cache still stamped Savoy/Sultan/Kostlitz/Tikkun/Shor live QIDs;
    Kimi scored in-batch `__LOCAL:` as not live-ready.*
97. **R97 — Clash-cleared identifierless persons skip live writes; uncertain duplicates confirm fail-closed; pass 2 UPDATE is own-only (Rule W-195).**
    After a W-190 strip, identifierless persons are `skipped` (W-154 / W-185),
    not CREATE. DeepSeek confirms only non-identifier matches that survived
    identity gates; clash never reaches the LLM; unsure / fail / `same_item`
    without a shared id skip. Pass 2 MERGE UPDATE only created/own QIDs;
    leftover P50 to a skipped person becomes P2093 on our work. Live-ready
    `skip_for_live` drops them from the written denominator. Never copy test
    Q/P; never UPDATE Savoy Q209579 / Sultan / Kostlitz / Tikkun Q2740944 /
    Shor Q6580025. Regression: `test_wikidata_duplicate_confirm.py`,
    `test_wikidata_person_identity_w190.py`,
    `test_judge_test_wikidata_live_ready.py`.
    *Why:* live-ready still treated clash-cleared name-only people as CREATE
    and pass 2 could MERGE onto foreign live QIDs.*
98. **R98 — Live, test, and dry-run upload progress share one two-step widget.**
    `WikidataUploadSteps` (Step 1 Write items / Step 2 Add connections, Now,
    ETA) MUST render in the progress modal, the upload panel, and the job
    tray for every `upload_target`, including live. Title and badge still
    follow frontend R19 (do not invent live). *Why:* live www.wikidata.org
    writes showed only the generic `JobProgressInline` bar while
    test.wikidata.org opened the two-step Now/ETA UI.
99. **R99 — Catalog work CREATE skips to a unique allowlisted live title; titles are monolingualtext; implausible millimetres are omitted (Rule W-196).**
    P1476/P1680/P1684 coerce to `monolingualtext` in the mapper and live
    hygiene. Codex P2048/P2049 above 1000 mm omit at emit and hygiene.
    After SPARQL miss, `inlabel` + P31 allowlist may yield one QID.
    Upload skips CREATE/UPDATE and stores `link_qid` for manuscript P1574.
    Persons without an identity PID stay `unsure`. Never UPDATE Q2873224 /
    Q2740944. *Why:* live job `8dbc4090` failed 23 string titles, wrote
    5180 mm, and CREATEd Av HaRachamim beside Q2873224.

100. **R100 — Transliteration prewarm MUST isolate cache sessions from the build session (Rule W-197).**
    `execute_studio_build` snapshots MARC dictionaries before prewarm. Prewarm
    reads and writes use short-lived bulk sessions, while external label calls
    run concurrently without a shared `AsyncSession`. *Why:* concurrent cache
    commits and rollbacks expired `RunRecord.marc`; the next ORM access raised
    `MissingGreenlet` and failed the build before item construction.

101. **R101 — Reused work items MUST retain source-backed author links (Rule W-198).**
    A work lookup can find an existing work before the builder sees its author
    evidence. The reuse path and the approved work-QID path MUST attach an exact
    approved `P50` claim, or a safe local target or `P2093` fallback, when the
    work has no author claim.
    The new claim MUST carry the source record reference. The builder MUST NOT
    add a second `P50` or `P2093` claim.

102. **R102 — Linked QIDs MUST not trigger CREATE duplicate blockers (Rule W-199).**
    An `existing_qid` marks an item as a link or update candidate. Duplicate
    evidence and the export audit MUST classify that item as `already_linked`,
    even when a stored verdict contains stale `candidates_found` data. Upload
    identity and ownership checks still apply before an update.

103. **R103 — Work candidates MUST preserve exact links and author identity (Rule W-200).**
    A structured work QID on the same record MUST satisfy related-work rows
    without a local CREATE. Equal titles with different authors MUST use
    separate local work candidates. A source-backed work MUST carry `P50` or
    `P2093`, or the build quality gate MUST reject the export.

104. **R104 — Canonical works MUST retain approved anchor-record authors (Rule W-201).**
    The canonical HMO adapter MUST copy the approved author from the work's
    anchor MARC record into `P50` or `P2093`. The claim MUST carry the NLI
    reference. A legacy merge MUST not be required for author recovery.

105. **R105 — Legacy work projection MUST use approved MARC author matches (Rule W-202).**
    When the normalized author list is empty, the legacy content projection MUST
    use an approved author match from the same MARC record. An unapproved match
    MUST not create an author claim.

106. **R106 — Canonical merges MUST rewrite local references after an ID change (Rule W-203).**
    When a canonical and legacy item merge, the canonical `local_id` wins.
    Every `__LOCAL:<legacy_id>` in statements, qualifiers, and reference
    metadata MUST point to the canonical `local_id` before final local-reference
    resolution. *Why:* Run `(52)` dropped a valid work `P50` claim after a
    legacy Mazal person merged into its canonical HMO person.
107. **R107 — Work author claims MUST use one safe representation (Rule W-204).**
    A safe `P50` target MUST replace a duplicate `P2093` fallback. The retained
    claim MUST keep the removed claim's references and qualifiers. An unresolved
    `P50` MUST yield to `P2093` so the work keeps its source-backed author signal.
    *Why:* Export `(53)` preserved canonical `P50` and legacy `P2093` together,
    which created a blocking author-claim defect.

108. **R108 — Ledger QID hydration MUST not invalidate stored verdicts (Rule W-205).**
    The merged read model MUST keep upload-ledger QIDs in display rows, but it
    MUST mirror only QIDs returned by the current cached duplicate adoption into
    the stable verdict projection. *Why:* Run `48ba6c13` stored 236 verdicts,
    but ledger QIDs changed 177 stable inputs after verification and the table
    rendered those verdicts as `unknown`.

109. **R109 — Person and work claims MUST use role-specific, heading-matched evidence (Rule W-206).**
    A person QID MUST pass the authority-heading check before local references
    resolve it. Manuscript languages MUST NOT become person P1412 claims without
    explicit person-level evidence. A contained work MUST use its own catalogue
    attribution, and a primary MARC author MUST outrank an `(מיוחס לו)` author.
    *Why:* Run `48ba6c13` created one fuzzy scribe QID, one manuscript-language
    P1412 claim, one compiler-as-author claim, and one attributed-author merge.
110. **R110 — Judge failures MUST render as abstain and retain their error (Rule W-207 / W-211).**
    The item read model MUST expose provider and parse failures as
    `ai_verdict.overall="abstain"` with
    `verification_status="provider_error"`, `judge_failure=true`, and a
    retained `verification_error`. Legacy `unknown` and `verification_failed`
    rows MUST normalize on read and in the migration. A changed per-item stable
    fingerprint MUST drop the old verdict; a whole-run build fingerprint MUST
    not invalidate unchanged items. *Why:* Run `48ba6c13` showed nine provider
    failures after a force rebuild even though the items had no validation issues.
111. **R111 — Fingerprints MUST omit volatile metadata and keep semantic dates (Rule W-208).**
    Build and verdict SHA projections MUST remove nested transport timestamps,
    fetch times, request IDs, and audit timestamps. They MUST keep manuscript
    date data and Wikidata time claims. *Why:* rebuild metadata changed SHA
    values for unchanged items and could hide valid verdicts.

112. **R112 — Work authors MUST come from structured source evidence (Rule W-209).**
    Canonical context MUST include approved contents-NER rows before work-author
    sanitation. `WORK_AUTHOR`, content-author, MARC-author, and approved authority
    evidence may create `P50` or `P2093`. A title fragment after Hebrew ל MUST NOT
    create `P2093`. A work with a matched source record and no author evidence MUST
    drop inherited `P2093` claims. Regression: `test_hmo_canonical_wikidata.py`
    and `test_wikidata_studio_works.py`.
    *Why:* run `48ba6c13` produced eight partial works because title fragments became author strings.*
113. **R113 — Wikidata verify rows MUST preserve judge metadata and retry invalid output (Rule W-210 / W-211).**
    Live job snapshots MUST retain the envelope fields required by the review
    table. The judge MUST retry empty or malformed provider responses before it
    exposes an `abstain` provider-error row. *Why:* the live modal showed blank
    metadata and transient provider failures as incomplete verdict rows.
