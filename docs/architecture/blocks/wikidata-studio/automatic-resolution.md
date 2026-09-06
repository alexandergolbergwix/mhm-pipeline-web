# Automatic Publication resolution

> Up: [Wikidata Studio](README.md)

## User flow

1. Open the current approved Release and its dry-run Plan.
2. Select the assessment model and the verification model.
3. Click **Resolve Release automatically**.
4. Open the prepared Release after the job completes.
5. Inspect its receipt before the separate publication action.

No item-by-item human approval is required in automatic mode.
The policy approves only the retained subset and records `policy:reference-first-v1` as the actor.
It never supplies foreign-QID consent or performs a Wikidata write.
Deferred items remain in the source corpus and report, outside the approved subset.
If every item is deferred, the job reports that no subset is ready.

## Evidence and decisions

The existing AI endpoint accepts `automatic: true` and optional `verification_model`.
The existing verify job slot runs `wikidata_publication_auto_job.py` for this mode.
The job covers the full Plan, with a limit of 500 entities.
It repeats duplicate checks and reads the exact remote QID and revision.

Primary evidence includes only the item's original imported MARC records, NLI authority rows,
and bounded HTTPS reads from the NLI, VIAF, LC, and GND host allowlist.
The source reader refuses redirects and uses size and time limits.
It never substitutes another record or treats an HMO mirror as independent evidence.
External reads use the shared cache. Evidence records carry source IDs and digests.

Two independent prompts assess identity and each proposed statement.
The verification model defaults to the selected model; the curator can select another model.
The verifier does not receive the first decision.
The evaluator exports optional `publication_decision` in the verdict schema and cache.
The backend validates the structured decision and its cited primary evidence IDs.
Invalid structured output never enters the verdict cache. The job retries a failed judge call once.

## Conservative policy

- `reuse_existing`: both checks agree on identity with primary evidence and a shared strong identifier.
  A work can instead use an exact title and verified author QID. A title alone is insufficient.
  The existing QID receives no updates, even when the proposed claims need changes.
- `create`: a complete duplicate check reports absence; both checks support labels and retained claims.
  The policy requires a supported P31. It removes unsupported statements.
- `defer`: any other result, including a conflicting identity, unavailable source, or provider error.
  A rejected candidate never becomes permission to create a duplicate.

If both checks reject a target, the job searches at most three replacement candidates on the same Wikidata site.
Each candidate requires fresh evidence and two checks. An empty search never authorizes creation.
The policy does not invent replacement identifiers, names, or bibliographical facts.
It does not enable automatic updates of existing Wikidata items.
These limits can defer more items than a human curator would accept.
The report shows the distinction; a lower review count is not proof of data quality.

## Projection and persistence

The worker builds a new immutable Release from the sealed source documents.
It resolves references to reused QIDs in statement values, qualifiers, and reference values.
It defers statements that need unresolved or newly created targets.
It excludes deferred entities from the Release; their report and source records remain available.
Retained records preserve original and deferred statements for inspection.
The new Release uses a job-bound idempotency key for restart recovery.

The worker approves the subset as an explicit policy decision and creates a fresh dry-run.
New blockers cause automatic deferral and another subset, within a three-attempt budget.
The job never weakens the dry-run or execution checks.
It checkpoints each item and checks cancellation between external steps.
A normal retry reuses completed decisions and repeats retryable failures.
A cache override repeats the full assessment. Cache keys include evidence, model, schema, evaluator, and policy code.
A page refresh restores the report. The prepared Release link uses its exact Publication ID.

## Files and tests

- `backend/app/publication/automatic_policy.py`: typed decisions and deterministic action rules.
- `backend/app/publication/automatic_evidence.py`: original records and bounded external evidence.
- `backend/app/publication/automatic_projection.py`: subset and dependency handling.
- `backend/app/pipeline/wikidata_publication_auto_job.py`: durable assessment, subset, policy review, and dry-run.
- `backend/app/publication/runtime.py`: trusted internal automatic documents and prepare idempotency.
- `backend/app/routers/publication_ai_review.py`: mode, model credentials, cache reuse, and retry entry.
- `backend/app/schemas/publication_ai_review.py`: persisted decisions and report outcomes.
- `eval-agent/eval_agent/evaluators/wikidata_publication_review.py`: automatic prompts and structured decision export.
- `eval-agent/config/schemas/verdict.v2.json`: optional decision contract.
- `eval-agent/eval_agent/evaluators/_base.py` and `orchestration/session.py`: decision serialization and cache preservation.
- `frontend/src/components/wikidata/WikidataPublicationAiReview.tsx`: automatic controls and report.
- `backend/tests/unit/test_publication_automatic_policy.py`: identity, sources, claims, and fail-closed outcomes.
- `backend/tests/unit/test_publication_automatic_projection.py`: qualifiers and unsafe dependencies.
- `backend/tests/test_publication_router.py`: no-write subset, retry, cancellation, and source scope.
- `frontend/e2e/wikidata-publication.spec.ts`: report restore and absence of human consent requests in automatic mode.
