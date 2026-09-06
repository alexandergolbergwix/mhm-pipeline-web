# Existing items without updates

> Up: [Wikidata Studio](README.md)

## User flow

1. Review the identity of a blocked item.
2. Click **Use QID without updates** only if both records describe the same item.
3. Review and approve the new Release.
4. Create a fresh Dry-run Receipt.

The old Release remains immutable.
The new Release retains the selected record and its source evidence.
Connections use its observed Wikidata QID.
The record has a `skip` action; the executor does not create or update it.
This choice applies to the new Release. It does not change the Studio source cache.
A later reference choice preserves the prior choices from its base Release.

## Contract and checks

The existing prepare endpoint accepts optional `reference_only`:
`publication_id`, `plan_id`, `plan_digest`, and `entity_keys`.
The background prepare job resolves QIDs from the saved Plan.
The client cannot supply an arbitrary QID or claim AI approval.
The Plan must match the run, latest Release, approval, source snapshot, and target.
An existing Execution prevents a new reference choice from that Plan.
Unknown or absent targets, duplicate keys, and mismatched entity digests fail.

The projection seals `publication_reference_only` in each selected document.
It contains the QID, remote revision, source entity digest, and source Plan ID.
The projection resolves connections before it defers unresolved claims.
It removes untrusted reference-only markers from the Studio cache.
The current source cannot override a selected QID with another QID.
The entity API exposes `reference_only` and the `skip` proposed action.

The dry-run checks each target again.
Only the same existing QID and remote revision produce `skip`.
A changed or unavailable target produces `block`, never `create` or `update`.
The SQL executor admits only `create` and `update` actions.
Reference records remain visible in the Release and Plan but have no write intent.

## Files and tests

- `backend/app/publication/reference_only.py`: current Plan and identity choice checks.
- `backend/app/publication/runtime.py`: projection, QID resolution, and entity read view.
- `backend/app/publication/core.py`: reference dry-run checks.
- `backend/app/publication/sql_repository.py`: exclusion of skip actions from Execution.
- `backend/app/schemas/publication.py`: prepare request and entity response.
- `frontend/src/api/publication.ts`: request and response types.
- `frontend/src/components/wikidata/WikidataPublicationControls.tsx`: explicit choice and counts.
- `frontend/src/components/wikidata/WikidataPublicationPanel.tsx`: prepare and restored record display.
- `backend/tests/test_publication_router.py`: projection, stale choices, wrong target, and SQL execution without a target write.
- `backend/tests/unit/test_publication_module.py`: same, changed, and absent reference targets.
- `frontend/e2e/wikidata-publication.spec.ts`: a new Release requires fresh approval.
