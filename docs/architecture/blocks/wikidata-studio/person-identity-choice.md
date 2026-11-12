# Source-scoped person identities

A pooled person can contain different people with similar names.
A global QID change can assign the wrong person to unrelated manuscripts.
Rule W-320 keeps a reviewed choice within its exact source scope.

`POST /runs/{run_id}/wikidata-studio/items/{local_id}/identity-choice`
requires normal write access and current item approval.
The request contains `expected_source_fingerprint`, `expected_current_qid`,
`target_qid`, and exact `control_numbers`.
The generic item PATCH cannot supply `person_identity_choice`.

The endpoint verifies the following facts:

- The canonical source fingerprint and curator-visible public target match the request.
- The native person has one active NLI authority identifier in the 987 namespace.
- The public target has Q5 and that exact active P8189.
- A complete exact-identifier search returns only that target.
- Each source record belongs to this run and this native person.
- Each source record attests the exact name in MARC 100, 600, or 700.

The name comparison permits a single catalogue family-name comma.
It never reverses a patronymic or removes an uncertainty mark.
The proof retains the public revision, entity digest, source URI, original labels,
aliases, original records, and hashes of the source identity fields.
The source hashes include the dates and roles in those MARC fields.
The endpoint rechecks the canonical source after the public lookup.
It records the project event before the row change in the same transaction.
The proof records the cached projection QID separately from a ledger-only visible QID.
The builder accepts only those two exact prior states.
An existing choice requires a separate review; this endpoint does not silently replace it.

Migration `0054_person_identity_choice` adds the nullable JSONB field.
`wikidata_person_identity_choice.py` validates and applies the proof.
The final canonical projection checks the source fingerprint and identity hashes.
It retains only the reviewed records and proven P31/P8189 claims with separate source blocks.
It fails if a chosen person disappears or an incoming local relation exceeds the reviewed scope.
A source change requires review; it must never restore the pooled global target.

The public person remains reference only.
The uploader skips its public write and its global ledger change.
Reconciliation respects the choice before the old pooled key.
Test mode never receives the chosen public QID as a target.
The table counts a valid current choice as reference only.
An unknown earlier upload outcome remains unknown until independent recovery.

The build fingerprint includes a nonempty choice.
An empty choice preserves the previous fingerprint contract.
Cached content becomes stale after a choice, so the operator must rebuild before upload.
Project export and history retain the proof.
A history revert clears the choice when the target revision predates it.
A reverted proof must still pass the current source checks before a build succeeds.

Tests: `test_wikidata_person_identity_choice.py` and
`test_hmo_canonical_wikidata.py` cover source changes, patronymics, source scope,
public ambiguity, reference blocks, both namespaces, and global ledger isolation.

## Bounded item reads (W-321)

Single-item reads use the SQL read-model with an exact local ID.
The detail path retains statements and identity evidence for that one item.
The raw choice path also selects one payload from the SQL read-model.
Neither path reads the full cache blob when the item rows exist.
Old builds can use the existing one-time row backfill.
The table retains a small `reference_only` flag and omits the full private choice proof.
The full detail and export retain the proof for exact outcome recovery.
