"""Plan checks and durable report views shared by the route and worker."""
import uuid
from pathlib import Path
from sqlalchemy import select
from app.models.publication import Publication, PublicationPlan
from app.models.run_job import RunJob
from app.publication.credentials import configured_publication_gateway_factory
from app.publication.runtime import PublicationRuntime
from app.schemas.publication import PublicationSummaryQuery
from app.schemas.publication_ai_review import PublicationAiReviewState

KIND = 'wikidata_publication_ai_review'


def review_version():
    from app.pipeline.agent_runner import locate_eval_agent
    from app.publication.digests import canonical_digest
    root = locate_eval_agent()
    return canonical_digest({
        'evidence_reader': Path(__file__).with_name('automatic_evidence.py').read_text(),
        'judge_runner': Path(__file__).parent.parent.joinpath('pipeline/wikidata_publication_ai_review_job.py').read_text(),
        'automatic_worker': Path(__file__).parent.parent.joinpath('pipeline/wikidata_publication_auto_job.py').read_text(),
        'automatic_policy': Path(__file__).with_name('automatic_policy.py').read_text(),
        'automatic_projection': Path(__file__).with_name('automatic_projection.py').read_text(),
        'schema': (root / 'config/schemas/verdict.v2.json').read_text(),
        'rubric': (root / 'config/rubrics/wikidata_publication_review.md').read_text(),
        'evaluator': (root / 'eval_agent/evaluators/wikidata_publication_review.py').read_text(),
    })


async def checked_plan(db, run_id, publication_id, plan_id, plan_digest, actor_id):
    summary = (await PublicationRuntime(session=db, gateway_factory=configured_publication_gateway_factory).read(
        run_id=run_id, publication_id=publication_id,
        query=PublicationSummaryQuery(type='summary'), actor_id=actor_id,
    )).publication
    plan = summary.plan
    if (not summary.source_current or plan is None or plan.plan_id != plan_id
        or plan.plan_digest != plan_digest or summary.execution is not None
        or summary.approval_set is None or summary.approval_set.status != 'approved'
        or plan.approval_set_id != summary.approval_set.approval_set_id
        or plan.release_digest != summary.current_release.release_digest):
        raise ValueError('The AI review requires the current Plan and approved Release.')
    return await db.get(PublicationPlan, uuid.UUID(plan_id))


async def latest_job(db, run_id, publication_id, actor_id):
    publication = await db.get(Publication, uuid.UUID(publication_id))
    if publication is None or publication.run_id != run_id:
        raise ValueError('Publication not found')
    return (await db.execute(select(RunJob).where(
        RunJob.run_id == run_id, RunJob.kind == KIND, RunJob.created_by == actor_id,
        RunJob.params['publication_id'].as_string() == publication_id,
        RunJob.params['plan_id'].as_string() == str(publication.latest_plan_id),
    ).order_by(RunJob.created_at.desc(), RunJob.id.desc()).limit(1))).scalar_one_or_none()


def job_view(job):
    if job is None:
        return PublicationAiReviewState()
    progress = job.progress or {}
    if (job.params or {}).get('review_version') != review_version():
        return PublicationAiReviewState(job_id=str(job.id), status='failed',
            error='The AI review rules changed. Start a fresh AI review.')
    return PublicationAiReviewState(job_id=str(job.id), status=job.status,
        phase=progress.get('phase'), message=progress.get('message'),
        processed=progress.get('processed', 0), total=progress.get('total', 0),
        report=(job.result or {}).get('report') or progress.get('report'), error=job.error)
