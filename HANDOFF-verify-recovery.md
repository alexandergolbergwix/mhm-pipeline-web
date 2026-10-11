# HANDOFF — Wiki verify recovery + state (2026-10-11)

## Where things stand (verified numbers)

- **HMO Wikibase** (run `a6e1b67d-b520-4b4a-ba30-c647e8e20ea8`): **100% pass** — 375/375 judged full; 55 structural/paradigm entities unjudged by design. DONE.
- **Wikidata Studio** (run `3494ebf5-ceb0-4702-a94e-c8f10be7f251`): **BROKEN** — overrides hold 1850 abstain / 127 full / 11 partial / 2 fail. The good state was **1822 full / 18 partial / 13 fail** (98.3%) and is recoverable (see below).
- ~137 override rows are stale (items no longer in the current build) — safe to delete.

## Root-cause chain (all confirmed by measurement)

1. **Modal container → Wikidata API 403**: UA lacked contact info (WMF policy). Fixed: contact email in UAs in `app/pipeline/wikidata_entity_compare.py` (fetch_wikidata_entity) and `app/pipeline/wikidata_duplicate_probe.py` (_USER_AGENT). **Verified 200 from inside a Modal test container** (old UA → 403, contact UA → 200, see `/tmp/modal-nettest.py` pattern).
2. **parallel=6 verify poisoned the overrides**: 6 workers stacked into the probe/live-lookup throttle → API failures → 1850 abstain verdicts persisted to override rows (judge failures are never cached). The overrides table now holds the broken state.
3. **Cache warm-hits missed on the recovery pass**: my item-shape fixes (ISBD title splits, person label swaps, description drops, extent_text slice) changed the query fingerprints → the cache entries from the good 12:49 run no longer match → every item judged fresh → on Modal the fresh judgments hit the (still partially broken) evidence path → abstains again.
4. **Web-dyno OOM (R15, API-down window 18:56 Oct 9)**: a full-scope fresh verify ran its loop on the 512MB Basic web dyno. Fixed: `wikidata_verify` is now a Modal kind (`app/pipeline/modal_job_client.py` MODAL_JOB_KINDS + `modal/modal_jobs.py` dispatch + token allowlist).

## Recovery sequence (do in this order)

1. **Prove the evidence path from Modal** — fire one item's verify (scope = 1 known item, e.g. `QDraft_Person_46`) via the standard trigger pattern (`/var/folders/mf/hm8jx1hn7jb97vg2j1fn5vym0000gn/T/opencode/trigger-hmo-verify.py` is the template; the wiki variant imports `WikidataStudioCache` from `app.models.wikidata_studio_cache`). Confirm the verdict's `verify_evidence.wikidata_existing.live.labels` is non-empty (check the override row or the agent.answers trace event).
2. **Rebuild** the wiki studio build (`wikidata_studio_build` job, force_rebuild) — re-anchors fingerprints to the fixed item shapes (ISBD title splits, extent_text slice, person label swaps are all build-side).
3. **Verify full scope** (`trigger-verify2.py` template, override_cache=True). Expected result: back to ~1822 full / ~30 non-passing.
4. **The remaining ~30** are: ~6 duplicate `candidates_found` (real curator link decisions — candidates are our own earlier uploads, QIDs named in the reasoning), ~10 person-label noise, ~10 title/claims hedges. The automation shipped: the duplicate probe stamps candidates carrying our P2888 HMO bridge as `ours`, and single-candidate `ours` matches auto-adopt (any match type) — `tests/unit/test_duplicate_qid_adoption.py::test_an_ours_provenance_match_adopts_even_a_title_match`.
5. **Person-label noise rows**: convergence passes flip 1–2 per run; or curator one-clicks; or calibration countersign.

## Key invariants discovered this session (all deployed, commits bc0eaa2..f844ad6 + 033a0c0..4337520 lineage)

- `title_claim_accuracy` is axis-native (the certified name axis + ISBD-split evidence own it).
- The HMO hedge upgrade: neutralized `name_quality` (malformed needs mechanical confirmation; sub-gate generic_fallback is noise) + no mechanical findings → hedged axes upgrade in code.
- The wiki hedge ignores a model `no` on `existing_qid_identity` when the item updates a QID (identity settled by adoption).
- Person descriptions that are the entity's own name (any word order) drop — `_upload_descriptions` in `app/pipeline/hmo_canonical_wikidata.py` (word-set comparison) and `_descriptions_for_node` in `converter/wikibase/hmo_exporter.py` (skeleton comparison).
- Person labels swap to natural order in the exporter; unknown-scribe labels KEEP their `(MS …)` system-label suffix.
- Ghost persons (no control numbers, only HMO-structural claims P288/P153/P293) drop in `hmo_exporter.py` resolution.
- `_truncate` in `hmo_exporter.py` balances parens; comment hygiene strips middots, close-paren quote remnants, bare class-name fragments.
- Extent evidence: `marc_ingest` stamps `extent_text` (full cleaned 300$a); P1104/P2048/P2049/P2635 cite `(extent_text, extent)`.
- Work descriptions: "The work 'X' as recorded in manuscript Y." (not "F1 Work linked to…").
- `_evidence_window` in `wikidata_verify_evidence.py` quotes the window around the claim's own value when it sits deeper than the 400-char head, with word-anchor matching for separator variance.
- `verify_record_ids` (wikidata_verdict_cache.py) leads with the CN embedded in the local_id, caps judge state at 1 record.
- The parallel=6 lesson: **parallel judging workers must not stack into the live-lookup throttle** — parallelism is only safe when the evidence pack is precomputed (cache-first), or the throttle is shared. Either fix the shared throttle or keep parallel for cache-warm passes only.

## Deploys

- Heroku release ~v634+, Modal deployed after every eval-agent/backend change (last deploys: `2d534c0`, `179861f`, `7f33457`, `bc0eaa2` modal only). Current HEAD lineage ends at the test-suffix commit; paper/ is gitignored and stays local (updated with: rule manifest + hedge reconciliation, local-reproduction tuning step, execution-isolation + API-policy production policy items, the noise-floor limitation).
- `paper/paper.tex` compiles clean (10 pages); the paper/ dir is intentionally untracked (user's DHQ paper — never commit).

## Watch out

- The verify scope query in `trigger-verify2.py` reads non-passing from the OVERRIDES table — after a build that drops items, stale override rows re-enter scope. Join the cache/rows before scoping (the HMO trigger-hmo-fresh.py does this via `current` set).
- The temp trigger scripts live in `/var/folders/mf/hm8jx1hn7jb97vg2j1fn5vym0000gn/T/opencode/` (may be purged between sessions; the patterns are simple to recreate — see the file contents in this session's history).
- `EVAL_AGENT_FALLBACK_JUDGE=gemini-3.5-flash` is set on Heroku but NOT in the Modal secret — the escalation fallback inside Modal is still Kimi. To use Gemini as the fallback judge, add the env to the Modal secret `mhm-jobs2`.
