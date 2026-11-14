<!--
This file is intentionally kept as a bridge for repo-local, web-specific
notes. The shared rules live upstream in /Users/alexandergo/Documents/Doctorat/pipeline.
Read AGENTS.md first, then use the upstream CLAUDE.md for common policy.
-->

# MHM Pipeline Web - Operating Manual

This repo inherits the shared agent rules, command docs, and skills from the
desktop pipeline repository at `/Users/alexandergo/Documents/Doctorat/pipeline`.

Use that repository as the upstream source of truth for shared behavior. Keep
this file for web-only notes and for any overrides that explicitly mention
`mhm-pipeline-web`.

## Startup

1. Read `/Users/alexandergo/Documents/Doctorat/pipeline/AGENTS.md`.
2. Read `/Users/alexandergo/Documents/Doctorat/pipeline/CLAUDE.md`.
3. Read the web-local docs in `docs/` and the repo history as needed.

## Web-specific inheritance note

The local `.claude/` and `.codex/` directories are maintained as a thin web
layer. When a shared task, workflow, or rule already exists in the pipeline
repo, prefer the upstream version unless this repo adds an explicit web-only
override.

## Architectural rules (W-1…W-332)

Every rule lives in a topic file under
[docs/architecture/rules/](docs/architecture/rules/). **Read the file for the
area you are touching before you change it** — do not work from this index
alone; the one-line summaries are pointers, not the invariant.

### [ai-verify.md](docs/architecture/rules/ai-verify.md) — AI verification (verify streams, judges, verdict caches)

- **W-17** — NER verdicts persist to ExtractionApproval.ai_verdict
- **W-18** — Stage-2 verify state_dir per-RUN (matches Rule W-14)
- **W-33** (verify state) — Verify state on Heroku is writable under `/tmp`
- **W-44** — Large-scope AI verify MUST stream verdicts live via job + TRACE
- **W-46** — Tier-1 judge model registry + Qubrid Kimi path
- **W-47** — HMO schema AI verify must show ontology context to the judge
- **W-48** — HMO item AI verify needs manuscript scope + substantive descriptions
- **W-50** — HMO verify label hygiene + multi-CN MARC merge
- **W-51** — AI verdict caches are content-addressed everywhere
- **W-54** — Canonical control-number join for AI verify
- **W-58** — Wikidata AI verdict cache keys canonicalize build and verify record identities
- **W-60** — Verify workers use provider-aware tier-1 credentials
- **W-62** — Wikidata diagnostic exports MUST preserve verdict and prompt evidence
- **W-63** — Wikidata verification MUST use the item source records, never an arbitrary run record
- **W-71** — Verdict evidence, cache keys, and static QIDs MUST share one verified contract
- **W-79** — Verification context MUST distinguish semantic subtypes
- **W-80** — Diagnostic display labels MUST be complete
- **W-104** — Studio AI verify MUST inject WikiProject Manuscripts skill context
- **W-115** — Wikidata AI verify MUST use the same Studio source as the review table
- **W-116** — Wikidata AI verify MUST NOT SPARQL-reconcile the Studio corpus
- **W-124** — Wikidata AI verify MUST receive all evidence channels + WPM Data Model
- **W-126** — Incomplete AI verify MUST report partial and keep TRACE verdicts
- **W-127** — Large-scope verify MUST stay alive under dyno pressure
- **W-128** — Verify job polls MUST stay light on the web dyno
- **W-130** — Interrupted AI verify MUST be Continuable from cached verdicts
- **W-131** — Studio list payloads and verify heaps MUST stay Basic-dyno-safe
- **W-132** — Wikidata verify MUST scope MARC and release in-memory Studio payloads
- **W-133** — Wikidata verify persist MUST NOT block the eval-agent stdout reader
- **W-134** — Interrupted verify jobs MUST auto-resume on the backend
- **W-135** — Verify judge throughput MUST use safe parallelism
- **W-136** — Verdict fingerprints MUST be invariant under verify-heap slimming
- **W-156** — The judge's evidence projection MUST NOT be the fingerprint projection
- **W-157** — A verdict judged without a conclusive duplicate answer MUST be re-judged
- **W-158** — A judge failure MUST NOT persist as a substantive verdict, nor be cached
- **W-258** — A verify pre-spawn failure MUST surface its real cause; missing provider keys must not read as "Verification complete"
- **W-259** — Job sub-progress must never regress and every long phase must move or show an ETA: the re-enrich sweep counted visited entities, hit the full bar, then the match phase restarted at 0 (bar jumped 5295 → 323, 2026-09-24); later the serial DB-apply sat frozen at "5295/5295" for its whole duration because it emitted nothing. Numerators count only finished work (sweep = `skipped_fresh`, match = `skipped_fresh + matched_done`), and every long phase appends a per-phase ETA (`~14 min left`) via monotonic start stamps + `_estimate_remaining` — message text through the existing `sub_message` channel, no payload change — see [jobs-and-progress.md](docs/architecture/rules/jobs-and-progress.md)

### [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md) — Wikidata Studio (public projection + write path)

