# AI Extraction — Skills

> Up: [AI Extraction](README.md)

### Skill: deploy / redeploy the Modal app
1. Edit `modal/modal_app.py` (or the vendored desktop `ner/` code it copies).
2. `cd modal && modal deploy modal_app.py` (or the `/deploy-modal` skill).
3. Copy the printed URL; if it changed:
   `heroku config:set MODAL_NER_URL=https://<workspace>--mhm-ner-mhmner-web.modal.run`.
4. Smoke: `curl <url>/health` — all five flags true. Debug cold-start issues
   with `modal app logs mhm-ner`.
5. Adding a model/weight: append to `WEIGHTS_TO_BAKE` (repo id + files),
   keep `_bake_weights` before the `add_local_dir` steps, load it in
   `@modal.enter`, expose it in `/health`.

### Skill: switch the extraction backend
1. `heroku config:set EXTRACTION_MODE=modal|hf-api|local` (plus
   `MODAL_NER_URL` for modal). Dev default is `local`.
2. Per-run override: pass `?mode=` to `start-stream` or `mode` in job params —
   explicit argument beats the env var (`resolve_mode`).
3. Verify: `GET /runs/{id}/extraction/status` reports `extraction_mode`
   before any run; the SSE `extraction.start` event carries the resolved mode.

### Skill: add a MARC field to ingest
1. Add the collapse logic in `_collapse_marc_subfields`
   (`marc_ingest.py:244`) — read `<tag>$<sub>` via `_str` + `_split_multi`,
   write a flat key. Keep it idempotent (guard on the flat key already set).
2. If it should yield entity candidates, extend `extract_named_entities` and
   check the dedup role priority table.
3. If the Exists-in badge should see it, add the key to `_STRUCTURED_KEYS`
   and map candidate types in `_TYPE_TO_FIELDS`
   (`marc_structured_index.py:72,102`).
4. If the `.mrc` path needs it too, the change belongs in the desktop
   `converter/transformer/field_handlers.py` and is synced via
   `pipeline/scripts/sync_converter_to_web.sh`.
5. Extend `backend/tests/unit/test_marc_ingest_ner_inputs.py` (or the
   colophon/notes/provenance-events suites).

### Skill: add a review-UI capability
1. One component per capability under
   `frontend/src/components/extraction/`; update the Rule W-16 list in
   CLAUDE.md.
2. Data flows via `useApprovalStore` polling — listen for
   `mhm.entities.refreshed`, never prop-drill the entity list.
3. Respect Rule W-36 (primitive Zustand selectors, `useReportDerivedIds`).
4. Every click path gets a Playwright spec in `frontend/e2e/` with mocked
   backend routes (Rule W-19) that asserts the captured request shape.

### Skill: debug empty extraction (0 entities)
1. Check the SSE/job progress for `extraction.model.unavailable` notes —
   `MODAL_NER_URL unset`, health-check failure, or "disabled by user".
2. Inspect one `run_records.marc` row: if it still has raw `100$a`-style
   keys and no `notes`/`provenance`/`contents`, the collapse didn't run —
   the defensive re-collapse in `start_extraction_stream` /
   `run_extraction_job` should fix it on the next run (R6).
3. `curl $MODAL_NER_URL/health`; if cold, expect the "warming" phase to take
   30–60 s — the UI surfaces this via the `extraction.step` warming event.
4. Cached empty results? Re-run with `skip_cache=true`.
5. Missing HF token → the route 400s with a Settings → Credentials pointer.

### Skill: audit full MARC mapping coverage

Run the streaming audit against the production-scale TSV before changing
projection code:

```bash
cd backend
.venv/bin/python -m scripts.audit_mapping_coverage \
  /path/to/filtered_manuscripts.tsv --sample-build 1000 \
  --json /tmp/marc-mapping-coverage.json
```

The gate requires zero normalization errors and zero `unmapped_tags`. Tags
reported as `evidence_only` remain available to catalog review but are not
promoted to speculative public Wikidata claims.
