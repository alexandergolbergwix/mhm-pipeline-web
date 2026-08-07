# Wikidata Studio — Tests pinning this block

> Up: [Wikidata Studio](README.md)

- `backend/tests/unit/test_wikidata_nonpassing_buckets.py` — Rule W-170 regression:
  person identifier fail-closed, same-family·different-given strip (incl. `אל`
  particle), date-gated patronymic father-ID, P1559 align/label evidence,
  HE↔EN map + refuse-and-strip adoption, subset-verify `__LOCAL:` catalog,
  manuscript claim hygiene (person-subject P921, catalog-note P1684), weak
  editor descriptions, Cambridge catalog labels, description year precision,
  language/facsimile labels, mm dimensions.
- `backend/tests/unit/test_wikidata_wpm_guards.py` — WPM/DS alignment: no MS P50,
  composite not codex, fragment P31 from condition notes, P1574+P1932, role map.
- `backend/tests/unit/test_item_validator.py` — every check incl. label-hygiene warnings + `TestP50OnManuscript`, `TestP7416AsQuantity`, `TestP31WrongQid`, `MISSING_P3959` ERROR, `DISCOURAGED_P31`, `LOCATION_WITHOUT_GEO_EVIDENCE`.
- `backend/tests/test_wikidata_export_quality.py` — build gate runs full validator ERROR set (incl. missing P3959 / bad P31).
- `backend/tests/test_wikidata_items_export_import.py` — override export/import round-trip and diagnostic CSV coverage (item fields, MARC context, and full AI verdict).
- `backend/tests/test_wikidata_export_quality_checker.py` — compact JSON/CSV audit reports only actionable failures, distinguishes authority-approved-only from diagnostic exports, and treats Hebrew gershayim in a title claim as content rather than wrapper noise (Rule W-76).
- `backend/tests/test_section_export_router.py` — section CSV retains item approval, verdict JSON, source records, validation, authority, and work-candidate evidence.
- `backend/tests/test_wikidata_item_views.py`, `test_wikidata_verdict_persistence.py`, and `unit/test_wikidata_verdict_cache.py` pin build `records` ↔ verify `record_ids`, MARC/local-target fingerprint parity, prompt-visible statement/work evidence, and safe stale-key display; `unit/test_marc_subject_resolve.py` and `test_marc_650_655_lod.py` pin verified static QIDs/labels and broad-P921 omission.
- `backend/tests/unit/test_hmo_wikidata_pq_mapper.py` — ontology/local-PID → public P/Q allowlist; bare project QID rejected; MS P50 forbidden.
- `backend/tests/unit/test_hmo_canonical_wikidata.py` — canonical claim mapping uses the PQ mapper; control numbers; no local-Q leak; summarized Production/CU rollup; P2888/P973 bridge; rollup summary counts; `filter_public_wikidata_items` / stale-cache shape (Rules W-117 / W-118); label hygiene + work evidence (W-120); MARC 245 / known-QID recovery (W-121); ontology-IRI P2888 rewrite (W-122).
- `backend/tests/unit/test_wikidata_work_candidates.py` — includes `245` / `100/245` accept reasons (W-121).
- `backend/tests/unit/test_property_mapping_hmo_links.py` / `test_item_builder_hmo_links.py` / `test_hmo_wikidata_pq_mapper.py` — browseable Item:Q only (W-122).
- `backend/tests/unit/test_wikidata_upload_guards.py` (~20+) — reconcile-before-create per type, fail-closed outage, validator hard gate, blocked-never-written, audit trail, ledger/adopt, dry-run truthfulness, foreign skip / accept-allow.
- `backend/tests/unit/test_wikidata_existence.py` — `wbgetentities` alive parse, ownership classify, QID-bound foreign accept gate.
- `backend/tests/unit/test_rdf_build.py` — clean raw 505 and flat contents create evidence-backed works.
- `backend/tests/unit/test_wikidata_studio_slicing.py`, `test_wikidata_studio_works.py`, `test_wikidata_manuscript_labels.py`, `test_wikidata_matcher_backfill.py` — build/serialisation behaviour. `test_wikidata_studio_works.py` also pins source evidence, folio qualifiers, embedded-author cleanup, Latin-heading rejection, non-inherited work P407, and exact per-item `records`; it also pins contents-level author fields and
approved work-QID reuse so enrichment metadata cannot be dropped; related_works
known-QID linking (Bible/Tanakh/Haggadah/Tikkun Chatzot) without evidence-less
CREATE (Rule W-114 / R41); curator-approved related works stamp evidence.
- `backend/tests/unit/test_wikidata_studio_control_number_join.py` — quoted/whitespace control numbers join records to approved authority and NER evidence before item projection.
- `backend/tests/unit/test_wikidata_autofix_apply.py`, `test_wikidata_entity_compare.py` — AI-fix merge + live compare.
- `backend/tests/test_hmo_instance_qids_for_run.py` — HMO QID injection into the fingerprint/build, including quoted control-number normalisation.
- `backend/tests/unit/test_hmo_wikidata_projection.py`, `unit/test_property_mapping_hmo_links.py`, and `unit/test_item_builder_hmo_links.py` — exact-URI, valid-QID, conflicting-mapping, real-item URL, malformed-QID, and slug-fallback behavior at the HMO→Wikidata boundary.
- `frontend/e2e/wikidata-studio.spec.ts` — page (modern + legacy), AI verification, filters, sort, approval, force-rebuild.
- `frontend/e2e/wikidata-item-table.spec.ts` — review table columns, data status, search, approval PATCH, upload-outcome filter, real filter counts, Approve all visible (`wikidata_item_bulk_approve` job).
- `backend/tests/test_studio_item_bulk_approve.py` — bulk-approve params + worker for HMO/Wikidata override rows.
- `frontend/e2e/wikidata-item-drawer.spec.ts` — drawer apply-fix, push, reconcile API shapes.
- `frontend/e2e/wikidata-upload-panel.spec.ts` — upload target radios (default dry-run), pill, pre-verify fail confirm gate.
- `frontend/tests/unit/useVerifyJob.spec.ts` — verify jobs upsert into the global job tray on start and reset the modal state when enqueue rejects.
- `backend/tests/test_run_job_params_wikidata_verify.py` — enqueue skips scope
  build (W-59); Gemini key only for Gemini tiers (W-60); worker passes Studio
  `source`/`approved_only` into `_fetch_wikidata_verify_items` (W-115 / R42).
