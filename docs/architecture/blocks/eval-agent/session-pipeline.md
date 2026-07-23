# Eval-Agent AI Verification System — Session pipeline & channels

> Up: [Eval-Agent AI Verification System](README.md)

## Shared session pipeline (all channels)

```
curator picks action + scope + tier-1 judge (never types a prompt — agent_actions registry)
   │
   ▼
POST …/ai-verify/start-stream        (or POST /runs/{id}/jobs for background)
   │  short-lived session_scope(): access check, fetch scope rows,
   │  resolve tier_model via judge_models registry + unwrap provider credentials
   │  (Gemini: user Settings key or GEMINI_API_KEY; Qubrid: server QUBRID_API_KEY)
   ▼
_…_event_stream generator
   1. pre-check inference_cache (Redis L1 → Postgres L2, kind=ai_verdict)
      → pre_cached / uncached / (authority only) unverifiable-no-id split
   2. emit session.start  {session_id, action_id, scope_size, goal, cache_hits}
   3. emit agent.verdict for each pre-cached hit  (from_inference_cache: true)
   4. if uncached: write filtered fixture into sessions/<sid>/pipeline-output/
      and spawn `python -m eval_agent.cli run --pipeline-output … --state-dir <per-run>
      [--tier-model <id>]` (provider env keys injected — never `--api-key` argv)
      → stdout [STEP]/[STATS]/[TRACE] lines become runner.step / agent.stats /
      agent.verdict (each judged row is emitted as `[TRACE] {"type":"agent.verdict",…}`
      during the loop — do not wait for `results.jsonl`)
   5. finally: read state_dir/runs/<latest>/results.jsonl for persistence and for
      any verdicts not already streamed (skip re-yield when TRACE already emitted),
      persist summaries to the owning DB rows, write-through inference_cache,
      emit session.end {outcome, cache_hits, fresh_verdicts, uncached_skipped}
   │  every event is also appended to sessions/<sid>/trace.jsonl (audit + replay)
   ▼
sse_stream(): "event: <type>\ndata: <json>\n\n" + ": keepalive" every 15 s
```

Background job path (`POST /runs/{id}/jobs` with a `*_verify` kind):
`run_verify_job` (`verify_job.py`) re-opens the same generator, appends each
event to `collected_events`, and on every progress tick writes a partial
`progress.session_snapshot` (same `{session_id, run_id, events, verdicts}`
shape as the terminal snapshot) so `useVerifyJob` can render `VerdictsTable`
live across Heroku dynos without reading the worker's `/tmp` trace.

`spawn_eval_agent_run` (`agent_runner.py:153`) drains stderr concurrently
(deadlock + error-visibility), kills the child after 180 s of total silence
(`_SUBPROCESS_IDLE_TIMEOUT_S`), terminates it on consumer cancellation, and
surfaces non-zero exits as `runner.error` with the stderr tail. If
`locate_eval_agent()` fails (dyno without the bundle), uncached rows produce a
`runner.warning` and `outcome: "partial"` instead of a crash — cached verdicts
still stream.

## The five channels

| Channel dir | Router / stream | Scope unit | Fixture files | Verdict persisted to | Background job kind |
|---|---|---|---|---|---|
| `ai-verify-sessions` | `ai_verify.py` | `AuthorityMatch` (+ MARC record) | `marc_extracted.json` + `authority_enriched.json` | `AuthorityMatch.payload.ai_verdict` | `authority_verify` (retired compatibility) |
| `extraction-verify-sessions` | `extraction_verify.py` | `ExtractionApproval` | above pair + `ner_results.json` (entities + `ml_genres`) | `ExtractionApproval.ai_verdict` + `ai_verdict_at` (Rule W-17) | `ner_verify` |
| `wikidata-verify-sessions` | `wikidata_studio.py` | built Studio item | `marc_extracted.json` + `wikidata_items.json` | inference_cache only (items are rebuilt artefacts) | `wikidata_verify` |
| `hmo-item-verify-sessions` | `hmo_studio_items.py` → `hmo_item_verify.py` | merged HMO item | `marc_extracted.json` + `hmo_wikibase_items.json` | `HmoStudioItemOverride.ai_verdict` + `ai_verdict_at` | `hmo_item_verify` |
| `hmo-schema-verify-sessions` | `hmo_wikibase_schema.py` → `hmo_schema_verify.py` | schema bootstrap entry | `hmo_wikibase_schema.json` (+ empty `marc_extracted.json`) | inference_cache only | — (SSE only) |

Channel quirks:
- **Authority** peels off matches with no Mazal/VIAF/Wikidata/KIMA id and emits a
  synthetic `abstain` (`judge_id: "system:no_authority_id"`) so judged count ==
  scope size (`ai_verify.py:832`). Also `/results` + `/export` endpoints read the
  on-disk `results.jsonl` with server-side `q`/`overall` filtering.
- **NER** passes `threshold=-1.0` (negative, NOT `0.0` — eval-agent treats `0.0` as
  falsy and would fall back to its 0.85 default) so hand-picked low-confidence
  entities are still judged. Cache keys are content fingerprints
  (`ner_verdict_query_summary`: cn/source/span/text/type/role/judge_model +
  schema-version salt); `sanitise_stale_ai_verdict` drops a stored verdict whose
  `cache_key` no longer matches the current row content.
- **Wikidata / HMO item** `autofix_from_wikidata`-style actions filter to items with a
  live QID and enrich them with live Wikidata rows before judging
  (`_prepare_wikidata_verify_scope`); the live fingerprint enters the cache key.
- **HMO item/schema** do the pre-cache split in the *router* and pass
  `pre_cached`/`uncached_items` into the stream; authority/NER split inside the
  generator. Item fixtures carry `control_numbers` + `entity_type`; ingest
  `enrich_control_numbers()` fills gaps via `deferred_links`; `session.py`
  merges MARC across all `control_numbers` via `marc_extract.merge_records()`
  (R19 / Rule W-50). Schema writes only the
  uncached entries into the fixture — writing
  the full report made the web-tier cache pointless. Schema fixtures are
  enriched with `description`, `aliases`, `property_kind`, and `range_uri` from
  `ontology_schema_reader.schema_entry_metadata_by_uri()` before judging; cache
  keys in `schema_verdict_query_summary` include those fields so prompt/rubric
  fixes do not warm-hit stale verdicts (R17 / Rule W-47).
