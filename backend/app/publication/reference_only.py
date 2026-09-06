"""Resolve explicit identity choices from a current, sealed Publication Plan."""
from __future__ import annotations

import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.publication import Publication, PublicationEntityRow, PublicationPlan
from app.models.publication import PublicationPlanAction
from app.publication.types import SourceSnapshotRef, TargetRef
from app.schemas.publication import PreparePublicationRequest


async def resolve_reference_selection(
    db: AsyncSession, run_id: uuid.UUID, request: PreparePublicationRequest,
    snapshot: SourceSnapshotRef, target: TargetRef,
) -> dict[str, dict[str, object]]:
    selection = request.reference_only
    if selection is None:
        return {}
    publication = await db.get(Publication, uuid.UUID(selection.publication_id))
    plan = await db.get(PublicationPlan, uuid.UUID(selection.plan_id))
    if (publication is None or publication.run_id != run_id or plan is None
        or plan.publication_id != publication.id or publication.latest_plan_id != plan.id
        or plan.plan_digest != selection.plan_digest or publication.latest_execution_id is not None
        or publication.latest_release_id != plan.release_id
        or publication.latest_approval_set_id != plan.approval_set_id
        or publication.source_snapshot_id != snapshot.snapshot_id
        or publication.source_digest != snapshot.digest or publication.source_revision != snapshot.revision
        or publication.target_site != target.site or publication.target_environment != target.environment):
        raise ValueError("Reference-only choices require the current Plan, source, and target")
    keys = selection.entity_keys
    if len(set(keys)) != len(keys):
        raise ValueError("Reference-only choices cannot contain duplicate entity keys")
    actions = (await db.execute(select(PublicationPlanAction).where(
        PublicationPlanAction.plan_id == plan.id, PublicationPlanAction.entity_key.in_(keys),
    ))).scalars().all()
    if len(actions) != len(keys):
        raise ValueError("A reference-only item is absent from the current Plan")
    result: dict[str, dict[str, object]] = {}
    # Preserve prior choices when the curator adds more reference targets.
    prior = (await db.execute(select(PublicationEntityRow).where(
        PublicationEntityRow.release_id == plan.release_id,
        PublicationEntityRow.document['publication_reference_only'].isnot(None),
    ))).scalars()
    for entity in prior:
        reference = entity.document.get('publication_reference_only')
        if isinstance(reference, dict):
            result[entity.entity_key] = dict(reference)
    for action in actions:
        entity = await db.get(PublicationEntityRow, (plan.release_id, action.entity_key))
        if (entity is None or entity.entity_digest != action.entity_digest
            or action.observation_status not in {'present_owned', 'present_foreign'}
            or not action.target_qid or not action.target_revision):
            raise ValueError("Reference-only use requires an observed existing QID and exact entity digest")
        result[action.entity_key] = {'qid': action.target_qid, 'remote_revision': action.target_revision,
            'source_entity_digest': action.entity_digest, 'source_plan_id': str(plan.id)}
    return result