- **W-26** (build cache) — Wikidata Studio build result is cached in Postgres
- **W-26** (P/Q audit — number reused) — Every Wikidata P/Q constant in property_mapping.py must be verified live
- **W-27** — Wikidata Studio curator controls
- **W-30** — Wikidata upload is fail-closed: reconcile-before-create + validator gate IN the write path
- **W-57** — Wikidata Studio HMO-parity surface + write-path ledger
- **W-65** — Wikidata projections MUST expose clean labels and verifier evidence
- **W-66** — Studio build joins MUST canonicalise all MARC control-number inputs
- **W-67** — Wikidata projections MUST fail closed on unsupported semantic claims
- **W-68** — Wikidata work projection MUST be source-aware, not authority-only
- **W-69** — Work identity, author evidence, and export fields MUST remain complete
- **W-70** — Work projection MUST consume enriched content metadata
- **W-72** — Public Wikidata semantic claims MUST be evidence-gated
- **W-73** — Illustrated genre MUST NOT imply illuminated manuscript
- **W-75** — P195 MUST NOT default to the NLI collection
- **W-76** — Hebrew gershayim MUST NOT trigger quote-noise warnings
- **W-77** — Explicit catalog semantics MUST survive projection
- **W-78** — Exported authority and work identity MUST stay precise
- **W-82** — Manuscript labels MUST reflect the physical holder
- **W-98** — Wikidata projection MUST follow the WikiProject Manuscripts data model fail-closed
- **W-99** — Wikidata writes require an existence check; creator-only modification supersedes the former accept exception (W-324)
- **W-100** — Project Wikibase P/Q MUST map to public Wikidata via ontology, never by ID identity
- **W-103** — Wikidata upload target is curator-chosen; default dry-run
- **W-114** — Related works MUST NOT mint evidence-less Wikidata CREATE items
- **W-117** — Wikidata Studio emits only WPM public items; summarized HMO nodes roll up
- **W-118** — Wikidata Studio read paths MUST filter HMO ontology rows from stale cache
- **W-119** — Wikidata Studio build jobs MUST NOT WDQS-reconcile the corpus
- **W-120** — Canonical Wikidata labels and work evidence MUST match legacy hygiene
- **W-121** — Canonical CREATE works MUST recover MARC 245 / known-QID evidence
- **W-122** — Wikidata→Wikibase bridges MUST be browseable Item:Q URLs
- **W-125** — Canonical Wikidata Studio MUST merge full MARC/authority enrichment
- **W-137** — Verify evidence must be complete and manuscript identity must be its own
- **W-138** — MARC values reach the handlers unwrapped; every claim names its channel
- **W-139** — AI verify asks Wikidata whether the item already exists (duplicate probe)
- **W-142** — Projection must not lose what the build already knows
- **W-140** — Manuscript metadata MUST be recovered from MARC before it is generated
- **W-143** — Holding institutions resolve through one audited table
- **W-144** — `absent` MUST mean every duplicate key was probed, and be visible
- **W-145** — Works MUST be probed for duplicates
- **W-146** — Created items MUST be reachable from one another
- **W-159** — The duplicate answer has exactly one writer
- **W-160** — A capped probe MUST be distinguishable from an unattempted one
- **W-161** — A holder name is either audited or the build fails; labels never default to NLI
- **W-262** — The export quality gate must see audited holders, distinct catalog ids on a real shared shelfmark, and a record-backed work author (run 3494ebf5 job 89a2decf, 317 findings) — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-264** — Work candidates MUST NOT be folio-range or person-name strings: a quoted 500 folio parenthetical (`([147]א-[154]א)`) passed the digit-counting length check and a translator genealogy chain was accepted as a work title; `assess_work_candidate` now returns `folio_range_title` (<3 letters plus a digit) and `person_name_not_work` (≥2 standalone `בן`/`בר`/`ב"ר` connectors, no work-noun head, 500 branch only — curator-approved/known-QID/505/245 paths untouched) (run 3494ebf5: 1 partial + 1 fail of 183 judged works, re-assessment rejected exactly 2 of 1,151 accepted candidates) — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-267** — A selected-scope rebuild MUST be a bounded patch that recomputes: `POST /wikidata-studio/rebuild-selected` rebuilds only the records behind the chosen items (canonical subset + legacy slice, merged into cache + rows by `local_id`, shared ids replaced), always recomputes (skip-cache inherent), caps at 200 items, and a full rebuild supersedes it because corpus-wide passes ran only over the subset — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-296** — Source-qualified scribes, authors, content sections, dates, physical form, and references survive the canonical merge; Gila audit exports remain local proposals until live correction receipts exist
- **W-300** — Colophon `קולופון:` field labels (957/590/500) are stripped at ingest before the W-72 apparatus filter so colophon year/Hebrew year/scribe survive; the record-author fallback covers only the main 245 work or the record's sole content work, keeping same-title works with different record authors separate (schemas `source-aware-works-v14` / `hmo-wikidata-v17`) — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-268** — The fast review-table row path MUST replay the verdict staleness contract: `page_wikidata_items` merged overrides + verdicts but skipped the W-169 SHA check the merged view applies, so after the run 3494ebf5 partial-scope rebuild 446 stale partials kept showing (only the 131 new works read unverified); the row path now replays adoption + local targets + live labels + `_sanitise_merged_verdicts` per page — SHA mismatch reads unknown, fresh verdicts keep — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-269** — Manuscript designation labels are the accepted identity for `name_ok`: `כתב יד עברי, {holder}, {shelfmark}` (optionally the NLI control number) is the deliberate W-82/W-161 designation, not a weakened label; the `name_ok` brief now carries the exception (judge holder+shelfmark substance, not name-likeness), and the verdict key bumps to `records_marc_v8` so old partials re-judge. Also fail-closed: collection-level titles ("Bible"/"Tanakh") no longer map to work QIDs (run 3494ebf5: a kabbalistic compilation mis-linked to Q1845) — see [ai-verify.md](docs/architecture/rules/ai-verify.md)
- **W-265** — Claim support uncertainty MUST NOT cap a verified claim-set at partial: wikidata_item claim checks treated noul in [0.4, 0.7) as a defect and dragged gold-full works with bridge claims (P2888/P973/P217) to partial; `overall_from_answers` now caps only on an actually-unsupported claim (`fail`), and the `type_ok` prompt defers span length to `name_ok` (single given name / surname-only span is still a person) — closes both known over-strict classes from shadow cycle 1. The prompt+gate change bumps the verdict key version to `records_marc_v7` (Rule W-71: keys cover everything the verdict depends on) — partial/fail re-judge on next verify, full sticks via W-171; a rebuild alone never changes verdict counts — see [ai-verify.md](docs/architecture/rules/ai-verify.md)
- **W-266** — The Studio export MUST serve the canonical build's cache with canonical input shapes: the section export recomputed its fingerprint WITHOUT `hmo_instance_qids` (never matching the canonical row) and rebuilt with hand-rolled legacy shapes (no `entity_kind`/`field` on matches, no `start`/`end` on NER entities), silently dropping all 105 persons, 345 works, and the HMO P2888/P973 links from run 3494ebf5's export (1406 vs 1856 items); the export now uses `compute_build_fingerprint_streamed` + `match_to_build_payload` + `_group_entity_rows` + `hmo_instance_qids`, and the quality checker gains 3 LOD gates (`person_without_external_identifier`, `manuscript_missing_catalog_id` blocking; `work_without_external_entity_link` informational) — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-270** — Foreign-block resolution retains reconcile methods and a curator drawer; the P3959 ownership override and automatic update are superseded by W-302 — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-271** — A publication worker must survive its own infrastructure: gateway calls have time budgets; a write timeout is outcome-unknown, not a blind retry. Execution workers retry dead DB connections on a fresh session (bounded); Modal health checks name missing keys and a 401 logs remediation — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-272** — Stale `absent` plans must not create duplicates; W-302 supersedes automatic conversion to UPDATE and requires a fresh Plan for any existing target — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-302** — Foreign updates require exact QID-bound curator approval; Publication binds QID, revision, and entity digest. A CREATE that finds an existing target needs a fresh Plan. Pass 2 binds the first-pass target; work identity fails closed. Ingest retains distinct folios, but projection can still lose them — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-303** — `owner_only` binds a live creator-owned manuscript update to an exact ledger mapping and a record in the selected run. Local and remote `P3959` must match at normal or preferred rank; claim guards preserve existing claims and metadata and block conflicts. Local snapshots and post-write audit are not atomic with the remote write — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-304** — Preserve explicit source reference blocks across merge, cache, Publication, exports, and writes. Reject ambiguous old flat multi-snak grouping at safety gates; fresh markers do not certify old data. Test-wiki and deferred-reference limits apply — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-305** — Preserve source work identity, author/folio occurrences, script wording, and source scope. Historical proposals bind exact claims, revisions, and digests; local and live review outcomes remain distinct — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-306** — GET retries include JSON body decode within one bounded budget. Owner preview restores summaries first and fetches one result; `include_result=false` defers result JSONB — see [jobs-and-progress.md](docs/architecture/rules/jobs-and-progress.md).
- **W-307** — Exact historical repairs persist the full intent before one revision-bound POST. Recheck current MARC, owner, and complete live state. Unknown server outcomes stop the batch as uncertain — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-308** — Corroborate author-qualified local work URIs against current MARC and the exact graph title transforms. Keep source context immutable. Local measurements run the production quality gate; canonical salt `hmo-wikidata-v23` — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-309** — A warm Studio page checks the cache ID without loading the corpus JSON. Only an initial backfill reads `result_items`; a missing build still fails closed — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-310** — Historical colophon removal permits only source-exact quote wrappers and an optional terminal period; all source and write guards remain mandatory — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-311** — A new P50 from P2093 retains the exact credited name in P1932 and all source evidence — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-312** — Canonical colophon cleanup follows curator overrides and requires one source-exact scoped counterpart; preserve complete reference blocks — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-313** — An approved foreign manuscript retains the active rank of one exact existing P31; all other claim guards remain strict — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-314** — A source build can skip optional AI prose extraction with a strict boolean; progress and heartbeat cover the complete job — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-315** — The second upload pass retains the complete resolvable source payload and complete reference blocks — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-316** — Upload counts use durable receipts and separate AI review; terminal live jobs refresh the table once — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-317** — Source audits require source-specific proof for P50 identities and retain uncertainty in notes and structured parts — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-318** — A new review load stops an obsolete cursor walk after its current page; stale loads cannot consume the remaining pages — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-319** — Bulk HMO writes apply curator overrides and preserve claim metadata — see [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md).
- **W-320** — Person identity choices bind exact source records and remain reference only — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-321** — Single-item reads use exact SQL rows; retain the full detail payload and a small reference-only flag on table rows — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-323** — Identical source citations retain one current retrieval on normal owned updates; append-only paths preserve existing blocks.
- **W-324** — Existing writes require creator proof without foreign approval exceptions; old approvals and cached ownership cannot bypass the guard.
- **W-325** — Generated person-role lists cannot become public descriptions; canonical version `hmo-wikidata-v26` invalidates the old form.
- **W-326** — Original MARC occurrences retain field associations and replace stale derived values — see [authority-marc.md](docs/architecture/rules/authority-marc.md).
- **W-327** — Source choices retain exact evidence, history, and honest requirement coverage — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-328** — Exact repairs use durable Publication state, server delays, and fresh creator user IDs — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-329** — Equivalent P50 representations retain every distinct source block and compatible scope — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-330** — Source-review saves cannot update a different run or item after the original view closes — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-331** — Source choices preserve record and page scope, ranks, historical claims, and complete references — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).
- **W-332** — Source candidate reads use the builder facade without a prior import — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md).

