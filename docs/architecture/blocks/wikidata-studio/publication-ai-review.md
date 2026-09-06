# AI review of blocked Publication items

> Up: [Wikidata Studio](README.md)

## User flow

1. Select a Tier-1 judge.
2. Click **AI review blocked items**.
3. Read the report and its evidence explanations.
4. Click **Approve AI recommendations (N) and check again** only after review.

The final button submits exact consents and requests a fresh dry-run.
It does not publish items. Uncertain items stay blocked.
The checkbox **Override AI cache (fresh review)** repeats the AI review.

## API and persistence

`GET` and `POST /api/runs/{runId}/wikidata-publications/{publicationId}/ai-review`
read or start an advisory report. POST requires editor access.
The request binds `plan_id` and `plan_digest` and selects `tier_model`.
The route returns immediately after it creates `wikidata_publication_ai_review`.
The generic job route cannot create this job kind.

The worker uses the verify admission slot. It checks account and encrypted grant scope.
It saves the partial report after every item. The job result stores the final report.
The page restores the report after refresh and polls active jobs.
Cancellation stops the subprocess. A restart can reuse saved item progress for the same job.
Reports support at most 500 blocked actions per Plan.
Generic job messages omit the report to keep Postgres notifications small.

## Evidence and gates

Each foreign QID requires a fresh target document with the Plan's remote revision.
The fixture contains the sealed proposal, remote document, target site, QID, revision, and evidence references.
The subprocess uses the fixed `wikidata_publication_review` evaluator and its rubric.
It must compare identity and every proposed change. The evidence is untrusted data.
Only a valid `full` verdict with `name_ok=yes` and `type_ok=yes` offers consent.
Missing evidence, changed revisions, incomplete verdicts, and provider errors stay blocked.
Unknown lookup actions receive at most two reconciliation attempts.
A successful retry still requires a fresh dry-run before publication.

The report binds the Publication, Plan digest, and Release digest.
Each recommended consent also binds the entity digest, QID, and remote revision.
The worker checks source and Plan currency at entry and completion.
The UI requires a completed matching report before bulk approval.
The existing dry-run and execution gates check the remote state again.
AI cannot create a valid receipt or perform a Wikidata write.

## Cache

`cache_lookup_or_call(kind=ai_verdict)` stores each result with the full evidence and selected model.
The key includes evaluator code and rubric digests. Provider errors do not enter the cache.
A completed report for the same Plan, account, model, and review version can be reused.
An explicit override bypasses both report reuse and inference cache reads.

## Key files and tests

- `backend/app/routers/publication_ai_review.py`: access checks and queue entry.
- `backend/app/schemas/publication_ai_review.py`: request, report, and progress contract.
- `backend/app/publication/ai_review.py`: current Plan checks and report views.
- `backend/app/pipeline/wikidata_publication_ai_review_job.py`: remote evidence, subprocess, and persistence.
- `backend/app/publication/wikidata_gateway.py`: complete remote snapshots for review.
- `backend/app/pipeline/agent_actions.py`: fixed `review_publication_blocked` action.
- `eval-agent/eval_agent/evaluators/wikidata_publication_review.py`: evaluator.
- `eval-agent/config/rubrics/wikidata_publication_review.md`: identity and claim rules.
- `frontend/src/components/wikidata/WikidataPublicationAiReview.tsx`: report and approval control.
- `frontend/src/api/publication.ts` and `frontend/src/api/runJobs.ts`: API and job types.
- `backend/tests/test_publication_router.py`: supported, uncertain, changed, and error outcomes; restore, override, cancellation, and access.
- `backend/tests/unit/test_run_job_params_publication.py`: generic route denial.
- `eval-agent/tests/test_wikidata_publication_review.py`: exact evidence and prompt contract.
- `frontend/e2e/wikidata-publication.spec.ts`: report restore and explicit approval before dry-run.
