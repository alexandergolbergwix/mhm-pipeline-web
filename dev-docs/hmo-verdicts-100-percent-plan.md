# Plan: HMO Wikibase items → ~100% "full" AI verdicts

Source: deep investigation of 421 fresh Qubrid judgments on run
`48ba6c13-115c-4763-bff1-c08b9031b518` (fresh full 298/421 = 71%;
Production 0/62, TextTradition/Witness/Time-Span/Work_Creation still queued).
This plan implements Track A (code) + Track B (rubric) + gate extensions.

**Precondition:** the working tree already carries the uncommitted
export-quality-gate work (`hmo_export_quality.py`, `hmo_export_quality_gate.py`,
graph_builder/exporter/rdf_helpers edits, `--persist-verdicts` in the fixup
loop). This plan builds ON TOP of that diff — do not revert it. Commit it
first (or together with Phase A) so the baseline is pinned.

**Mirror caveat (Rule W-43 residual):** all `backend/converter/` edits must be
hand-ported to the desktop pipeline repo later — do NOT run
`sync_converter_to_web.sh` while the desktop repo carries unrelated WIP.

---

## Phase A — graph_builder metadata (Track A, P0→P2)

All in `backend/converter/rdf/graph_builder.py` unless noted.

### A1. Production events (P0 — ~68 items at 0% full)
`_add_production_event` (line ~818) emits NO `rdfs:label` and NO
`rdfs:comment` on `prod_uri` itself (only on place/time nodes) — exporter
falls back to URI local name `Production {cn}`.
- Build label `Production of MS {cn}` + parenthetical substance from what is
  known: `clean_marc_label(data.place)`, `self._format_time_label(data.dates)`,
  scribe names (collect from the scribe block that already runs in
  `build_graph`; pass scribe names into `_add_production_event` or stamp the
  label/comment after the scribe loop).
- `_stamp_wikibase_comment(graph, prod_uri, ...)` with the same substance:
  `"Production event of manuscript {cn} in {place}, {date span}, scribe {names}."`
  (omit missing parts; never emit an empty parenthesis).

### A2. Time-Span labels (P3 but same function — ~50 items)
Line ~913: `graph.add((time_uri, RDFS.label, Literal(time_label)))` — bare
`1460`, no lang tag.
- Change to `Literal(f"Production period {time_label} (MS {cn})", lang="en")`.
- Keep the existing comment. NOTE: `time_span_uri(time_label)` is shared
  across manuscripts with the same span — if two MSS share `1460`, the label
  gains one MS's CN. Either (a) keep the shared URI and label
  `Production period {time_label}` without CN, with the comment carrying
  "used by N manuscripts", or (b) leave CN out of the label entirely.
  **Decision: no CN in the label** (shared node), CN(s) in the comment via
  the same merge/dedup the exporter already does for multi-MS comments.

### A3. TextTradition (P0 — ~75 items)
`add_text_tradition` (line ~2474):
- Label language: `Literal(label_text, lang=label_language_for_text(label_text))`
  instead of hardcoded `"he"` (Latin titles like `Diodati Segre` currently get
  a Hebrew tag).
- Guard: callers must skip tradition creation when
  `not is_usable_work_title(title)` or `is_descriptive_content_title(title)`
  — apply at the call sites in the philological-layer path (grep
  `add_text_tradition(` callers), mirroring the Work/Expression guards.
- Comment: enrich default to
  `"Textual tradition of the work '{title}', attested in manuscript {cn}."`
  (pass `control_number` from the caller; keep existing `description` override).

### A4. TransmissionWitness (queued ~73 items)
`add_transmission_witness` already stamps label + comment. Only:
- Skip creation for unusable titles (same guard as A3).
- Append folio range to the comment when the linked expression has one.

### A5. Manuscript labels (P1 — ~13 partials)
`_add_manuscript` (line ~582): `label = data.title or f"MS {control_number}"`
uses the raw 245.
- `title = sanitize_work_title(data.title or "")`; if empty or `< 4` chars →
  `f"MS {data.shelfmark or control_number}"`.
