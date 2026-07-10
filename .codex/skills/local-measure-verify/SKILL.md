# Skill — local-measure-verify

Measure the true post-fix baseline of a Studio build/label/rubric change
**locally and read-only**, before deploying: rebuild a run's Studio items with
the current (undeployed) code in a scratch dir, re-judge a chosen scope of
items with an eval-agent tier-1 model (Qubrid Kimi K2.5 by default), and emit a
before/after verdict report. **Nothing is written to the database, the caches,
or the live wiki.**

Script: [`backend/scripts/local_measure_verify.py`](../../../backend/scripts/local_measure_verify.py)

## When to invoke

- You changed RDF build / label hygiene / an evaluator rubric (e.g. Rule W-53
  for HMO items, or a Wikidata Studio item fix) and want to know how many
  previously-`partial`/`fail` items now pass — **without** running the real
  curator-ops rebuild against production.
- The user asks to "re-verify locally", "measure the baseline", "run the eval
  only on the entities that didn't pass", or similar, for a specific run.

Do **not** use this to actually refresh production artifacts/verdicts — that is
the curator-ops path (`scripts/rebuild_run_rdf_and_items.py` +
`scripts/hmo_item_verify_fixup_loop.py --persist-verdicts`, or the Studio UI's
**Rebuild (skip cache)** + AI verify). This skill is measurement only.

## What it does (both channels)

1. **Build (read-only).** Reads the run's records / approved matches / approved
   NER / overrides from `DATABASE_URL` and builds the Studio items into a local
   scratch dir. No cache upsert, no `RdfArtifact` / `HmoStudioItemCache` write.
   - `--channel hmo`: `build_rdf_graph` → `HmoWikibaseExporter.from_ttl` →
     `resolve_against_mappings` (schema mappings read read-only) → SHACL.
     Evaluator `hmo_wikibase_item`.
   - `--channel wikidata`: `wikidata_studio.build_items_for_run` (already
     DB-free). Evaluator `wikidata_item`.
2. **Scope.** `--scope non-passing` (default) selects items whose *baseline*
   `overall` is `partial`/`fail`; `--scope all` takes everything; `--local-id`
   (repeatable) / `--limit` slice further. Baseline comes from
   `--baseline-export <export.json>` when given, else from the run's stored
   verdicts (read-only).
3. **Verify.** Writes a fixture (`marc_extracted.json` + the channel's items
   JSON, scoped) and runs `eval-agent run --linear --no-cache --no-self-verify
   --threshold -1 --tier-model <model>` against a local `--state-dir`.
4. **Report.** Writes `measure_report_<ts>.json` with the new verdict
   distribution, before→after transitions, and `now_passing` /
   `still_partial_or_fail` counts.

## Environment

- `DATABASE_URL` — Postgres (Heroku prod or local). Fetch prod read-only creds
  with `heroku config:get DATABASE_URL -a mhm-pipeline-web`.
- `QUBRID_API_KEY` (alias `QUABRID_API_KEY`) — Qubrid key; auto-falls back to
  `heroku config:get QUBRID_API_KEY -a mhm-pipeline-web`.
- `GEMINI_API_KEY` — only for a Gemini `--tier-model`.
- A repo-root `.env` is loaded when present.

## Recipes

Measure HMO post-fix baseline on the previously-failing scope (run 48ba6c13):

```bash
cd backend
export DATABASE_URL="$(heroku config:get DATABASE_URL -a mhm-pipeline-web)"
export QUBRID_API_KEY="$(heroku config:get QUBRID_API_KEY -a mhm-pipeline-web)"
.venv/bin/python -m scripts.local_measure_verify \
  --channel hmo --run-id 48ba6c13-115c-4763-bff1-c08b9031b518 \
  --baseline-export "$HOME/Downloads/run-48ba6c13-…-hmo-wikibase-items (5).json" \
  --scope non-passing
```

Quick 5-item pilot (the RDF build still runs in full):

```bash
.venv/bin/python -m scripts.local_measure_verify --channel hmo \
  --run-id 48ba6c13-115c-4763-bff1-c08b9031b518 --limit 5
```

Wikidata Studio, all items:

```bash
.venv/bin/python -m scripts.local_measure_verify --channel wikidata \
  --run-id <run_id> --scope all
```

## Extending to a new channel

Add a `Channel` subclass in `local_measure_verify.py` implementing
`build_measurement(run_id, scratch) -> (items, marc_records)` (read-only) and
`baseline_from_db(run_id) -> {local_id: verdict}`, set `name` / `evaluator` /
`items_filename`, and register it in `CHANNELS`. The verify + report core is
channel-agnostic and needs no changes.

## Invariants

- **Measurement only.** Never add DB writes (cache upsert, verdict persist) to
  this script — that would defeat its purpose and touch production. The
  write-back path is a separate, explicitly-confirmed operation.
- Scope defaults to previously-non-passing so a re-judge is cheap; `--threshold
  -1` forces every scoped item to be judged regardless of confidence.
- Verdict-cache salt invalidation is unchanged (Rule W-51); `--no-cache` here
  only forces a fresh judge for the measurement.

See also: `CLAUDE.md` Rule W-53 (HMO items) / W-46 (tier-1 models) / W-51
(content-addressed verdict caches).
