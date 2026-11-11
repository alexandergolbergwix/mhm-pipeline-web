# Job payloads

**R42 — Summary lists do not load result JSONB (W-306).**
  `GET /runs/{run_id}/jobs?include_result=false` defers `RunJob.result`.
  It returns `result: null` and omits bulk detail from progress.
  The default preserves the existing result contract.
  A selected job GET retains its detail.
  *Why:* the owner preview requested 52 complete jobs in a 15.8 MB response.