- When `data.shelfmark` exists, also add an `en` label
  `f"Jerusalem, NLI, {data.shelfmark}"` (matches the Wikidata Studio
  shelfmark-fallback pattern). Check whether `ExtractedData` exposes
  `shelfmark`; if not, thread it from ingest (it exists on MARC AVA — see
  `holding_*` handling) or skip the en label in this pass.

### A6. Expression descriptions (P1 — ~30 partials)
`_add_expression` (line ~769) / `_add_content_work`: replace the template-only
comment with substance:
`"Expression of '{title}' in MS {cn}, folios {folio_range}, language {lang}"`
— pull `folio_range` from the parsed 505 entry (already produced by
`parse_contents_entry`), language from `data.languages`, omit missing parts.
Block export of expressions whose title fails `is_usable_work_title()`
(same gate as works — currently only works are gated).

### A7. Person / organization hygiene (P1 — ~18 partials + 1 fail)
`backend/converter/rdf/rdf_helpers.py`:
- `clean_person_display_name` (line ~169): strip dangling `אבן` suffix (same
  treatment as the existing dangling `בן`).
- Organizations (`E74_Group`, e.g. `Sassoon, David Solomon` collections):
  label `lang="en"` only — never copy a Latin org name into the `he` slot.
  Find the org-node creation site (grep `E74_Group` in graph_builder) and
  route labels through `label_language_for_text`.
- `backend/converter/wikibase/hmo_exporter.py`: dedupe repeated sentences when
  merging multi-MS comments into one description (Sassoon linked to 3 MSS
  currently repeats the same clause 3×) — split on `". "`, dedupe preserving
  order, rejoin, then `_truncate`.

### A8. Work-title sanitization, gershayim-aware (P2 — ~12 partials)
`sanitize_work_title` (rdf_helpers.py:125):
- Normalize Hebrew gershayim `״`/`"` pairs; strip *unbalanced* trailing `"`/`)`
  without touching legitimate abbreviations (`ה"ה`, `שד"ל`) — reuse the
  gershayim-preserving logic already in `clean_marc_label`'s
  `_ISBD_ADJACENT_QUOTED` handling.
- Apply `disambiguate_work_label()` to content works (505-derived), not only
  the main 245 work.

### A9. Genre/SubjectType (P2 — ~22 queued)
`_add_genre_node` (line ~738) already takes `control_number` — audit every
call site passes it (grep `_add_genre_node(`). Label language via
`label_language_for_text(genre)` instead of hardcoded `he`.

---

## Phase B — rubric + evaluator alignment (Track B)

Without this, code fixes plateau (~90–95%): the judge treats improved
Production/Time-Span labels as "system identifiers".

`eval-agent/config/rubrics/hmo_wikibase_item.md` — extend rule 3 (structural
entities, after existing 3c):

```
3d. E12_Production / E52_Time-Span / F27_Work_Creation / TransmissionWitness /
    TextTradition — intentional English system labels that carry MS scope in
    label or description AND a substantive description (place, date, scribe,
    folio, or tradition scope) → name_ok = yes. Do not downgrade for
    "Production of MS …" wording alone.

3e. E74_Group (organizations/collections) — name_ok = yes when the English
    org name is present and the description carries manuscript linkage;
    never expect Hebrew personal-name format; role_ok follows the org's
    custody/ownership role, not authorship.
```

`eval-agent/eval_agent/evaluators/hmo_wikibase_item.py`:
- No structural change needed unless the prompt hides `entity_type` for these
  classes — verify Production/Time-Span/Group reach the prompt with their
  `entity_type` (they should, post W-48).

**Cache invalidation (Rule W-51):** bump the schema salt in
`backend/app/pipeline/hmo_item_verdict_cache.py` (`w50_v1` → `w52_v1`) so old
verdicts miss automatically after the rubric change — no `override_cache`
needed. Also bump the eval-agent side salt if the rubric hash is not part of
the prompt (it is via prompt text, so layer-2 self-invalidates).

