# Foreign P31 ranks: read-only audit

All four live items carry normal-rank codex and manuscript claims.
All four native items propose a preferred codex claim and a normal manuscript claim.
The current generic rank check correctly refuses this difference before the foreign reference merge.
The examples do not contain an incoming normal assertion against an existing preferred assertion.

The live API read has UTC timestamp `2026-11-12T00:10:21Z`.
The native witness is `state/gila-recheck-2026-11-11/supported-studio-items.json`.

| QID | Source CN | Live revision | Live codex rank | Native codex rank |
|---|---|---:|---|---|
| Q134619072 | 990000533850205171 | 2565033700 | normal | preferred |
| Q134925976 | 990000558270205171 | 2565033809 | normal | preferred |
| Q134624941 | 990000655210205171 | 2565035665 | normal | preferred |
| Q134605231 | 990000688750205171 | 2565036143 | normal | preferred |

The exact revision values also appear in the fixture. The operator must read each item again before a write.

The relevant code is:

- `backend/converter/wikidata/manuscript_projection.py`, lines305–320: the generator assigns preferred rank to a specific manuscript class.
- `backend/converter/wikidata/uploader.py:_build_wbi_item`: the uploader builds native claims, then compares full rank-aware identities.
- `_claim_identity`: the identity includes the main snak, qualifiers, and rank.
- `_owner_claim_identity`: the identity includes the main snak and qualifiers, without the rank or references.
- `_owner_snak_identity`: the identity includes the complete typed value. Time and quantity precision remain part of that value.
- `register_foreign_accept`: the approval binds to one exact live QID.

The generic check at the proposed/existing identity comparison raises `conflicting P31 rank`.
The later foreign branch merges source blocks only when the full identity already matches.
Therefore, the reference merge never receives these four native codex assertions.

The product policy must preserve a community rank for an exact accepted-foreign P31 assertion.
Apply that policy before the generic comparison. Keep the generic comparison unchanged.
Limit the policy to an accepted foreign manuscript QID and P31. Exclude owner-only, owned, test-wiki, and unaccepted paths.
Use the built WBI snaks. A value-only or label-only comparison is insufficient.

The policy must require one exact main-snak and qualifier match.
It must preserve that active claim's GUID, rank, qualifiers, and complete source blocks.
It must add only distinct incoming source blocks to that existing claim.
It must not append a second assertion merely because the generator prefers another rank.
It must not change the native source object in place.

Only normal and preferred ranks can enter this rank-preservation policy.
An incoming deprecated assertion must retain the ordinary refusal against an active assertion.
An existing deprecated assertion must not supply a rank for an incoming active assertion.
Multiple exact unranked matches must not cause an automatic choice between community claims.
The generic duplicate, malformed metadata, qualifier, and other-property rank checks remain active.
Different qualifiers or precision do not identify the same assertion.

The fixture runs the current uploader against in-memory copies of all four live items.
The unchanged native P31 statements reproduce the exact refusal in all four cases.
A second pass changes only the fixture's effective codex rank to the existing normal rank.
That pass succeeds in all four cases. It adds the source blocks and preserves every existing rank and GUID.
It also preserves all existing source blocks, other claims, labels, descriptions, aliases, and sitelinks.
No login or remote write occurs.

The evidence file is `state/gila-independent-samples-2026-11-11/blocked/foreign-P31-rank-preservation-fixtures.json`.
It contains exact before/after P31 claims, complete source blocks, the public revisions, native indices, and source hashes.
It also contains negative cases for deprecated ranks, ambiguous matches, qualifiers, and the other upload paths.
The parent task owns the production change, tests, documentation sync, deployment, and live retry.

## Production result

The code fix passed its regression tests and deployed in commit `eb76b49e`.
Heroku release `v793` and Modal use that same backend.
The twelve approved foreign manuscripts then completed their normal live updates.
The complete public readback preserves every previous claim, rank, qualifier, complete reference block, and metadata value.
The preservation check reports twelve verified entities and zero issues.
The [final audit](gila-live-update-recheck-2026-11-11.md) records the job IDs and source comparison.
