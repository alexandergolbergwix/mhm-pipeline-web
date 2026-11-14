# Creator-only Wikidata writes

> Up: [Wikidata Studio](README.md) · Rule W-324

Only the authenticated account that created an entity can update that entity.
The user made this policy unconditional on 12 November 2026 after a community report about duplicate persons.
This policy supersedes foreign-edit exceptions in W-99, W-221, W-270, W-302, and W-313.

## Write contract

The uploader checks the authenticated account and the first-revision author.
The author must match the account before the build and immediately before the write.
The normal ownership check must also pass.
A cached owned classification cannot replace this creator proof.
An absent account, unknown creator, or different creator blocks the update.
P3959, a ledger entry, curator approval, and a previous successful job do not grant permission.

`register_foreign_accept` ignores legacy approvals.
Saved approval fields remain readable for compatibility. They cannot authorize an edit.
The item override API rejects a new foreign approval with HTTP 422.
The bulk preview and both upload passes enforce the same policy.
A foreign item cannot receive a duplicate CREATE as a substitute for its blocked update.

Publication plans block foreign updates even with exact historical consent.
The gateway independently checks the creator before every send, including a confirmed CSRF retry.
It also compares the actual write target with the planned target.
Old plans and queued jobs cannot bypass these checks.
The owner-only path caches creator proof only after its authenticated revision and identity checks pass.
The historical repair executor shares the uploader guard.

## Reuse and exports

Existing foreign QIDs remain valid statement targets after the normal identity checks.
An explicit reference-only choice remains available.
The UI has no control that grants permission for a foreign edit.
QuickStatements has no authenticated creator proof, so it emits no commands against an existing QID.
QuickStatements can still refer to that QID from a new item.
Use the guarded uploader for updates to owned items.

## Files and tests

- `backend/converter/wikidata/uploader.py`: creator proof and final write guard.
- `backend/converter/wikidata/quickstatements.py`: existing targets receive comments instead of edit commands.
- `backend/app/pipeline/wikidata_existence.py`: legacy approval helper always returns false.
- `backend/app/pipeline/wikidata_upload.py`: preview and deferred-pass policy.
- `backend/app/publication/core.py`, `runtime.py`, `wikidata_gateway.py`: plans, public consent fields, and final sends.
- `backend/app/routers/wikidata_studio.py`: rejects new foreign approvals.
- `WikidataItemDetailDrawer.tsx`, `WikidataPublicationControls.tsx`, `WikidataBlockedActionDrawer.tsx`, `WikidataPublicationAiReview.tsx`: creator-only guidance and reuse.
- `backend/tests/unit/test_wikidata_creator_only.py`: stale approvals, invalid cached ownership, unknown accounts, and safe QID references.

The existing ownership, upload, Publication, QuickStatements, and UI suites also cover the revised policy.
`backend/tests/test_wikidata_quickstatements_download_safety.py` and `backend/tests/unit/test_wikidata_quickstatements_safety.py` check creator-safe downloads and command omission.
Local tests do not establish deployment or a public cleanup.

The shared converter changes are explicit web divergences. Port these guards upstream before a full converter sync.

Validation and known limits: [12 November report](../../../audits/creator-guards-and-metadata-2026-11-12.md).