---

## Phase C — export quality gate extensions

`backend/converter/wikibase/hmo_export_quality.py` — new checks in
`audit_entity_draft`, so regressions are caught at build time, before AI
verify:

| code | trigger |
|---|---|
| `production_missing_label` | `entity_type == E12_Production` and no en label beyond URI local name |
| `timespan_bare_label` | Time-Span label matches `^\d{3,4}(-\d{3,4})?$` |
| `latin_label_in_he` | `he` label contains no Hebrew characters (covers orgs + traditions) |
| `unbalanced_label_quotes` | label has odd count of `"`/`״` after gershayim-abbrev exclusion, or dangling `)` |
| `witness_unusable_title` | TransmissionWitness/TextTradition whose title fails `is_usable_work_title` |

Wire nothing new — `hmo_item_build` already runs the gate.

---

## Phase D — tests

- `backend/tests/unit/test_rdf_helpers.py` — gershayim sanitization, `אבן`
  suffix strip, content-work disambiguation.
- `backend/tests/unit/test_graph_builder_codicological_labels.py` (extend) —
  Production label/comment substance; Time-Span label; Manuscript shelfmark
  fallback; TextTradition Latin label language; skipped unusable traditions.
- `backend/tests/unit/test_hmo_export_quality.py` (extend) — the five new
  gate codes, positive + negative cases (gershayim abbreviations must NOT
  trip `unbalanced_label_quotes`).
- `backend/tests/unit/test_hmo_exporter_descriptions.py` (extend) — multi-MS
  comment dedup; org en-only labels.
- `eval-agent/tests/test_hmo_wikibase_items.py` — rubric 3d/3e fixtures
  (Production with substance → prompt carries entity_type; no assertion on
  the judge itself, just prompt content).

Run: `cd backend && .venv/bin/python -m pytest tests/ -k "rdf_helpers or graph_builder or export_quality or exporter_descriptions" -q`
then the full backend suite.

---

## Phase E — docs (mandatory, Rule W-49 gate before any push/deploy)

- `CLAUDE.md` — new **Rule W-52** (structural-entity metadata + rubric 3d/3e +
  gate extensions + salt bump), pointer bumps in `AGENTS.md` / `README.md`.
- Per `docs/architecture/task-index.md`: `rdf-graph` (rules/key-files/tests),
  `hmo-wikibase-studio` (rules/tests, gate codes), `eval-agent`
  (rubric note, skills-and-tests).

---

## Phase F — verify loop (definition of done)

1. Local: unit tests green; local rebuild of run `48ba6c13`
   (`python -m scripts.rebuild_run_rdf_and_items <run>` against the target DB)
   → export gate **0 issues**, SHACL **0 violations**.
2. Pilot: `python -m scripts.hmo_item_verify_fixup_loop --run-id <run>
   --limit 30 --persist-verdicts` with `QUBRID_API_KEY` — expect Production /
   Time-Span / tradition samples to flip to `full`.
3. Full: `--all --persist-verdicts` (~1,000 items, 3–7 h) — target ≥95% full
   on exportable scholarly entities; remaining partials must be genuine
   catalog noise (1-token generic titles like `תורה`), to be fail-closed at
   build (not exported) or accepted.
4. Only then (with explicit user permission per push): commit, pre-deploy docs
   gate, deploy, Heroku rebuild + production re-verify.

## Expected impact

| Fix | Items | Gain |
|---|---:|---|
| A1 Production | ~68 | +68 full (0% → ~full with B) |
| A3/A4 Tradition/Witness | ~148 | +110–130 |
| A6 Expression | ~30 | +20–25 |
| A5 Manuscript | ~13 | +10 |
| A7 Person/org | ~18 | +12–15 |
| A8 Works | ~12 | +8–10 |
| A2 Time-Span | ~50 | +40+ (needs B) |

~71% → ~95%+ full; 100% only on scholarly entity classes after fail-closing
catalog noise.
