# Wikidata Studio — Tests pinning this block

> Up: [Wikidata Studio](README.md)

- `backend/tests/unit/test_wikidata_export36_w176.py` — Rule W-176:
  under designation labels only the 245/P1476 title remains as an alias.
- `backend/tests/unit/test_wikidata_verdict_cache.py` — sticky-full sanitise
  across schema bumps, gloss-agnostic cache keys, fixture keeps value_label
  (Rule W-175).
- `backend/tests/unit/test_judge_failure_is_not_a_verdict.py` — provider and
  parse failures render as `abstain` with provider-error status, retain
  diagnostics, and keep verify jobs `partial` (Rule W-207 / W-211).
- `backend/tests/unit/test_judge_failure_is_not_a_verdict.py` and
  `eval-agent/tests/test_gated_retry.py` — live snapshot metadata and retries
  for invalid judge responses (Rule W-210).
- `backend/tests/unit/test_wikidata_studio_fingerprint.py` — transport metadata
  does not change build SHA, while semantic MARC dates still change it (W-208).
- `backend/tests/unit/test_wikidata_export35_w174.py` — Rule W-174:
  catalogue P973 shelfmark gate, Hebrew bracket expansion, Leeds holder
  gloss via `qid_label`, work-title alias elaboration strip.
- `backend/tests/unit/test_wikidata_export34_w173.py` — Rule W-173:
  RELATED_WORKS Bible ladder on piyyut MSS, P1574 ladder QIDs labeled
  (incl. Esther Q131068), P1922 chronology gate, Cambridge holder label,
  Hebrew preferred P8189 strip.
- `backend/tests/unit/test_wikidata_export33_w172.py` — Rule W-172:
  judge fixture keeps quantity `unit`, HE description omits shelfmark,
  scholarly P1684 gate, facsimile work description, person alias filter.
- `backend/tests/unit/test_wikidata_corpus_scale_w171.py` — Rule W-171:
  ISBD split, P1574 specificity, identity gate, leaf unit, alias hygiene,
  incipit gate, century→embedded year, sticky-full, optional TSV stream
  sample, rubric Mode-β clauses.
- `backend/tests/unit/test_wikidata_nonpassing_buckets.py` — Rule W-170 regression:
  person identifier fail-closed, same-family·different-given strip (incl. `אל`
  particle), date-gated patronymic father-ID, P1559 align/label evidence,
  HE↔EN map + leftover-token refuse (W-190), subset-verify `__LOCAL:` catalog,
  manuscript claim hygiene (person-subject P921, catalog-note P1684), weak
  editor descriptions, Cambridge catalog labels, description year precision,
  language/facsimile labels, mm dimensions, paren MARC roles → P11603,
  Amran Q172597 + bare-751 not P1071, P1476 stays MARC 245, orphan P3342 drop, quantity unit in fixture, (מוזכר) alias strip.
- `backend/tests/unit/test_wikidata_wpm_guards.py` — WPM/DS alignment: no MS P50,
  composite not codex, fragment P31 from condition notes, P1574+P1932, role map.
