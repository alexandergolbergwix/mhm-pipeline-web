- `backend/tests/unit/test_verify_scope_progress.py` — scope preparation reports 1-based steps, nested `lookups` counts on the duplicate step, an unknown phase falls back to step 1, sub-counts never exceed their total, and the publisher writes only when the state changed (Rule W-147).
- `backend/tests/test_wikidata_publication_execution_job.py` and
  `backend/tests/unit/test_run_job_params_publication.py` — Publication jobs
  carry references only, cannot start through the generic jobs endpoint, and
  pause the durable Execution after a worker failure.
- `backend/tests/test_publication_migrations.py` — the Publication action table
  contains the timestamp required by the claim query.

- `backend/tests/unit/test_job_modules_import.py` — imports every
  `app/pipeline/*_job*.py` module: a job runner that no test exercises
  (dispatch is lazy inside `_execute_job`) can no longer ship a syntax or
  import error that would surface raw interpreter messages to curators
  (2026-09-16: `invalid syntax (rdf_build_job.py, line 49)` on the RDF page).

- `backend/tests/unit/test_authority_re_enrich_progress.py` — the enrichment
  sweep emits throttled per-entity progress (W-113) and stops with
  `ReEnrichCancelled` as soon as the cancel flag is set (R28).

- `backend/tests/unit/test_modal_job_client.py` — dispatch/lease/wait
  semantics for Modal execution, including `test_run_on_modal_accepts_200_ok_body`
  (Rule W-243): the endpoint's real 200 `{"ok":true,"spawned":true}` counts as
  an accepted dispatch, a live executor lease blocks re-dispatch, and a stale
  lease is taken over. Rule W-254 pins the executor-heartbeat semantics:
  `test_owner_heartbeat_does_not_mask_dead_executor` (a stale
  `executor_heartbeat_at` is takeable even when the owner keeps `updated_at`
  fresh), `test_fresh_executor_heartbeat_blocks_redispatch`, and
  `test_owner_heartbeat_does_not_extend_wait_past_budget` (the poller's wait
  expires once the executor heartbeat is dead).
