# AI Extraction — Rules

> Up: [AI Extraction](README.md)

1. **R1 — Modal is a deploy target, NEVER a Python dependency.** The backend
   MUST talk to `MhmNer` over HTTPS (`MODAL_NER_URL`) only; never import
   `modal/modal_app.py`. *Why:* trust/deploy boundary (Rule W-15) — the dyno
   stays torch-free and the Modal image redeploys independently.
2. **R2 — `_bake_weights` MUST run before any `add_local_dir` step.** *Why:*
   Modal rejects misordered chains, and baking first keeps the ~3 GB weight
   layer cached when desktop NER code changes.
3. **R3 — Every inference call MUST go through `cache_lookup_or_call`.** NER
   and genre kinds are content-addressed with no expiry. *Why:* Rule W-25 —
   the first user pays Modal; everyone else warm-hits Redis/Postgres.
4. **R4 — Re-running extraction MUST NEVER clobber curator state.** The
   `_bulk_persist_entities` upsert updates only prediction-snapshot columns
   (`type/role/confidence/model_confidence/exists_in`) on conflict. *Why:*
   the content-addressed unique key exists precisely so approvals survive
   re-runs.
5. **R5 — Postgres is the durable entity store; `ner_results.json` is a cache.**
   Status and entity endpoints MUST fall back to `extraction_approvals` when
   the file is missing. *Why:* Heroku wipes local disk on every deploy
   (Rule W-39 family).
6. **R6 — Records read from `run_records.marc` MUST be re-collapsed** (call
   `_collapse_marc_subfields` / `prepare_record_for_pipeline` when raw `$`
   keys exist) before any NER input is built. *Why:* the 2026-06-02 smoking
   gun — empty `notes`/`provenance`/`contents` means Modal is called with
   empty strings → 0 entities → 0 work items downstream.
7. **R7 — Production dates come from 260/264$c, NEVER 008.** The shared date
   contract keeps negative astronomical years from explicitly marked BCE
   centuries as the lower boundary; positive century starts stay imprecise
   until an exact source narrows them. *Why:* 008 is catalog-entry metadata,
   not manuscript production date (`marc_date_sources.py` contract).
8. **R8 — Entity dedup keys on `(normalize(text), kind)` with role-priority
   merge; different kinds MUST stay separate entities.** Replaced roles go to
   `alt_roles`. *Why:* Rule W-33 — a person named in 100 and 600 is one
   entity; a place and a person sharing a name are not.
9. **R9 — Any input-changing PATCH MUST clear `ai_verdict` and recompute
   `exists_in`.** *Why:* a verdict/grounding computed for the old text is
   stale evidence.
10. **R10 — One model failure MUST NOT fail the session.** `warm_up`
    availability degrades per role; per-record exceptions are logged and the
    record continues with the remaining models. *Why:* four independent
    models; a curator can still review three-quarters of the output.
11. **R11 — Curator mutations route through the entity_event log** via
    `_emit_extraction_event` before commit, and MUST invalidate
    `extraction.entities` scoped cache after. *Why:* Rule W-21 versioning +
    stale-poll prevention.
12. **R12 — Auto-approve preview and apply MUST share one predicate function**
    (`_auto_approve_eligible`). *Why:* a preview count that diverges from the
    apply count destroys curator trust.
13. **R13 — AI verify actions are prefab registry entries only**
    (`extraction_actions.py`); the UI never accepts free-text agent goals.
    *Why:* bounded, auditable agent behaviour.
14. **R14 — Zustand/table-reporting invariants of Rule W-36 apply** to any new
    review-UI capability (primitive selectors, fingerprint-guarded
    `onFilteredChange`, stable callbacks). *Why:* three production blank-UI
    incidents (React #185).

15. **R15 — MARC 500 work mentions are derived, source-aware data (Rule W-68).** Recompute them from raw 500$a on every preparation; triggers must begin a note or follow a manuscript noun; prefer quoted titles and otherwise split only at semicolons or recognised title heads. Remove older 500-derived contents before merging fresh results. *Why:* the old כולל substring, comma, and Hebrew-vav splitter minted geography, people, citations, and catalogue prose as works.
16. **R16 — Every non-empty MARC tag must be structured or explicitly classified as evidence-only.** The full-corpus coverage audit (`scripts.audit_mapping_coverage`) is a release check; flattened TSV/JSON uploads reuse the canonical desktop handlers and retain uncommon title, note, shelfmark, and RDA carrier values. *Why:* a tag that disappears before RDF cannot be recovered by Wikibase or Wikidata reconciliation.
17. **R17 — An inserted content section does not establish authorship or a physical unit (W-296).** The anchored sermon parser retains its descriptive title, source, genre, and record identity. It does not turn the scribe into an author or infer a folio. *Why:* Gila's opening sermon disappeared while the manuscript inherited its genre.
18. **R18 — Retain distinct source folios for repeated work titles (W-302).** Ingest keeps distinct title/folio pairs on repeated preparation. This is not an end-to-end projection guarantee. *Why:* title-only dedup can discard source locations.
19. **R19 — Preserve source script terms and catalogue reference fields (W-305).** Retain `script_type_source` and `script_mode_source` beside normalized classes. Keep a 942 catalogue name and number separate. Retain 581 citation text as source evidence. *Why:* a normalized script class loses specific source words, and bibliography prose is not a catalogue code.
20. **R20 — Explicit rejection survives every review path (W-305).** Preserve `rejected` in the API, events, cache fingerprints, import, export, and history. Auto-approve excludes rejection. *Why:* a false approval alone means pending. See [explicit rejection](rejection.md).
21. **R21 — Person dates require intact occurrence slots (W-322).** Preserve empty slots and retain ambiguous arrays in `person_subfield_reviews`. *Why:* sparse 700 dates shifted onto the wrong people. See [person dates](person-dates.md).

22. **R22 — Preserve original field occurrences (W-326).** Derive each association from its occurrence. Withhold sparse flat dates, roles, cities, and countries. Replace stale derived values. *Why:* the old fallback borrowed another library's location.