- **W-273** — Research Assistant prompt gate: every user prompt passes the local scope router AND the TypeSafe Jev model check (jev-1.13.0) before any LLM call, enforced at both the Heroku dispatch and the Modal entries (dev-stub-only gating left production ungated); all retrieved text incl. MARC notes passes the quarantine; a provider 402 humanizes and retries once on the fallback model — see [platform-infra.md](docs/architecture/rules/platform-infra.md)
- **W-274** — Remote-agent run contract: every research-agent run emits exactly one terminal event with a machine code (`model_quota_exhausted`, `gate_refused`, `stream_ended`, `deadline_exceeded`), the SSE bridge only synthesizes a terminal when none arrived and never with a look-alike message; a run journal (research_agent_run_journal + `/research-agent/runs/{id}/debug`) records gate→dispatch→terminal; fact/count questions are answered deterministically from the DB (no LLM); the model layer is QUBRID-ONLY with a 402 circuit breaker (2026-10-17: a Modal ModuleNotFoundError surfaced as "stopped responding" and duplicated user rows) — see [platform-infra.md](docs/architecture/rules/platform-infra.md)
- **W-162** — Every PID a projection can emit has a channel row; "unsupported" names its cause
- **W-163** — The export-quality gate runs in the build path, on both sources
- **W-164** — A manuscript's label is a designation; its inception is the audited year
- **W-165** — A work item is attested from exactly one record
- **W-168** — An item whose identifier already exists on Wikidata is an UPDATE
- **W-169** — Review-table verdict reads MUST replay verify's adoption and live labels
- **W-170** — Non-passing projection defects MUST be fixed fail-closed, not rubric-weakened
- **W-171** — Corpus-scale Mode-α predicates + Mode-β sticky-full (ISBD/P1574/extent/incipit/aliases/dates; rubric hygiene; claims-fingerprint sticky-full)
- **W-172** — Judge fixture keeps quantity `unit`; HE desc ≠ label; P1684 catalog notes; person alias identity; facsimile work descriptions
- **W-173** — RELATED_WORKS P1574 ladder; Hebrew preferred P8189 strip; P1922 chronology gate; Cambridge holder label
- **W-174** — Catalogue P973 shelfmark gate; Hebrew restoration brackets; audited P195 gloss; work-title alias elaborations
- **W-175** — Sticky-full on review sanitise; gloss labels out of cache keys; Verify non-passing default; schema-salt policy
- **W-176** — Under designation labels, only the 245/P1476 title may remain as a manuscript alias
- **W-177** — Upload natives MUST carry build-adopted QIDs
- **W-178** — Test and live Wikidata credentials MUST be separate Settings secrets
- **W-179** — Wikidata upload jobs MUST log in once and abort on auth failure
- **W-180** — Wikidata writes MUST pass WikibaseIntegrator's `is_bot` flag
- **W-181** — Test upload path MUST match Studio scope; missing/foreign-alive QIDs CREATE
- **W-182** — Test uploads remap live P/Q by exact label+datatype; leftovers refuse (see W-186)
- **W-183** — Test uploads MUST NOT treat live Q-ids as item values by number alone
- **W-184** — UPDATES MUST pass ownership verification; foreign accept is live-only
- **W-185** — Test upload batches existence, remaps quantity units, omits identifierless persons; aborts on session death
- **W-186** — Test uploads MUST write the full remapped claim set or refuse the item
- **W-187** — Test stub CREATE conflict MUST adopt; MHM stub descriptions MAY be claim-value targets
- **W-188** — Recover trusted VIAF/NLI/QID before the W-154 omit gate; read HMO and legacy evidence shapes
- **W-189** — Test remap MUST stub every live Q and typed P; item-write label conflicts adopt or uniquify
- **W-190** — Person live-identity MUST be bidirectional leftover-token coverage at adopt and upload prepare
- **W-191** — Successful Wikidata writes MUST land every native statement (resolve `__LOCAL:`; never omit unbuilt snaks)
- **W-192** — Wikidata upload defers unresolved `__LOCAL:` to pass 2 MERGE UPDATE; progress is two visible steps with ETA
- **W-193** — Exists-check MUST treat Wikibase quantity amounts and repaired time strings as equal to natives
- **W-194** — Studio natives judged/uploaded MUST drop false live identities and rewrite or degrade leftover `__LOCAL:`
- **W-195** — Clash-cleared identifierless persons MUST skip live writes; uncertain duplicates confirm fail-closed; W-302 defines pass-2 authorization
- **W-196** — Catalog works MUST skip CREATE when an allowlisted live title exists; coerce P1476/P1680/P1684 to monolingualtext; omit implausible codex millimetres
- **W-197** — Wikidata Studio transliteration prewarm MUST snapshot MARC and isolate cache sessions from the build session after a production `MissingGreenlet` failure
- **W-198** — Reused work items MUST retain source-backed author links from later source records
- **W-199** — Linked Wikidata QIDs MUST not trigger CREATE duplicate blockers from stale verdicts
- **W-200** — Work candidates MUST preserve exact links and author identity
- **W-201** — Canonical works MUST retain approved anchor-record authors
- **W-202** — Legacy work projection MUST use approved MARC author matches
- **W-203** — Canonical merges MUST rewrite local references after an ID change
- **W-204** — Work items MUST keep one safe author representation; a safe P50 MUST replace a duplicate P2093
- **W-205** — Ledger QID hydration MUST not invalidate stored AI verdicts
- **W-206** — Wikidata person and work claims MUST use role-specific, heading-matched source evidence
- **W-207** — Legacy judge failures MUST normalize to abstain and preserve diagnostics
- **W-208** — Fingerprints MUST exclude volatile metadata but retain semantic dates
- **W-209** — Work author strings MUST use structured source evidence
- **W-210** — Live verdict rows MUST preserve metadata and retry invalid judge output
- **W-211** — Public AI verdicts MUST use a closed outcome set; provider errors use abstain
- **W-212** — Publication writes use a sealed Release; bulk live upload is separately available under W-291
- **W-213** — Same-person canonical drafts MUST coalesce before export
- **W-214** — Person given names and explicit deferred connections govern upload readiness
- **W-215** — The modern review table must load all API pages before it derives filter counts and action scopes
- **W-216** — PostgreSQL projection order must match Python canonical key order
- **W-222** — AI reviews blocked Publication items through a durable advisory job; a curator supplies exact consent
- **W-223** — Explicit reference-only choices bind an observed QID; Execution excludes skip actions
- **W-226** — AI event reads support large bounded lines; Publication assessments use isolated bounded workers
- **W-227** — Failed Publication workers reconcile to paused resumable executions; the UI shows saved progress and a Resume action
- **W-225** — Publication AI calls release database transactions and isolate verdict cache failures
- **W-224** — Automatic policy separates identity and claims, defers uncertainty, and never fabricates human consent
- **W-220** — Map stored review decisions to entity statuses at the HTTP boundary
- **W-221** — Expose explicit foreign QID consent with exact revision and entity digest
- **W-219** — Restore durable Publication results after refresh; reuse current plans unless cache override is explicit
- **W-218** — Publication dry-runs use durable jobs with progress, cancellation, and terminal receipt refresh
- **W-217** — Publication must use saved target-specific credentials and encrypted execution grants