- `backend/tests/unit/test_wikidata_verify_scope_cache.py` — verify prefers
  existing Studio cache and rebuilds with `reconcile=False` (W-116 / R43); skips
  non-public `entity_type` rows (W-117 / R44); quoted CN join + `verify_evidence`
  pack (W-124 / R50).
- `backend/tests/unit/test_wikidata_verify_evidence.py` — multi-channel evidence
  pack partitioning (W-124).
- `backend/tests/unit/test_wikidata_canonical_enrichment.py` — legacy→canonical
  claim merge for manuscripts/persons/works (W-125).
- `backend/tests/unit/test_verify_outcome.py` — incomplete verify →
  `outcome=partial` + TRACE/checkpoint merge (W-126 / R52); missing
  `runner.exit` synthesize + throttle (W-127 / R53).
- `backend/tests/test_wikidata_item_views.py` — merged read model drops HMO-class
  `entity_type` rows from canonical cache (W-118).
- `backend/tests/test_wikidata_items_export_import.py` — JSON export excludes
  non-public entity types (W-118).
- `backend/tests/unit/test_wikidata_studio_build_job.py` — build worker passes
  `reconcile=False` (W-119 / R46).
- `backend/tests/unit/test_wikidata_studio_list_view.py` — `list_view` trim,
  lean fixture + compact cached-verdict candidates (W-131).
- `backend/tests/unit/test_wikidata_verify_heap.py` — scoped MARC + heap release (W-132).
- `backend/tests/unit/test_wikidata_persist_batch.py` — non-blocking persist flush (W-133).

Any new external-write path or reconcile change MUST extend
`test_wikidata_upload_guards.py` (Rule W-30).


- `backend/tests/test_wikidata_items_export_import.py` — diagnostic CSV columns include authority evidence and local-reference target JSON.
- `eval-agent/tests/test_wikidata_item.py` — evaluator payload/prompt carries authority, internal-reference, and contents/catalog context.


- `backend/tests/unit/test_wikidata_builder_modules.py` — public builder API retains the shared models and all extracted projection methods.

- `backend/tests/unit/test_wikidata_work_candidates.py` — source-aware 500/505 decisions, catalogue-prose rejection, authority overrides, and sanitation.
- `backend/tests/unit/test_wikidata_studio_fingerprint.py` — MARC JSON changes invalidate the durable Studio build cache.
- `backend/tests/unit/test_wikidata_phase1_projection.py` — Phase 1 evidence gates for P136/P31, canonical-holder P195, verified Masorah P921, MARC-100 work chains, facsimile typing, P1684, P127, and descriptions.
- `backend/tests/unit/test_hebrew_date_parse.py` — Hebrew geresh/gershayim century parsing and mixed century/year fallback; malformed date tokens must not abort a record.
- `backend/tests/unit/test_item_validator.py` — legitimate internal Hebrew gershayim are not reported as LABEL_QUOTE_NOISE.
- `backend/scripts/audit_marc_tsv_scale.py` — streaming full-corpus normalization plus bounded deterministic builder smoke test.

