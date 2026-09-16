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
