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
12. **R12 — Live uploads use the curator's own encrypted Wikidata token**
    (Settings → `_unwrap_user_secret`), never a shared credential. Both
    `POST /jobs` (`wikidata_upload`) and `POST …/wikidata-studio/upload`
    enqueue only — never run `upload_items` on the request path (Rule W-107).
    *Why:* attribution / authorship plus Heroku H12 on sequential writes.
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
Live Wikidata upload streams `item_outcome` / `recent_item_outcomes`; the panel
patches matching rows via `studioUploadProgress` (parity with HMO R51 /
frontend R14 / Rule W-110). Bulk-approve and verify may silent-reload
(throttled) but MUST keep the table mounted when items are already loaded.
Studio **build** still refreshes only at finish (cache written at end).
*Why:* mid-run `setLoading(true)` unmounted the table and flickered Publication
status during publish.

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