- `backend/tests/unit/test_item_validator.py` — every check incl. label-hygiene warnings + `TestP50OnManuscript`, `TestP7416AsQuantity`, `TestP31WrongQid`, `MISSING_P3959` ERROR, `DISCOURAGED_P31`, `LOCATION_WITHOUT_GEO_EVIDENCE`.
- `backend/tests/test_wikidata_export_quality.py` — build gate runs full validator ERROR set (incl. missing P3959 / bad P31).
- `backend/tests/test_wikidata_items_export_import.py` — override export/import round-trip and diagnostic CSV coverage (item fields, MARC context, and full AI verdict).
- `backend/tests/test_wikidata_export_quality_checker.py` — compact JSON/CSV audit reports only actionable failures, distinguishes authority-approved-only from diagnostic exports, and treats Hebrew gershayim in a title claim as content rather than wrapper noise (Rule W-76).
- `backend/tests/test_section_export_router.py` — section CSV retains item approval, verdict JSON, source records, validation, authority, and work-candidate evidence.
- `backend/tests/test_wikidata_item_views.py`, `test_wikidata_verdict_persistence.py`, and `unit/test_wikidata_verdict_cache.py` pin build `records` ↔ verify `record_ids`, MARC/local-target fingerprint parity, prompt-visible statement/work evidence, safe stale-key display, and separation of ledger QIDs from probe-adopted QIDs (W-205); `unit/test_marc_subject_resolve.py` and `test_marc_650_655_lod.py` pin verified static QIDs/labels and broad-P921 omission.
- `backend/tests/unit/test_hmo_wikidata_pq_mapper.py` — ontology/local-PID → public P/Q allowlist; bare project QID rejected; MS P50 forbidden; P1476/P1684 monolingualtext (W-196).
- `backend/tests/unit/test_hmo_canonical_wikidata.py` — canonical claim mapping uses the PQ mapper; control numbers; no local-Q leak; summarized Production/CU rollup; P2888/P973 bridge; rollup summary counts; `filter_public_wikidata_items` / stale-cache shape (Rules W-117 / W-118); label hygiene + work evidence (W-120); MARC 245 / known-QID recovery (W-121); ontology-IRI P2888 rewrite (W-122); same-identity person coalescing before the export gate (W-213).
- `backend/tests/unit/test_wikidata_work_candidates.py` — includes `245` / `100/245` accept reasons (W-121).
- `backend/tests/unit/test_property_mapping_hmo_links.py` / `test_item_builder_hmo_links.py` / `test_hmo_wikidata_pq_mapper.py` — browseable Item:Q only (W-122).
- `backend/tests/unit/test_wikidata_upload_guards.py` (~20+) — reconcile-before-create per type, fail-closed outage, validator hard gate, blocked-never-written, audit trail, ledger/adopt, dry-run truthfulness, foreign skip / accept-allow.
- `backend/tests/unit/test_wikidata_upload_qid_hydrate.py` — upload natives receive Studio-cache `existing_qid` (W-177 / R80).
- `backend/tests/unit/test_wikidata_secret_keys.py` — live vs `wikidata_test` Settings key routing (W-178 / R81).
- `backend/tests/unit/test_wikidata_auth_token.py` — bot-password normalize/validate (two-line paste).
- `backend/tests/unit/test_wikidata_upload_login_once.py` — shared uploader + auth abort (W-179 / R82).
- `backend/tests/unit/test_wikidata_upload_is_bot.py` — `is_bot` kwarg (not `bot=`), default false, no retry on bot-right / permissiondenied; write-rights preflight (W-180 / W-181).
- `backend/tests/unit/test_wikidata_upload_job_progress.py` — live progress rows include `label` / `entity_type` / `outcome_counts` + processing status shape (W-141 upload modal).
- `frontend/tests/unit/wikidataUploadProgressModal.spec.ts` — sticky/test resolve of `upload_target` (no invented live; preferred target; frontend R19).
- `frontend/tests/unit/runJobsStore.spec.ts` — active poll merges without wiping terminal upload snapshots; upsert preserves `upload_target` (frontend R19).
- `frontend/tests/unit/studioUploadProgress.spec.ts` — processing → terminal status replace; Wikidata/HMO row patches.
- `backend/tests/unit/test_studio_dict_to_native.py` — Studio-cache dict → native item (W-181 / R84).
- `backend/tests/unit/test_audit_test_wikidata_upload.py` — claim-count / live-URI / `__LOCAL:` / identity-clash helpers for the test-upload live-readiness audit (W-190 / W-191).
- `backend/tests/unit/test_judge_test_wikidata_live_ready.py` — compact test-wiki snapshot + merge (deterministic blockers beat LLM `full`; skipped ≠ live-ready; `skip_for_live` excluded from written live-ready).
- `backend/tests/unit/test_wikidata_duplicate_confirm.py` — W-195: garbage/`same_item` without id/`unsure` skip; clash never calls confirm; pass 2 created/own vs foreign; P50 `__LOCAL:` → P2093. W-196: work allowlisted P31 may keep `same_item`.
- `backend/tests/unit/test_wikidata_work_link_w196.py` — W-196: unique prayer probe, crossword reject, ambiguous pair, Tikkun skip+`link_qid`, skipped-work session QID.
- `backend/tests/unit/test_wikidata_live_native_hygiene.py` — W-194: Savoy/Shor QID clear, Tikkun work-item denylist, `__LOCAL:` rewrite/degrade/in-batch keep; W-196: string title coerce, 5180 mm omit.
- `eval-agent/tests/test_wikidata_test_live_ready.py` — evaluator pack includes test snapshot; prompt forbids copying test Q/P.
- `backend/tests/unit/test_wikidata_person_identity_w190.py` — leftover-token refuse (Savoy/Sultan/Kostlitz), Monson/Curiel/Briel cover, upload-prepare foreign clash → skip (not CREATE), own clash → blocked (W-190 / W-195).
- `backend/tests/unit/test_wikidata_upload_claim_complete_w191.py` — upload sort order, `__LOCAL:` resolve, unpartitioned leftover still blocked, `wikibase-item` P31 builds (W-191).
- `backend/tests/unit/test_wikidata_upload_claim_exists_w193.py` — quantity `+11`/`11.0` exists-match, BCE `format_wikidata_time` / `+-199` repair (W-193).
- `backend/tests/unit/test_wikidata_upload_deferred_links.py` — partition defers P50, pass-2 progress `1/2`→`2/2` steps, ETA hidden until 3 samples (W-192).
- `backend/tests/unit/test_wikidata_test_wiki_compat.py` — test.wikidata.org remap + leftover **refuse** (not drop) + ownership on test Q refs + ownership-before-adapt + SPARQL fallback + skipped-foreign `__LOCAL:` wiring + unmapped live Q leftover + quantity unit remap + illegal live-entity no-retry + no live strip + no `Bad value type` retry + adopt-on-conflict + datatype-keyed P map + MHM stub reuse + foreign-alive CREATE + holder/live gloss stubs + disambiguated P CREATE + item-write label-conflict adopt (W-182 / W-183 / W-184 / W-185 / W-186 / W-187 / W-189 / R85–R91).
- `backend/tests/unit/test_wikidata_existence.py` — batched `wbgetentities`, 429 retry, existence cache, alive parse, ownership classify, QID-bound foreign accept gate (W-185).
- `backend/tests/unit/test_wikidata_canonical_enrichment.py` — identifier recovery from evidence, upload omit + P2093 rollup (W-185), strong-ID person coalescing with local-reference rewrite, and conflicting existing-QID preservation (W-213).
- `backend/tests/unit/test_rdf_build.py` — clean raw 505 and flat contents create evidence-backed works.
- `backend/tests/unit/test_wikidata_studio_slicing.py`, `test_wikidata_studio_works.py`, `test_wikidata_manuscript_labels.py`, `test_wikidata_matcher_backfill.py` — build/serialisation behaviour. `test_wikidata_studio_works.py` also pins source evidence, folio qualifiers, embedded-author cleanup, Latin-heading rejection, non-inherited work P407, and exact per-item `records`; it also pins contents-level author fields and
approved work-QID reuse so enrichment metadata cannot be dropped; related_works
known-QID linking (Bible/Tanakh/Haggadah/Tikkun Chatzot) without evidence-less
CREATE (Rule W-114 / R41); curator-approved related works stamp evidence.
- The same work test also checks that a later source record adds a source-backed
  `P50` claim to a reused work when the first source had no author.