### [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md) — HMO Wikibase Studio (build, upload, canonical read-back)

- **W-41** — HMO items table upload-outcome fields + upload-lifecycle AI verification
- **W-42** — HMO item upload is SHACL-gated by default
- **W-45** — HMO item build hygiene + verify MARC correlation
- **W-52** — HMO item build must emit substantive metadata for every exportable entity
- **W-53** — HMO item build: honest-negative grounding, person heading fidelity, second-pass label hygiene
- **W-55** — Canonical ontology namespace is `https://w3id.org/mhm/ontology#`
- **W-56** — Wikibase reconcile queries the instance's own direct-property URI, namespace-agnostically
- **W-85** — HMO entity links MUST use the live project Wikibase URL
- **W-86** — Authority identifiers MUST be unique across HMO entities
- **W-89** — Canonical rollout and Authority retirement MUST be fail-closed
- **W-90** — Live HMO read-back MUST use source-URI mappings and normalize Wikibase JSON
- **W-91** — Unsupported Wikibase scalar datatypes MUST be normalized before writes
- **W-92** — Canonical projections MUST fail closed on transient cache fallback
- **W-93** — Retired Authority mutations MUST remain fail-closed and observable
- **W-94** — Canonical readiness MUST be integrity-based, not count-based
- **W-95** — HMO creation MUST gate authority and live identity before canonical replacement
- **W-96** — HMO item builds MUST reject unmapped reconciliation predicates
- **W-97** — HMO canonical read-back MUST follow deferred-link writes
- **W-101** — HMO entities MUST be multi-source enriched through fail-closed matching
- **W-102** — Four HMO pillars: Wikibase root, Wikidata map, ontology mirror, multi-source richness
- **W-109** — HMO Studio MUST resolve AuthorityMatch ID collisions in-place
- **W-111** — All HMO URL claims MUST strip MARC quote wrappers
- **Rule-based verification** — deterministic (non-AI) per-rule verdicts on HMO items: advisory by default, curator-picked blocking rules, fail-closed API rules, Modal shard fan-out. Deep dive: [hmo-wikibase-studio/rule-verify.md](docs/architecture/blocks/hmo-wikibase-studio/rule-verify.md) + block rules R55–R57.

### [jobs-and-progress.md](docs/architecture/rules/jobs-and-progress.md) — Run jobs (claim, admission, progress, publish)

- **W-38** — Run jobs are claimed, heartbeated, and reconciled
- **W-59** — Commit Wikidata verify jobs before materialising their Studio scope
- **W-61** — Rejected verify-job enqueue requests MUST reset the curator modal
- **W-64** — Verify-job progress MUST count distinct candidates, not stream events
- **W-233** — Verify stream `finally` blocks MUST never yield while closing (`generator_is_closing()`); a yield under `GeneratorExit` fails the cancelled job and skips verdict persistence
- **W-234** — RDF graph builds MUST stream per-record subgraphs, checkpoint every 25 records, and run index/coverage post-processing in a subprocess — never accumulate a whole-corpus Graph
- **W-235** — Heavy jobs (build/verify/upload slots) share one admission cap and execute on the worker dyno; web claims a queued heavy job only after the worker grace window
- **W-236** — Cancelling a queued job MUST finalize it, and Cancel must show its effect immediately: `request_cancel` finalizes queued rows inline (still-queued conditional; claim path refuses flagged rows; tick sweep stays as race safety net — 2026-09-24: the 60 s tick made Cancel look like a no-op for a minute). Running jobs finalize via in-loop cancel polling (`cancel_watcher` → `JobCancelledError`, ~1/s — Studio builds poll at phase + record boundaries), the maintenance pass force-finalizes running rows whose flag exceeds `RUN_JOB_CANCEL_FORCE_AFTER_S` (300 s), and the cancel response snapshot is upserted at once + the tray shows `cancelling` while the flag is set (finalization is cooperative)
- **W-237** — rdf_build / hmo_item_build may execute on Modal (mhm-jobs app, body-token auth, Postgres via Modal secret); dispatch failure or unset MODAL_JOBS_URL always degrades to the Heroku runner
- **W-238** — A no-op authority refresh must hit the item fingerprint cache: `re_enrich_run` reports `content_changed`, and only that (or an explicit force_rebuild) triggers the RDF/item rebuild
- **W-239** — "Build items" on an unchanged run short-circuits to the item cache (input-change check against `built_at`); a changed approval/NER row/override bypasses it
- **W-240** — Long jobs must never hold a DB transaction across network/CPU phases: commit per unit of work and recycle the pooled connection (`db.close()`) before CPU stretches — `idle_in_transaction_session_timeout=120s` kills idle open transactions
- **W-241** — Authority enrichment is per-entity resumable: `enriched_at` on each match row; a fresh entity (enriched after the latest upstream change) is skipped without a matcher call
- **W-242** — Authority matching runs concurrently (ENRICH_CONCURRENCY, default 8, per-task sessions); never serially over the network; the apply session commits before the fan-out — no transaction may span the gather; the fan-out and finalize pass report per-completion/chunk progress so the silent gap after the fast sweep never reads as "stuck"
- **W-243** — Modal dispatch accepts any 2xx with `ok:true` (the endpoint answers 200, never 202); every job-runner code change deploys `modal_jobs.py` in the same change, and the `mhm-jobs2` secret carries `AUTHORITY_MODE=postgres` — otherwise Heroku runs the job locally while a stale Modal container races it
- **W-244** — Modal containers heartbeat their own lease (60 s, `claimed_by`-guarded) so a long quiet CPU stretch never trips the stale reap, and `finish_job` never overwrites a terminal row
- **W-245** — Verify scope preparation must yield the event loop and report phases: the 18k-item cache deserialise + MARC-context pass are pure CPU — without yields the heartbeat, publisher, and stale reap all freeze and the dyno wedges
- **W-246** — Never load a multi-MB blob just to test existence or freshness: `ensure_ttl_on_disk` probes `md5(ttl_content)` server-side and transfers the TTL blob only on mismatch, and the merged Studio items view is fingerprint-keyed in-process so unchanged repeat loads skip the 18k-entity merge
 - **W-247** — Large exports stream per item AND stay memory-flat: heavy loads run inside the `StreamingResponse` generator after the first bytes go out (Heroku H12 = 30 s initial window, H15 = 55 s idle between chunks), no single await between streamed bytes may exceed the idle window, DB work uses short-lived `session_scope` windows (a request-scoped session stays pinned for the whole stream), JSON uses `export/formatters.py:json_array_stream` (never `json_stream`, which buffers the whole document), and the data source must be O(chunk) — the rule-verify export strips + scope-filters verdicts in SQL and streams entities off a `jsonb_array_elements` cursor because the full merged view is ~1.1 GB RSS on an 18k run (R15 on a 512 MB dyno)
 - **W-248** — Module-level code in `modal/*.py` MUST run only behind `modal.is_local()`: Modal re-imports the module inside every container on cold start, so module-level shell-outs (e.g. the `git archive` HEAD materialisation) crash-loop the whole app — every dispatched job sat "Waiting for capacity…" while containers died at import (2026-09-19)
 - **W-249** — A crashed Modal job runner MUST fail the job row in-container (no zombie "running forever" rows); see [eval-agent incident 2026-09-18](docs/architecture/blocks/eval-agent/incident-2026-09-18-verify-on-modal.md)
 - **W-250** — JSONB containment binds carry the Python object, never `json.dumps` output: asyncpg JSON-encodes JSONB bind values itself, so a pre-dumped string double-encodes into a jsonb scalar that matches nothing (rule-verify drill-down `results @>` filter and the review-table External authority column filters both matched 0 rows while the Python-side summary still counted the fails) — see [platform-infra.md](docs/architecture/rules/platform-infra.md)
 - **W-251** — Rule-verify value shapes must mirror the builder/exporter output in the same change: the `time` shape unwraps the Wikibase `{time, precision}` dict and accepts the `+` sign, the `quantity` shape accepts a falsy `0.0` amount (never `amount or ""`), and API-backed probes pass every required kwarg (`_fetch_json(..., timeout=...)`). Shape drift or a missed kwarg reads as mass error/fail: run 3494ebf5 logged 2216 `label_candidates` errors + 318 datatype fails from two rule bugs. The same run also fixed the builder side: EN comments/descriptions never embed Hebrew script (`_split_scripts` moves Hebrew to the `he` comment) and object-property flags (`has_vocalization`/`has_cantillation`) emit typed vocabulary individuals instead of boolean literals (which skipped 26 statements) — see [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md)
 - **W-252** — The in-run duplicate rule keys on the item's OWN record CN (the control number present in its source URI), never on `control_numbers[0]`: W-48 propagation gives shared hubs (text traditions, subject headers, corpus-wide works) the CN set of every linked manuscript, so the first entry is corpus-wide — every 'תכלאל' expression across 96 manuscripts and every same-title/different-author work pair read as one duplicate group (536 fails on the run 3494ebf5 re-measure). Items with no own-CN (corpus-shared nodes) are not same-record comparable: the rule marks them `not_relevant` — see [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md)
 - **W-253** — Modal shard fan-out must fit the shared Postgres connection budget: prod `DATABASE_URL` is a shared essential-1 instance (hard 20-connection limit, co-tenants fluctuate 6→18 within minutes), every shard container holds its own SQLAlchemy pool, and oversized fan-outs burst-past the budget (`rdf_build` ×3 + `hmo_rule_verify` ×1 failed with `too many connections` on the run 3494ebf5 rebuild). Shard sizes are env-tunable (`MHM_RDF_BUILD_SHARD_SIZE`, `MHM_RULE_VERIFY_SHARD_SIZE`) with defaults 9500 records / 9500 items; retry on saturation instead of raising concurrency — see [jobs-and-progress.md](docs/architecture/rules/jobs-and-progress.md)
 - **W-254** — Modal executor liveness has its own heartbeat column: `run_jobs.executor_heartbeat_at` (migration 0048) is set/bumped ONLY by the Modal executor; the web poller heartbeats `updated_at` for `modal-%` rows while polling, so every staleness/liveness check for a `modal-executor:*` claim must read `COALESCE(executor_heartbeat_at, updated_at)` (preemption-restart lease acquisition, dispatch lease, `container_alive`). Reading `updated_at` alone made a preempted container's claim look fresh forever — 2026-09-21, run 3494ebf5: the preemption restart exited "lease not acquired", the poller's wait could never expire, and the row ran as a zombie with zero containers until a manual SQL force-cancel — see [jobs-and-progress.md](docs/architecture/rules/jobs-and-progress.md)
 - **W-255** — A protective budget cap or a rate-limited probe abstains as `not_relevant`, never `error`: budget-exhausted `label_candidates`/`qids_alive` and a 429/network probe exception did not execute, so they cannot be execution failures — `error` stays for the API-disabled gate, a partially-executed check, or a mid-check lookup failure. Run 3494ebf5 logged 17,450 `label_candidates` errors (17,252 budget + 198 HTTP 429) while every deterministic rule sat at 0 fails / 0 errors; only 19 genuine label collisions failed — see [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md)
 - **W-256** — Rule-verify Wikidata probes run once, in a single container: sharded `hmo_rule_verify` shards run CPU rules only; after they merge, one dedicated `run_rule_verify_api_pass` container (6 h timeout) owns the whole scope's probe budget (`RULE_VERIFY_API_PASS_PROBE_MAX` default 30 000, `RULE_VERIFY_API_PASS_QID_MAX` default 60 000) and the single throttle domain (W-139), then merges its results into the shard verdicts (API-rule entries replaced, rollups recomputed; summary is per-rule tallies only). Dispatch failure degrades to an inline pass in the orchestrator (W-15), never to skipping probes. Per-shard fetchers split the 300-probe budget four ways (≤1 200 probes for ~12.8k eligible items ≈ 95 % unprobed) and stacked four request rates into 132 HTTP 429s (run 45513a45) — see [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md)