- `backend/tests/unit/test_wikidata_studio_source_cache.py` — legacy/canonical cache lookup isolation for shadow builds.
- `backend/tests/unit/test_wikidata_studio_cache.py` — pre-notability
  `NO_IDENTIFIER` rows are stale while identifier-bearing person rows remain
  current (Rule W-153 / R69).
- `backend/tests/unit/test_wikidata_canonical_enrichment.py` — canonical merge
  drops identifierless persons after matching and before cache persistence
  (Rule W-154 / R70).
- `backend/tests/unit/test_hmo_canonical_wikidata.py` — final canonical
  assembly drops identifierless persons before validation/cache persistence
  (Rule W-154 / R70).
- `backend/tests/unit/test_hmo_canonical_wikidata.py` — canonical HMO claim filtering, fingerprints, and native Wikidata projection.
- `backend/tests/unit/test_hmo_canonical_wikidata.py` — post-filter local-reference resolution, authority-date conflict omission, `(MS …)` person-label cleanup, broad-subject removal, and work-evidence source-record provenance (Rule W-155).
- `backend/tests/unit/test_wikidata_verdict_cache.py` — slim-persist ↔ full-item fingerprint parity, evidence-free verdict survival, and subset-verify evidence drift retention (Rules W-136 / R64).
- `backend/tests/unit/test_wikidata_verify_scope_cache.py` — verify scope uses curator override-merged items before fingerprinting (R65).
- `backend/tests/test_wikidata_item_views.py` — merged view validates verdicts against the retained pre-derived override projection (R66); applies the persist-slim projection before stable-key validation (R67); retains subset verdicts when local-target labels differ by scope (R68); retains verdicts after probe QID adoption and live value-label gloss parity with verify (R72 / W-169).
- `backend/tests/unit/test_wikidata_evidence_and_identity.py` — raw-tag evidence slice, per-claim provenance, manuscript identity scoping, claim dedup, identity gates (Rule W-137).
- `backend/tests/unit/test_wikidata_description_hygiene.py` — generated manuscript descriptions, catalog-note rejection, description language routing (Rule W-137).
- `backend/tests/unit/test_wikidata_wave2_projection.py` — MARC unwrapping, dimension parsing, channel-aware provenance, work-title identity, local-reference resolution, generic subjects, verified holders (Rule W-138).
- `backend/tests/unit/test_wikidata_duplicate_probe.py` — identifier probes, batch attribution, throttle/fail-closed statuses, evidence-pack exposure (Rule W-139).
- `backend/tests/unit/test_marc_extent_and_digital_access.py` — extent summation, gematria/page units, fail-closed cases, 856$u → P953, closed-vocabulary material, Hebrew description language agreement (Rule W-140).
- `backend/tests/unit/test_marc_llm_extract.py` — span grounding (a hallucinated span cannot pass), closed material vocabulary, unavailable-vs-empty, budget reporting, advisory-only surfacing, Qubrid request shape (Rule W-140).
- `backend/tests/unit/test_wikidata_projection_recovery.py` — contained-work relinking, multi-valued P973, facsimile typing, Hebrew label holder/language (Rule W-142).
- `backend/tests/unit/test_wikidata_studio_build_job.py` — `TestMiningReadsMarcProse`: the mining phase loads run MARC for the manuscripts in the build and never loads it for other entity types (Rule W-140).
- `backend/tests/unit/test_marc_llm_extract.py` — `TestPromptNamesEachProperty` (every PID is explained; a language is not a place) and `TestNoSourceIsReported` (a prose-free manuscript reports `no_source`, not silence) (Rule W-140).
- `backend/tests/unit/test_wikidata_phase1_projection.py` — `TestAuditedHolderTable`: a table institution resolves to its verified QID, the table beats an unverified authority QID, an ambiguous institution abstains (Rule W-143).
- `backend/tests/unit/test_wikidata_duplicate_probe.py` — `TestHolderPlusShelfmarkKey` (AND not OR; abstained holder and two shelfmarks yield no key), `TestWorkTitleProbe` (title+class, curator confirmation required, manuscripts excluded), `TestAbsentMeansEveryKeyAnswered`, `TestCachedAnswerIsVisibleWithoutProbing` (Rules W-144 / W-145).
- `backend/tests/unit/test_wikidata_lod_linking.py` — approved former-owner / signatory / mentioned rows become edges; a former owner is never a current `P127`; seller and censor stay refused; no item is created for the sake of an edge; the canonical context stamps `marc_authority_matches` in the desktop shape (Rule W-146).
- `backend/tests/test_wikidata_export_quality_checker.py` — `TestDuplicateCoverageChecks`: unprobed work, missing holder+shelfmark probe, top-level candidate blocks, and `P2093` flagged only when an author item exists (Rules W-144 / W-145 / W-146).