- It also checks that an approved work QID does not bypass that author recovery.
- `backend/tests/unit/test_wikidata_nonpassing_buckets.py` — fuzzy person QIDs
  fail closed after heading comparison, and manuscript languages do not become
  person P1412 claims (W-206).
- `backend/tests/unit/test_wikidata_studio_works.py` — a structured 500
  attribution supplies the contained-work author, excludes the compiler, and
  gives a primary MARC author precedence over an attributed author (W-206).
- `backend/tests/unit/test_hmo_canonical_wikidata.py` — canonical and merged
  work items keep the primary author and remove an attributed author (W-206).
- `backend/tests/unit/test_wikidata_studio_control_number_join.py` — quoted/whitespace control numbers join records to approved authority and NER evidence before item projection.
- `backend/tests/unit/test_wikidata_autofix_apply.py`, `test_wikidata_entity_compare.py` — AI-fix merge + live compare.
- `backend/tests/test_hmo_instance_qids_for_run.py` — HMO QID injection into the fingerprint/build, including quoted control-number normalisation.
- `backend/tests/unit/test_hmo_wikidata_projection.py`, `unit/test_property_mapping_hmo_links.py`, and `unit/test_item_builder_hmo_links.py` — exact-URI, valid-QID, conflicting-mapping, real-item URL, malformed-QID, and slug-fallback behavior at the HMO→Wikidata boundary.
- `frontend/e2e/wikidata-studio.spec.ts` — page (modern + legacy), AI verification, filters, sort, approval, force-rebuild.
- `frontend/e2e/wikidata-item-table.spec.ts` — review table columns, data status, search, approval PATCH, upload-outcome filter (incl. skip vs create), last-upload remap/skip popovers, real filter counts, Approve all visible (`wikidata_item_bulk_approve` job).
- `backend/tests/test_studio_item_bulk_approve.py` — bulk-approve params + worker for HMO/Wikidata override rows.
- `frontend/e2e/wikidata-item-drawer.spec.ts` — drawer apply-fix, push, reconcile API shapes.
- `frontend/e2e/wikidata-upload-panel.spec.ts` — compatibility-only dry-run/test radios, test full-claim remap hint (W-186), `upload_target=test` job params, pill, pre-verify fail confirm gate, live **processing…** pill while a historic row is under write, and live two-step progress UI (modal + job tray `WikidataUploadSteps`).
- `frontend/tests/unit/runJobsHref.spec.ts` — modal job kinds append `?job=` (verify + upload, Rule W-141).
- `frontend/tests/unit/wikidataUploadOutcomes.spec.ts` — upload outcome tally + terminal row selection for the progress modal.
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
  claim merge for manuscripts/persons/works and same-identity coalescing
  before export (W-125 / W-213).
