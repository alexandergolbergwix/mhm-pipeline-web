- `backend/tests/unit/test_verify_scope_progress.py` — scope preparation reports 1-based steps, nested `lookups` counts on the duplicate step, an unknown phase falls back to step 1, sub-counts never exceed their total, and the publisher writes only when the state changed (Rule W-147).
- `backend/tests/test_wikidata_publication_execution_job.py` and
  `backend/tests/unit/test_run_job_params_publication.py` — Publication jobs
  carry references only and cannot start through the generic jobs endpoint.
