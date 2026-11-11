# Explicit extraction rejection

> Up: [AI Extraction](README.md) · Rule W-305

`ExtractionApproval.approved` and `ExtractionApproval.rejected` express three states.
Both false means pending. Only `approved` true means approved. Only `rejected` true means rejected.
The database constraint `ck_extraction_review_exclusive` rejects two true values.

Migration `0053_extraction_rejection` adds the non-null `rejected` field with a false default.
Old unapproved rows remain pending. The migration does not infer a lost curator decision.
The release must apply this migration before the new application serves requests.
This implementation does not apply the migration to production.

The drawer and bulk Reject action transmit `approved=false,rejected=true`.
An approval clears rejection. A false approval alone resets the decision to pending.
PATCH and bulk requests reject contradictory decisions before the database write.
The entity response, curator event, backend fingerprint, and frontend fingerprint include `rejected`.
Prediction refresh preserves both decision fields.

Auto-approve excludes rejected entities.
Its conflict update also checks rejection to protect a concurrent curator decision.
An explicit curator approval can replace rejection.

Section and project exports retain rejection.
Section import validates the state and updates the event and durable row together.
A legacy import without rejection preserves an existing rejection unless it explicitly approves the row.
A history revert projects both flags. A legacy false approval restores pending.

The local measurement script can read the production schema before this migration.
It defers the absent column and reports `legacy_schema_pending_compatibility`.
It does not classify an old false approval as rejection.

Tests: `backend/tests/test_extraction_rejection.py`, `test_history_router.py`,
and `frontend/tests/unit/extractionRejection.spec.ts`.