- `backend/tests/unit/test_verify_outcome.py` — incomplete verify →
  `outcome=partial` + TRACE/checkpoint merge (W-126 / R52); missing
  `runner.exit` synthesize + throttle (W-127 / R53).
- `backend/tests/test_wikidata_item_views.py` — merged read model drops HMO-class
  `entity_type` rows from canonical cache (W-118).
- `backend/tests/test_wikidata_items_export_import.py` — JSON export excludes
  non-public entity types (W-118).
- `backend/tests/unit/test_wikidata_studio_build_job.py` — build worker passes
  `reconcile=False` (W-119 / R46).
- `backend/tests/unit/test_wikidata_studio_build_gate.py` — the build snapshots
  MARC before transliteration cache work and uses bulk cache sessions (W-197).
- `backend/tests/unit/test_wikidata_studio_list_view.py` — `list_view` trim,
  lean fixture + compact cached-verdict candidates (W-131).
- `backend/tests/unit/test_wikidata_verify_heap.py` — scoped MARC + heap release (W-132).
- `backend/tests/unit/test_wikidata_persist_batch.py` — non-blocking persist flush (W-133).

Any new external-write path or reconcile change MUST extend
`test_wikidata_upload_guards.py` (Rule W-30).

- `backend/tests/unit/test_publication_module.py`,
  `backend/tests/test_publication_repository.py`, and
  `backend/tests/unit/test_wikidata_publication_gateway.py` — sealed Release
  digests, execution leases, recovery, target credentials, revision checks,
  and fail-closed gateway outcomes (Rule W-212).
- `backend/tests/test_publication_router.py` and
  `backend/tests/test_wikidata_publication_execution_job.py` — queued prepare
  and execution jobs, access checks, no HTTP write, and secret-free job data.
- `frontend/e2e/wikidata-publication.spec.ts` — one default Publication target
  selector, no concurrent legacy controls, Review → Dry-run → Publish, and
  bounded audit cursor flow.


