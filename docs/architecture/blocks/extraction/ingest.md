# AI Extraction — Ingest

> Up: [AI Extraction](README.md)

## How it works — 1. Ingest (`marc_ingest.py`)

`parse_marc_upload(raw, filename=...)` dispatches on suffix, with
content-sniffing fallback (`marc_ingest.py:37-65`):

- **`.mrc` / `.marc`** — parsed via the vendored desktop
  `converter.parser.unified_reader.UnifiedReader` + `extract_all_data`, so
  every desktop MARC field handler runs identically (`_parse_mrc_via_desktop`).
- **`.json` / `.jsonl`** — already-parsed record dicts (desktop
  `marc_extracted.json` shape).
- **`.tsv` / `.csv`** — one record per row; headers matched
  case-insensitively; multi-value cells split on `|` / `;` / `<--SEP-->`.

`_normalise_records` guarantees a `_control_number` and, when raw
`<tag>$<sub>` keys are present (NLI-style exports), calls
`_collapse_marc_subfields` (`marc_ingest.py:244`), which derives the flat keys
the rest of the pipeline reads:

- `title` (245$a+$b), `authors` (100/110/111 with $e roles + $d dates),
  `contributors` (700/710/711/800/810/811), `subjects` (600/610/611/650/651
  with $0 authority ids), `genres` (655), `place` (260/264$a, 751 fallback),
  `dates` (260/264$c only — **never 008**; contract in `marc_date_sources.py`).
- **NER inputs**: `notes` (500/590/541$a), `provenance` (561$a),
  `contents` (505$a split on `--`), `colophon_text` (590$a plus
  keyword-detected 500$a: קולופון / colophon / כתב יד סופר).
- **Structured colophon**: `_extract_colophon_fields` pulls `colophon_year`
  (Hebrew gematria via desktop `converter.transformer.gematria`, or a
  Gregorian 4-digit fallback) and `colophon_scribe` (patronymic בן / ב"ר
  regex).
- **Work mentions**: `_extract_work_mentions` reparses raw 500$a on every
  preparation. A trigger must begin the note or follow a manuscript noun;
  quoted spans are preferred, and unquoted lists split only at semicolons or
  recognised title heads. Each row retains `source_field`, `source_text`, and
  `candidate_kind`; stale 500-derived contents are removed before the fresh
  list is merged (Rules W-33/W-68).
- **Editorial metadata**: `_extract_editorial_metadata` → `editor_names`,
  `edition_statement`, `edition_features`.
- **Provenance events**: `_extract_provenance_events` builds
  `record["provenance_events"]` from 541 ($b acquisition place via
  `_city_from_address`, $a agent, $d date) and 583 ($j site, typed
  exhibition/conservation from $a), reusing desktop `FieldHandlers` helpers so
  the `.mrc` path and the collapsed-key path emit byte-identical events
  (Rule W-32). Idempotent — skips when the `.mrc` path already filled it.

`extract_named_entities(record)` (`marc_ingest.py:1018`) yields entity
candidates for the Authority block: contributors/authors/subjects, production
place (260), related places (751/752), one `<type>_place` place entity per
provenance event, provenance-institution corporates, `work_mentions` as
`kind="work", role="contained_work"`, and editors. A final dedup pass keys on
`(normalize(text), kind)` with role-priority merge (author/scribe/editor 4 >
contributor 3 > subject 2 > production_place 1 > place 0); losing roles are
recorded in `alt_roles`. Different kinds with the same text are NOT collapsed.

`prepare_record_for_pipeline(rec)` (`marc_ingest.py:827`) must be called by
every consumer that reads `run_records.marc` straight from the DB — it
re-collapses legacy subfield-key rows and normalises genres/subjects/places.