- **W-261** — A Wikidata Studio build must release its read transaction before the canonical entity read, retry a dropped connection from the saved batch cursor at most 3 times, and be allowed to run for 24 hours with CPU work off the event loop (run 3494ebf5 job 5964799c died at 18524/18524 with `ConnectionDoesNotExistError` while the tray stayed on 10001) — see [jobs-and-progress.md](docs/architecture/rules/jobs-and-progress.md)
 - **W-260** — Referenced ontology individuals MUST be draftable, or the build can never go clean: the graph builder references ontology named individuals (`hm:Certain`, `hm:CatalogInherited`, `hm:ComplexHierarchy`, …) as statement objects without declaring them in the run graph, the exporter drafts only typed nodes, so ~3.9k deferred links on run 3494ebf5 could never resolve — every retry failed identically and the `unresolved == 0` canonical gate stayed unreachable. `resolve_against_mappings(..., ontology_index=…) auto-drafts ontology-declared individuals (fail-safe: unmapped class or undeclared URI keeps the URI link), and `EXPORTER_VERSION` rides in the build fingerprint so exporter-logic changes invalidate cached builds; the upload path synthesizes the same entities at publish time (no rebuild needed), classifies never-existing targets as non-blocking `unresolvable`, and renames duplicate cached local_ids deterministically (`<id>_2`) instead of failing the canonical persist — identity is the source URI, never the local_id — see [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md)
- **W-105** — Studio “Approve all visible” MUST run as a background job
- **W-106** — All Studio / RDF builds MUST run as `run_jobs` with inline progress
- **W-107** — All Studio publish/upload paths MUST run as `run_jobs`
- **W-108** — Job-backed publish MUST refresh curator tables on terminal success
- **W-110** — Live Studio upload MUST patch changed rows, not flicker the table
- **W-112** — Pipeline job progress MUST use 1-based steps with a unit label
- **W-113** — Long pipeline steps MUST report nested sub-progress
- **W-129** — Run jobs MUST pass admission control before claim
- **W-141** — The job tray's "View" MUST reopen the surface that started the job
- **W-147** — Verify scope preparation MUST report steps, not a static string
- **W-167** — Verify scope cache partitioning lives in one function
- **W-148** — Subset AI verification MUST retain freshly persisted verdicts when only derived evidence scope changes
- **W-149** — Wikidata verify fingerprints MUST use the curator override-merged Studio item state, matching the main table.
- **W-150** — Wikidata verdict reads MUST validate against the retained pre-derived item projection; ledger QIDs and local-reference display labels are presentation enrichment.
- **W-151** — Wikidata verdict reads MUST reuse the persist-slim projection before stable-key validation.
- **W-152** — Wikidata stable verdict keys MUST ignore subset-derived `__LOCAL:` display labels.
- **W-153** — Identifierless Wikidata Studio person cache rows MUST be stale and rebuilt.
- **W-154** — Canonical Wikidata Studio merges MUST omit person items without a publishable external identifier (recover trusted IDs first — W-188).
- **W-155** — Canonical Wikidata Studio finalization MUST be post-filter and source-scoped.

### [authority-marc.md](docs/architecture/rules/authority-marc.md) — Authority matching + MARC ingest

- **W-23** — KIMA / VIAF / Mazal payload completeness
- **W-28** — Mazal + KIMA live in Heroku Postgres
- **W-29** — Authority payload completeness
- **W-33** (matcher routing — number reused) — Authority matcher routing, deduplication, and notes grounding
- **W-37** — Homonym abstain, scoring, and curator picker
- **W-74** — Hebrew date punctuation MUST NOT abort record normalization
- **W-81** — MARC coverage MUST be loss-aware
- **W-83** — Place authority identifiers MUST preserve their namespace
- **W-84** — Ambiguous KIMA names MUST abstain
- **W-166** — An unconfirmed authority identity may not name or date a person
- **W-322** — Sparse person-date arrays retain source evidence without guessed occurrence associations; completion reports require current public proof.

### [rdf-ontology.md](docs/architecture/rules/rdf-ontology.md) — RDF graph + ontology

- **W-32** — Non-production provenance-event places on the maps
- **W-34** — Full HMO RDF projection
- **W-43** — RDF SHACL validation MUST NOT use RDFS inference; type nodes explicitly instead
- **W-87** — HMO RDF literals MUST be export-safe
- **W-88** — GraphBuilder MUST emit only ontology-declared properties
- **W-230** — Research summary MUST UNION work/person/place vocab the same way manuscripts UNION F4 + Bibliographic_Unit
- **W-232** - Every rdflib `graph.query()` MUST run under the process-global `RDFLIB_QUERY_LOCK` (`query_graph` helper)
- **W-257** — Approved place identity MUST reach the RDF graph regardless of KIMA coordinates (run f4e8e4b3 dropped 11/21 approved place matches behind a coordinate gate)

### [platform-infra.md](docs/architecture/rules/platform-infra.md) — Platform, caching, Heroku, external calls

- **W-15** — modal/ is a deploy target, not a backend dependency
- **W-25** — Redis L1 in front of the Postgres inference cache
- **W-39** — Every on-disk build cache needs a durable Postgres counterpart
- **W-40** — Never hold an open DB transaction across a slow/retrying external write
- **W-123** — Login MUST NOT wait on Wikibase Cloud account provisioning
- **W-228** — Research agent Modal HTTPS-only; user wiki creds never on Modal; scope router + Dual-LLM quarantine + parameterized SPARQL
- **W-229** — Modal AG-UI binds Starlette Request (no query-param 422) and calls `dispatch_request(request, agent=agent)`
- **W-231** — Research AG-UI retries planner output (≥3) and hides pydantic-ai retry strings from the chat
- **W-295** — Corpus refreshes pass both prompt gates; source counts follow upload ledgers and invalidate cached summaries after mapping changes
- **W-297** — Research Assistant relay hardening: relay writes retry once on a dropped connection and map to a retryable 503 (never a bare 500) on `agui-async`/`agui-events`/`agui-stream`; the `agui-events` 503 lets Modal's webhook retry resend; the shared Redis client adds `health_check_interval`/`socket_keepalive`/command `Retry` (2026-11-05: `Agent stream failed (500)` under R14 memory pressure) — see [platform-infra.md](docs/architecture/rules/platform-infra.md)
- **W-298** — Research SSE releases its DB session before the stream; the async Redis client uses async retry; report tables preserve cells and split panels shrink (2026-11-05) — see [platform-infra.md](docs/architecture/rules/platform-infra.md)
- **W-299** — HMO Wikibase writes publish build labels verbatim and compare labels in NFC — see [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md)
- **W-301** — Simple manuscript example requests use bounded, run-scoped MARC records after the gate instead of an unrelated planner audit — see [platform-infra.md](docs/architecture/rules/platform-infra.md)

### [frontend-ui.md](docs/architecture/rules/frontend-ui.md) — Frontend curator surfaces

- **W-263** — A page that waits on a run job polls until the job row is terminal; `waitForRunJob` has no default deadline

- **W-16** — Stage 2 review surface parity with the desktop
- **W-24** — All four curator surfaces support per-field manual editing
- **W-31** — Authority Enrichment review surface parity with extraction
- **W-35** — Reusable glass components, never raw `.glass` classes
- **W-36** — Zustand selectors and parent callback effects

### [security-versioning.md](docs/architecture/rules/security-versioning.md) — Public endpoints, event log, export

- **W-20** — Public endpoints carry the full spam + brute-force stack
- **W-21** — Every curator decision routes through the entity_event log
- **W-22** — Project state + history are exportable as one JSON bundle

### [testing-and-docs.md](docs/architecture/rules/testing-and-docs.md) — Testing + docs-sync gates

- **W-19** — User-flow e2e is the canonical test surface
- **W-49** — Pre-deploy / pre-push docs sync is mandatory

When adding a rule: append it to the matching topic file, add one index
line here, and bump the `W-1…W-N` pointer in this heading plus
[AGENTS.md](AGENTS.md) and [README.md](README.md).

## Project structure

| Path | Purpose |
|---|---|
| `backend/app/main.py` | FastAPI factory, router registration |
| `backend/app/routers/` | One router per resource (runs, ai_verify, extraction, rdf, hmo_studio, wikidata_studio, …) |
| `backend/app/pipeline/` | Per-section orchestrators (extraction, rdf_build, hmo_studio, agent_runner, authority, authority_hardening) |
| `backend/app/crypto/secrets.py` | KEK + encrypted secret round-trip |
| `backend/app/auth/` | Session cookies + RBAC |
| `backend/converter/` | Byte-identical mirror of desktop converter tree |
| `backend/ontology/` | hebrew-manuscripts.ttl + shacl-shapes.ttl (HMO ontology) |
| `frontend/src/routes/` | One page per route (RunDetail compatibility redirect, StageExtraction, StageRdf, HmoStudio, WikidataStudio, ResearchAssistant, …) |
| `frontend/src/components/` | Shared widgets (AgentFlowDiagram, SelectAllVisible, …) |
| `frontend/src/components/wikidata/` | Studio-specific components: `ItemValidatorBadge`, `ItemApprovalBadge` |
| `frontend/src/components/research/` | Research Assistant chat/canvas + map/SPARQL/network panels |
| `frontend/src/api/` | Per-resource API clients |
| `frontend/tests/` | Vitest unit tests |
| `frontend/e2e/` | Playwright browser tests |
| `backend/tests/` | pytest + httpx route tests |
| `modal/modal_app.py` | Modal app for the four NER + genre models (deployed; not imported by backend) |
| `modal/modal_research_agent.py` | Research Assistant AG-UI (deployed; not imported by backend; Rule W-228) |
| `modal/README.md` | Modal deploy + economics |
| `docs/project-hierarchy-plan.md` | Authoritative plan reference |
| `docs/testing.md` | Three-layer test pyramid documentation |

## Common commands

```bash
# Backend dev server
cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

