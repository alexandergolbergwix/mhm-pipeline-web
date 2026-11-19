"""Check older Publication attempts before a new exact reference repair."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.publication import (
    Publication,
    PublicationExecution,
    PublicationPlanAction,
    PublicationWriteIntent,
    PublicationWriteReceipt,
)

_LEGACY_TYPE_REJECTION = "'Bad value type wikibase-entityid, expected string'"


async def has_unresolved_prior_write(db: AsyncSession, qid: str, actor_id: str) -> bool:
    old = PublicationWriteIntent
    prior_execution = aliased(PublicationExecution)
    prior_publication = aliased(Publication)
    prior_action = aliased(PublicationPlanAction)
    newer = aliased(PublicationWriteIntent)
    newer_action = aliased(PublicationPlanAction)
    receipt = PublicationWriteReceipt

    # Old Studio runs classified this deterministic rejection as unknown.
    # Require a later confirmed update of the same entity at a newer base revision.
    resolved = (
        select(newer.id)
        .select_from(prior_execution)
        .join(prior_publication, prior_publication.id == prior_execution.publication_id)
        .join(prior_action, (prior_action.plan_id == prior_execution.plan_id)
              & (prior_action.entity_key == old.entity_key))
        .join(newer, (newer.target_qid == old.target_qid) & (newer.entity_key == old.entity_key))
        .join(PublicationExecution, PublicationExecution.id == newer.execution_id)
        .join(Publication, Publication.id == PublicationExecution.publication_id)
        .join(newer_action, (newer_action.plan_id == PublicationExecution.plan_id)
              & (newer_action.entity_key == newer.entity_key))
        .join(receipt, receipt.intent_id == newer.id)
        .where(
            prior_execution.id == old.execution_id,
            prior_execution.actor_id == actor_id,
            prior_publication.profile_name == "mhm-wikidata",
            prior_publication.target_site == "www.wikidata.org",
            prior_publication.target_environment == "production",
            old.state == "outcome_unknown",
            old.action == "update",
            old.detail == _LEGACY_TYPE_REJECTION,
            prior_action.action == "update",
            prior_action.target_qid == old.target_qid,
            newer.action == "update",
            newer.state == "succeeded",
            PublicationExecution.actor_id == actor_id,
            Publication.profile_name == prior_publication.profile_name,
            Publication.target_site == prior_publication.target_site,
            Publication.target_environment == prior_publication.target_environment,
            newer_action.action == "update",
            newer_action.target_qid == old.target_qid,
            newer_action.target_revision > prior_action.target_revision,
            receipt.created_at > old.updated_at,
            receipt.status == "succeeded",
            receipt.qid == old.target_qid,
            receipt.fingerprint == newer.result_fingerprint,
        )
        .correlate(old)
        .exists()
    )
    unresolved = await db.scalar(select(old.id).where(
        old.target_qid == qid,
        old.state.in_(("pending", "in_flight", "outcome_unknown")),
        ~resolved,
    ).limit(1))
    return unresolved is not None