- `backend/tests/test_wikidata_items_export_import.py` — diagnostic CSV columns include authority evidence and local-reference target JSON.
- `eval-agent/tests/test_wikidata_item.py` — evaluator payload/prompt carries authority, internal-reference, and contents/catalog context.


- `backend/tests/unit/test_wikidata_builder_modules.py` — public builder API retains the shared models and all extracted projection methods.

- `backend/tests/unit/test_wikidata_work_candidates.py` — source-aware 500/505 decisions, catalogue-prose rejection, authority overrides, and sanitation.
- `backend/tests/unit/test_wikidata_studio_fingerprint.py` — MARC JSON changes invalidate the durable Studio build cache.
- `backend/tests/unit/test_wikidata_phase1_projection.py` — Phase 1 evidence gates for P136/P31, canonical-holder P195, verified Masorah P921, MARC-100 work chains, facsimile typing, P1684, P127, and descriptions.
- `backend/tests/unit/test_hebrew_date_parse.py` — Hebrew geresh/gershayim century parsing, BCE production-year boundaries, and mixed century/year fallback; malformed date tokens must not abort a record.
- `backend/tests/unit/test_item_validator.py` — legitimate internal Hebrew gershayim are not reported as LABEL_QUOTE_NOISE.
- `backend/scripts/audit_marc_tsv_scale.py` — streaming full-corpus normalization plus bounded deterministic builder smoke test.

- `backend/tests/unit/test_wikidata_studio_source_cache.py` — legacy/canonical cache lookup isolation for shadow builds.
- `backend/tests/unit/test_wikidata_studio_cache.py` — pre-notability
  `NO_IDENTIFIER` rows are stale while identifier-bearing person rows remain
  current (Rule W-153 / R69).
- `backend/tests/unit/test_wikidata_canonical_enrichment.py` — canonical merge
  recovers trusted VIAF/NLI before omit, then drops remaining identifierless
  persons (Rules W-154 / W-188 / R70 / R90).
- `backend/tests/unit/test_hmo_canonical_wikidata.py` — final canonical
  assembly recovers evidence IDs before the W-154 drop; name-only people
  still omitted (Rules W-154 / W-188 / R70 / R90).