# Frontend dev server
cd frontend && yarn dev

# Backend tests
cd backend && .venv/bin/python -m pytest tests/ -v

# Frontend unit tests
cd frontend && yarn test:unit

# Browser e2e (one-time browser install: npx playwright install chromium)
cd frontend && yarn test:e2e

# Typecheck frontend
cd frontend && yarn tsc --noEmit

# Full smoke: ensure all routers register
cd backend && .venv/bin/python -c "from app.main import app; print(len(app.routes))"

# Modal — deploy the NER app (after editing modal/modal_app.py)
cd modal && modal deploy modal_app.py

# Modal — deploy the Research Assistant (after editing modal_research_agent.py)
cd modal && modal deploy modal_research_agent.py
# then: heroku config:set RESEARCH_AGENT_MODAL_URL=https://<workspace>--mhm-research-agent-web.modal.run

# Modal — tail container logs (for cold-start debugging)
modal app logs mhm-ner

# Switch extraction to Modal (heroku)
heroku config:set EXTRACTION_MODE=modal MODAL_NER_URL=https://<workspace>--mhm-ner-mhmner-web.modal.run

# Switch authority enrichment to Postgres (production default — Rule W-28)
heroku config:set AUTHORITY_MODE=postgres

# Import Mazal + KIMA from local SQLite into Heroku Postgres (one-time, idempotent)
# Run locally pointing at Heroku DATABASE_URL — 15 s for KIMA, ~10 min for Mazal
cd backend && DATABASE_URL=... KIMA_DB_PATH=backend/data/kima/kima_index.db \
  .venv/bin/python -m scripts.import_kima_to_postgres
cd backend && DATABASE_URL=... MAZAL_DB_PATH=.../mazal_index.db \
  .venv/bin/python -m scripts.import_mazal_to_postgres
```

## What this web app does NOT do (yet)

- Train models — pipeline (desktop) owns training.
- Run the eval-agent orchestrator (planner) as a user-facing surface.
  The planner is internal research tooling; only the per-candidate
  `ai-verify` modal is exposed.
- Single-item direct live Wikidata push — that route returns `410 Gone`.
  The bulk live upload API is separate from the sealed Publication path (W-291).
- Auto-approve AI verdicts (curator always confirms; verdicts surface
  as a `✨ AI says pass` pill).

## When to update the plan / this file

Update `docs/project-hierarchy-plan.md` whenever:
- A new section is added under Project (currently 5: AI Extraction,
  Authority, RDF Graph, HMO Wikibase, Wikidata Studio).
- A backend route is added, renamed, or removed.
- A new ML model or external API is integrated.

Add a rule to the matching file in
[docs/architecture/rules/](docs/architecture/rules/) (plus one index line
above) whenever:
- A new architectural invariant emerges from an incident.
- A trust boundary changes.
- A "Stage N" creeps back into user-facing strings (delete it; cite Rule W-3).

Keep the rule text out of this file — it is the index, not the rulebook.

Also bump the `W-1…W-N` pointer in [AGENTS.md](AGENTS.md) and
[README.md](README.md) when you add a new `Rule W-N`.

A code change that alters the architecture is not complete until the
plan doc, block docs (see [AGENTS.md](AGENTS.md)), and this file are updated.

## Reading list (re-read at session start when stuck)

- `docs/project-hierarchy-plan.md`
- `docs/testing.md`
- The desktop CLAUDE.md at
  `/Users/alexandergo/Documents/Doctorat/pipeline/CLAUDE.md` (the
  source of every safety guard listed above).
- The eval-agent CLAUDE.md at
  `/Users/alexandergo/Documents/Doctorat/eval-agent/CLAUDE.md`.
- **W-275** — Every MARC field has a deliberate Wikidata fate, recorded and measured: `converter/wikidata/marc_field_map.py` gives each field one fate (`map` / `evidence` / `out_of_model`), and `scripts/marc_wikidata_coverage.py` measures per-field and per-property coverage against it, exiting non-zero on regression (CI runs an in-repo fixture). A holding library is `710` → `P195` (collection), never `P50` (author) (2026-10-18: 897/897 records carried `710` documented as `P50` at 0%; `100$a`/`700$a` authorship 0%; nine catalog-only fields with no recorded fate) — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-276** — Identity and linked-tag MARC channels are complete: `035$a` falls back into the P3959 identity channel when `001` is absent (agency prefix and `-Aleph` stripped, anything unrecognised fails closed), and MARC `880` projects as its linked tag (`880$6` names the tag; the romanized form reaches `marc_880`/`marc_880_name`, the vernacular is never overwritten) (2026-10-18: records with no `001` were unjoinable; all 25 `880` rows were dropped despite carrying the ALA-LC romanized title) — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-277** — A coverage measurement must prove its join works before it reports 0%: a projected work reference is `__LOCAL:<local_id>` while the work is filed under `<local_id>`, and property share counts each PID once per record (a shared work must not push a share past 100%); tests pin both (2026-10-19: the first harness reported `100$a`/`700$a` at 0% against real 89.5%/45.9%, and `P1476` at 167% — a false 0% is indistinguishable from a broken join) — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-278** — The live-instance HMO ledger is cross-run and readers must share the writer's scope: `wikibase_entity_mappings` is the write-dedup ledger, so `hmo_item_reconcile.live_instance_qids_select()` is the one definition (all runs, `run_id IS NOT NULL`, ambiguity dropped in both directions) and every reader — data-status badge, facets, merged views, `X/Y on wiki` counter, cache-invalidation key — resolves through it (2026-10-19: run 47e03437 showed 18,480 items as "new (not uploaded)" and `0/18481 ON WIKI` while 18,466 were already live from runs 3494ebf5 and 48ba6c13; only 14 were genuinely new — the reader was run-scoped and the writer global) — see [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md)
- **W-279** — A chunked loop's step and slice must agree: `run_rule_verify_api_pass` stepped by `_API_PASS_CHUNK` (100) but sliced `CHUNK` (1000), overlapping chunks by 900 items so every item was probed up to 10x and `done` ran to ~10x total; verdicts survived (keyed by `local_id`) which is exactly what made it invisible in the output (2026-10-21: run 47e03437, 18,481 items, the probe pass ran 22 min without finishing or reporting a readable number). One constant on both sides; the test asserts each `local_id` merges exactly once. Also: the Heroku Postgres role limit is 20 connections — under fan-out load progress callbacks fail and the bar freezes while the job still works (a moving `updated_at` means it is not dead) — see [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md)
- **W-280** — External reads are batched and cached globally: `prime_fetch_caches` batches `wbgetentities` 50 ids per request (18,481 items → 370 requests) and writes every request through `cache_http_call(user_id=None)` — the Redis L1 → Postgres L2 `inference_cache`, shared across users and projects. The cache key is endpoint + payload with volatile keys (timestamp/date/now/nonce/…) stripped, so date and time never enter it. TTL `wikidata.wiki_entity` (7 d) not the 90-day label TTL, because `hmo.live.alive` must still notice a deletion. A missing `wikibase_cloud_base_url` logs at WARNING and priming refuses to fetch rather than building a host-less relative URL (2026-10-21: run 47e03437 made one throttled request per item and read `4100 / 18481` after 90+ minutes, and every later run paid the same price because the memo died with the pass) — see [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md)
- **W-281** — Read the field that holds the data; a gate must not hide a filled one: `958$a`/`958$b` now feed `script_type`/`script_mode` (P9302 5.4% → 73.7%), `957$a` feeds `colophon_text` after its `קולופון:` label is stripped at ingest (P1684 0.9% → 62.8%) with the Rule W-72 placeholder guard untouched, P921's gate is a *verified QID* not an explicit `primary` marker (2.3% → 50.9%) with `Jews`/`Judaism` still withheld, and genres that name their Wikidata concept directly project without a second assertion (P136 15.1% → 27.8%) while `Illustrated works` stays gated (2026-10-21: four properties were far below what the catalogue holds — an unread column or a gate rejecting evidence it already had, not a data gap; five others are corpus-capped and `P1071` has zero source rows) — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-282** — A parameter that never reaches its target is a silent total failure: `build_context` accepted `wikibase_endpoint` and never passed it to `RuleContext`, so every live rule built a host-less `/w/api.php` and failed with `unknown url type` (72 entities / 200 rule errors on run 47e03437 while the setting was correct all along); and `_build_claim_spec` shaped an empty `monolingualtext` into a claim, so 885 items carried `{"text": ""}` on P288 and failed `claims.datatype`. Verify a value at its target, not just at the caller — an omitted constructor argument does not raise — and empty input is not a claim value (2026-10-21) — see [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md)
- **W-283** — A finding must describe a defect, not a hint: the CanonRef label dropped its hierarchy so `Mishnah: Berakhot` and `Talmud Bavli: Berakhot` collapsed onto one label (52 false within-run duplicates); `_run_claims_present` scored Rule W-260 vocabulary stubs (`CertaintyLevel`/`DateFormatType`/`HierarchyType`/`AttributionSource`/`EpistemologicalStatus`) as entities with no claims (11); and `hmo.wikidata.label_candidates` scored a link candidate as a failure (28). `fail 976` on run 47e03437 was 91 hints and 885 empty-claim shapes — no bad data at all. Scoring hints as failures makes the count meaningless, and a meaningless count hides the real ones (2026-10-21) — see [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md)
- **W-284** — An empty value is silence, not an unresolved statement: `_build_claim_spec` returning `None` has two causes and only one is a defect, so `_is_blank_statement_value` drops blank input without recording it while a genuinely unshapeable value still lands in `skipped_statements` (2026-10-22: the W-282 empty-claim fix moved the same 885 shapes from `claims.datatype` to `statements.resolved` — one defect replaced another; check what the caller does with the value an edited branch now returns) — see [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md)
- **W-285** — The connection budget belongs to the deployment, not to every process: `essential-1` caps Postgres at 20 connections and `app/db.py` gave every process 15, so a rule-verify fan-out wanted ~90 and killed a build mid-refresh while progress callbacks died behind `too many connections for role`. Pools are now 1+2 by default (`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`), `pg_bouncer_url` routes the shared engine through PgBouncer when set, `app.realtime`'s LISTEN bridge is pinned to `database_url` because LISTEN is session-level and dies under transaction pooling, and shard sizes were raised to halve concurrent containers (2026-10-22) — see [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md)
- **W-286** — Verdict inputs must keep structural claims visible and quote transcription provenance: 62 per-fragment P1684 claims per manuscript pushed P31 past the judge's 40-statement projection, so 111 of 123 AI-verdict fails on run 47e03437 read "no instance of" that was present — transcription claims now merge to one per property with the NLI reference (salt `hmo-wikidata-v15`), the projection cap prioritises P31/structural PIDs, unquoted transcriptions cite an `hmo_extraction` channel against temporary-record MARC stubs, and the judge display orders P31-first with 400-char value truncation (2026-10-28) — see [ai-verify.md](docs/architecture/rules/ai-verify.md)
- **W-287** — The default tier-1 judge must not depend on a per-user key: a re-verify job died on "missing Gemini API key" while `QUBRID_API_KEY` sat in both the web dyno and the `mhm-jobs2` secret; the default is now Qubrid Kimi K2.5 from one switch (`tier1_models.yaml` `default` → `default_tier1_model()` — routes, cache-key helpers, dropdown), Qubrid/TypeSafe use server keys only, Gemini stays an explicit choice, and verdict keys carry the judge model so a default switch re-judges (2026-10-29) — see [ai-verify.md](docs/architecture/rules/ai-verify.md)
- **W-288** — A curator's drawer accept binds the QID on every judged path: `apply_wikidata_item_override` maps `accepted_foreign_qid` → `existing_qid` when absent, or the verify scope judges the item without the binding and fails it as a live CREATE duplicate after the curator chose (2026-10-29) — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-289** — A batch job may not hold one session for its whole scope: under the 1+2 pool the bulk-approve's single held session starved pollers into `QueuePool limit … connection timed out` and the batch died at 250/881; chunk ~50 items per short-lived session (2026-10-29) — see [hmo-wikibase.md](docs/architecture/rules/hmo-wikibase.md)
- **W-290** — Reconciliation reads are batched before the per-item loop: per-item WDQS identifier lookups ran at ~1.3 items/min (15 h dry-run ETA on run 47e03437); `prewarm_reconciler_for_items` fills the reconciler cache with one `VALUES` SPARQL per 40 ids and `_make_reconciler` is a process singleton (2026-10-29) — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-291** — The direct live upload is restored: the Sep-5 retirement left connection-bearing corpora unshippable (the release's own deferred-reference findings block review), so the `live` target returns to the Upload panel and the stored Settings credential releases with the curator's session at job creation — the 2026-10-17 mechanism (2026-10-31) — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-292** — Sharded upload ownership, P2888 work identity, ledger persistence on reconciled skips, and new/verified scope filters: shards authenticate slot uploaders once and `_get_authenticated_user()` uses the WBI session (+ forwards `our_p3959` on manuscripts); works pre-warm/reconcile by `P2888` HMO URI and bypass LLM confirm when `P2888` matches; LLM confirm receives author+description context with retry/memo; live label+description collisions adopt owned QIDs or skip cleanly; `upload_items` persists reconciled `updated`/`exists`/`skipped` QIDs to the ledger; `update_existing=False` and `ai_verified_only=True` filter step-1 while pre-seeding `created_qids`; and identifierless persons read `local_only` while `create` reads `updated` in the UI (2026-10-31, job b4905cb6) — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
- **W-293** — Research Assistant visual signals, collapsed Markdown recovery, Wikimedia-compliant User-Agent, and Basic-dyno pool/memory guards: `ResearchMarkdown.tsx` normalizes single-line headings/lists/pipe tables into `SignalDataTable` views and auto-extracts corpus KPI/role/place signal cards; `EdaDashboardPanel.tsx` and `ResearchCharts.tsx` render Base44-style stat tiles, century/25-yr trends, role donuts, ranked bars, and coverage funnels; `show_chart` and the analytics tools place interactive canvas artifacts; `wiki.py`/`tools.py` send the Wikimedia-compliant `MHMPipeline/1.0 (https://github.com/alexgoldberg/mhm-pipeline; alexander.goldberg@biu.ac.il)` `User-Agent` and catch `httpx.HTTPError` on bot login; and `research_graph.py`/`research.py`/`research_provenance.py` serialize graph loads (`_LOAD_LOCK`, cap 1), release DB connections (`await db.close()`) before rdflib CPU work, compute SQL fingerprints without loading MARC/TTL blobs, preserve `canvas_state` artifact order, and open corpus maps directly in `corpus` mode so the 1+2 QueuePool and 512 MB dyno never stall on "Loading map…" (2026-11-02) — see [platform-infra.md](docs/architecture/rules/platform-infra.md)
- **W-294** — Prof. Prebor's 9-point MARC-to-Wikidata conversion audit: `_extract_942_holdings` pairs `942$a/$x/$c/$z` with `710$a/$e` (`current owner`) for `P217` shelfmarks (`Ms. 2473`, `Ms. EVR I 726`, `Ms. Kaufmann A 315`) and `<942$a>. <city>, <country>. <942$z>` labels (894/897 records); `_determine_instance_type` emits `Q123172729` (`manuscript codex`, 762 records) and `Q720106` (`scroll`, 13 records) alongside `Q87167`; `ledger_key_for_item` and `reconcile_work_by_label_and_author` separate homonymous works (`דרושים`) by `P2888` and `P2093`/`P50` author while `_add_contents` retains `100/245` works alongside `500` works; manuscripts emit `P361 = Q118384267` (`Ktiv`) + `P3831 = Q60474998` (`digitized collection`); `100$c`/`700$c` join onto `$a`, `(ממנו)`/`(אליו)`/`former owner` map to `P3342` + `P3831` role qualifiers (`Q10359183`/`Q28008314`/`Q136357543`) + former `P217` shelfmark, and `?` adds `P1480 = Q30230067` (`possibly`); folio-prefixed `500$a` works and `740$a` project to `P1574` + `P958`; `P571` omits `P1480 = Q5727902` (`circa`) on exact years and attaches Hebrew dates via `P1932` (779 records); `751$e = related place` uses `P3831 = Q127695525` (`place of attestation`); `"באיטלקית באותיות לטיניות"` emits `P282 = Q8229` (`Latin script`) without false `ItalianScript` `P9302`; and `581$a` bibliography (`P528`, 532 records), `957$a` colophon folios (`P7416`), and `500$a` title-page inscriptions (`P1684` + `P3831 = Q1339862`) are preserved (2026-11-02) — see [wikidata-studio.md](docs/architecture/rules/wikidata-studio.md)