- `backend/tests/unit/test_hmo_canonical_wikidata.py` — canonical HMO claim filtering, fingerprints, and native Wikidata projection.
- `backend/tests/unit/test_hmo_canonical_wikidata.py` — post-filter local-reference resolution, authority-date conflict omission, `(MS …)` person-label cleanup, broad-subject removal, and work-evidence source-record provenance (Rule W-155).
- `backend/tests/unit/test_wikidata_verdict_cache.py` — slim-persist ↔ full-item fingerprint parity, evidence-free verdict survival, and subset-verify evidence drift retention (Rules W-136 / R64).
- `backend/tests/unit/test_wikidata_verify_scope_cache.py` — verify scope uses curator override-merged items before fingerprinting (R65).
- `backend/tests/test_wikidata_item_views.py` — merged view validates verdicts against the retained pre-derived override projection (R66); applies the persist-slim projection before stable-key validation (R67); retains subset verdicts when local-target labels differ by scope (R68); retains verdicts after probe QID adoption and live value-label gloss parity with verify (R72 / W-169).
- `backend/tests/unit/test_wikidata_evidence_and_identity.py` — raw-tag evidence slice, per-claim provenance, manuscript identity scoping, claim dedup, identity gates (Rule W-137).
- `backend/tests/unit/test_wikidata_description_hygiene.py` — generated manuscript descriptions, catalog-note rejection, description language routing, and Hebrew description separation from shelfmark labels (Rule W-137 / W-172).
- `backend/tests/unit/test_wikidata_wave2_projection.py` — MARC unwrapping, dimension parsing, channel-aware provenance, work-title identity, local-reference resolution, generic subjects, verified holders (Rule W-138). Hygiene omits P2048/P2049 above 1000 mm (W-196).
- `backend/tests/unit/test_wikidata_duplicate_probe.py` — identifier probes, batch attribution, throttle/fail-closed statuses, evidence-pack exposure, and linked-QID priority over stale duplicate warnings (Rules W-139 / W-199).
- `backend/tests/unit/test_marc_extent_and_digital_access.py` — extent summation, gematria/page units, fail-closed cases, 856$u → P953, closed-vocabulary material, Hebrew description language agreement (Rule W-140).
- `backend/tests/unit/test_marc_llm_extract.py` — span grounding (a hallucinated span cannot pass), closed material vocabulary, unavailable-vs-empty, budget reporting, advisory-only surfacing, Qubrid request shape (Rule W-140).
- `backend/tests/unit/test_wikidata_projection_recovery.py` — contained-work relinking, multi-valued P973, facsimile typing, Hebrew label holder/language (Rule W-142).
- `backend/tests/unit/test_wikidata_studio_build_job.py` — `TestMiningReadsMarcProse`: the mining phase loads run MARC for the manuscripts in the build and never loads it for other entity types (Rule W-140).
- `backend/tests/unit/test_marc_llm_extract.py` — `TestPromptNamesEachProperty` (every PID is explained; a language is not a place) and `TestNoSourceIsReported` (a prose-free manuscript reports `no_source`, not silence) (Rule W-140).
- `backend/tests/unit/test_wikidata_phase1_projection.py` — `TestAuditedHolderTable`: a table institution resolves to its verified QID, the table beats an unverified authority QID, an external authority QID without a table entry abstains, and an ambiguous institution abstains (Rule W-143 / W-174).
- `backend/tests/unit/test_wikidata_duplicate_probe.py` — `TestHolderPlusShelfmarkKey` (AND not OR; abstained holder and two shelfmarks yield no key), `TestWorkTitleProbe` (title+class, curator confirmation required, manuscripts excluded), `TestAbsentMeansEveryKeyAnswered`, `TestCachedAnswerIsVisibleWithoutProbing`, and linked-QID priority over stale duplicate warnings (Rules W-144 / W-145 / W-199).
- `backend/tests/unit/test_wikidata_lod_linking.py` — approved former-owner / signatory / mentioned rows become edges; a former owner is never a current `P127`; seller and censor stay refused; no item is created for the sake of an edge; the canonical context stamps `marc_authority_matches` in the desktop shape (Rule W-146).
- `backend/tests/test_wikidata_export_quality_checker.py` — `TestDuplicateCoverageChecks`: unprobed work, missing holder+shelfmark probe, top-level candidate blocks, linked QIDs do not create duplicate blockers, and `P2093` flagged only when an author item exists (Rules W-144 / W-145 / W-146 / W-199).
- `backend/tests/unit/test_wikidata_studio_works.py` — exact known work QIDs do not create authorless local works; equal titles with different verified authors stay separate (W-200).
- `backend/tests/unit/test_hmo_canonical_wikidata.py` — canonical works carry an approved MARC author as `P50` or `P2093` (W-201).
- `backend/tests/unit/test_hmo_canonical_wikidata.py` — canonical work sanitation drops unsupported title-fragment `P2093` claims and recovers approved contents-NER authors (W-209).
- `backend/tests/unit/test_wikidata_studio_works.py` — a Hebrew ל-preposition in a contents title does not create a `P2093` claim (W-209).
- `backend/tests/unit/test_wikidata_studio_works.py` — approved MARC author matches recover the main work author when the raw author list is empty (W-202).
- `backend/tests/unit/test_wikidata_canonical_enrichment.py` — matched legacy person IDs are rewritten in work claims and metadata before local-reference resolution (W-203).
- `backend/tests/unit/test_wikidata_canonical_enrichment.py` — safe work `P50` claims replace duplicate `P2093` fallbacks, while unresolved `P50` claims preserve the `P2093` fallback (W-204).
- `backend/tests/test_wikidata_export_quality.py` — source-backed works without `P50` or `P2093` fail the build gate (W-200).

- `backend/tests/test_publication_router.py` checks entity pages after approval and rejection, consent through the worker, and QID/revision/digest mismatches.
- `frontend/e2e/wikidata-publication.spec.ts` checks explicit consent, the QID link, the submitted command, and selection reset.
