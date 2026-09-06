"""Review blocked Plan actions without a public write or implicit consent."""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from sqlalchemy import select
from app.db import session_scope
from app.models.publication import Publication, PublicationPlanAction
from app.models.run_job import RunJob
from app.pipeline import agent_runner
from app.pipeline.inference_cache import cache_lookup_or_call
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress
from app.publication.ai_review import checked_plan, review_version
from app.publication.credentials import ExecutionCredentialResolver
from app.publication.digests import canonical_digest, thaw_json
from app.publication.sql_repository import SqlAlchemyPublicationRepository
from app.publication.wikidata_gateway import CurrentWikidataBoundaryFactory
from app.schemas.publication import PublicationForeignQidConsent
from app.schemas.publication_ai_review import PublicationAiReport, PublicationAiReviewItem

logger = logging.getLogger(__name__)
EVALUATOR = 'wikidata_publication_review'


class ReviewCancelled(Exception):
    pass


async def _cancel_check(job_id):
    if await is_cancel_requested(job_id):
        raise ReviewCancelled()


async def _judge(db, job_id, item, tier_model, api_key, force_refresh, actor_id):
    async def fetch():
        with tempfile.TemporaryDirectory(prefix='publication-ai-') as directory:
            root = Path(directory)
            fixture, state = root / 'fixture', root / 'state'
            fixture.mkdir()
            (fixture / 'wikidata_items.json').write_text(json.dumps([item], ensure_ascii=False))
            (fixture / 'marc_extracted.json').write_text('[]')
            exited = False
            async for event in agent_runner.spawn_eval_agent_run(pipeline_output=fixture,
                evaluators=(EVALUATOR,), api_key=api_key, tier_model=tier_model,
                state_dir=state, override_cache=True):
                if event.type == 'runner.exit':
                    if event.payload.get('return_code') != 0:
                        raise ValueError('The AI judge did not complete.')
                    exited = True
            if not exited:
                raise ValueError('The AI judge did not report completion.')
            # Read only this invocation; never accept the runner helper's legacy fallback.
            paths = list((state / 'runs').glob('*/results.jsonl'))
            rows = [json.loads(line) for path in paths for line in path.read_text().splitlines() if line.strip()]
            matches = [row for row in rows if row.get('record_id') == item['local_id'] and row.get('evaluator_id') == EVALUATOR]
            if len(matches) != 1:
                raise ValueError('The AI judge did not return one matching verdict.')
            result = matches[0]
            if result.get('error') or result.get('verification_status') != 'judged':
                raise ValueError('The AI provider did not return a valid review.')
            verdict = result.get('verdict')
            if (not isinstance(verdict, dict) or verdict.get('overall') not in {'full', 'partial', 'fail', 'abstain'}
                or not isinstance(verdict.get('reasoning'), str) or not verdict['reasoning'].strip()):
                raise ValueError('The AI judge returned an incomplete review.')
            return verdict

    rubric = agent_runner.locate_eval_agent() / 'config' / 'rubrics' / f'{EVALUATOR}.md'
    task = asyncio.create_task(cache_lookup_or_call(db, kind='ai_verdict', user_id=uuid.UUID(actor_id),
        query_summary={'evaluator': EVALUATOR, 'version': review_version(), 'tier_model': tier_model,
            'rubric': canonical_digest(rubric.read_text()), 'item': item},
        fetch=fetch, skip_cache=force_refresh))
    try:
        while not task.done():
            await _cancel_check(job_id)
            await asyncio.wait({task}, timeout=1)
        return await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def run_wikidata_publication_ai_review_job(job_id: uuid.UUID) -> None:
    report = None
    total = 0
    try:
        async with session_scope() as db:
            job = await db.get(RunJob, job_id)
            if job is None:
                return
            run_id = job.run_id
            params = job.params or {}
            if params.get('review_version') != review_version():
                raise ValueError('The AI review rules changed. Start a fresh AI review.')
            actor = str(params.get('actor_id') or '')
            publication_id = str(params.get('publication_id') or '')
            scope = str(params.get('credential_scope_id') or '')
            if job.created_by != uuid.UUID(actor) or not scope.startswith('ai-review:'):
                raise ValueError('The AI review account or scope is invalid.')
            await _cancel_check(job_id)
            plan = await checked_plan(db, run_id, publication_id, params['plan_id'], params['plan_digest'], actor)
            publication = await db.get(Publication, uuid.UUID(publication_id))
            ref = f'wikidata:{publication.target_environment}:{actor}'
            resolver_args = dict(publication_id=publication_id, execution_id=scope, actor_id=actor)
            credential = await ExecutionCredentialResolver(params['_publication_credential'], **resolver_args).resolve(ref)
            api_key = None
            if params.get('_ai_credential'):
                api_key = (await ExecutionCredentialResolver(params['_ai_credential'], **resolver_args).resolve(ref)).secret
            boundary = await CurrentWikidataBoundaryFactory().open(credential)
            actions = (await db.execute(select(PublicationPlanAction).where(
                PublicationPlanAction.plan_id == plan.id, PublicationPlanAction.action == 'block',
            ).order_by(PublicationPlanAction.entity_key).limit(501))).scalars().all()
            if len(actions) > 500:
                raise ValueError('The AI report supports at most 500 blocked actions per Plan.')
            total = len(actions)
            saved = (job.progress or {}).get('report')
            report = PublicationAiReport.model_validate(saved) if saved else PublicationAiReport(
                publication_id=publication_id, plan_id=str(plan.id), plan_digest=plan.plan_digest,
                release_digest=plan.release_digest, tier_model=params['tier_model'], created_at=datetime.now(UTC))
            if report.plan_digest != params['plan_digest']:
                raise ValueError('The saved AI report belongs to another Plan.')
            done = {item.entity_key for item in report.items}
            repository = SqlAlchemyPublicationRepository(db)

            async def save():
                await update_job_progress(job_id, {'phase': 'ai_review', 'processed': len(report.items),
                    'total': total, 'message': 'Review blocked Wikidata items.', 'report': report.model_dump(mode='json')})

            await db.commit()
            await save()
            for action in actions:
                await _cancel_check(job_id)
                if action.entity_key in done:
                    continue
                entity = await repository.get_release_entity(str(plan.release_id), action.entity_key)
                document = thaw_json(entity.document)
                labels = document.get('labels') or {}
                label = str(labels.get('en') or labels.get('he') or entity.entity_key)
                base = dict(entity_key=entity.entity_key, label=label, qid=action.target_qid)
                await db.commit()
                try:
                    if action.observation_status != 'present_foreign':
                        observation = None
                        for _ in range(2):
                            await _cancel_check(job_id)
                            observations = await boundary.reconcile_batch((entity,))
                            observation = next((value for value in observations if value.entity_key == entity.entity_key), None)
                            if observation is not None and observation.status != 'unknown':
                                break
                        resolved = observation is not None and observation.status != 'unknown'
                        item = PublicationAiReviewItem(**base, status='lookup_resolved' if resolved else 'review_required',
                            reason='The lookup returned a result. Create a fresh Dry-run Receipt to check the Plan.' if resolved
                            else 'The lookup remains unavailable. No creation is permitted.')
                    else:
                        snapshot = await boundary.fetch_entity(action.target_qid)
                        if (snapshot is None or snapshot.qid != action.target_qid or snapshot.revision != action.target_revision
                            or snapshot.document is None):
                            item = PublicationAiReviewItem(**base, status='review_required',
                                reason='The Wikidata item changed or lacks evidence. Create a fresh Dry-run Receipt before review.')
                        else:
                            fixture = {**document, 'local_id': entity.entity_key, 'entity_type': entity.entity_type,
                                'publication_review': {'target': credential.target.site, 'qid': snapshot.qid,
                                    'revision': snapshot.revision, 'entity_digest': entity.entity_digest,
                                    'proposed_entity': document, 'remote_entity': dict(snapshot.document),
                                    'evidence_refs': list(entity.evidence_refs), 'identity_assertions': list(entity.identity_assertions)}}
                            verdict = await _judge(db, job_id, fixture, params['tier_model'], api_key,
                                bool(params.get('force_refresh')), actor)
                            supported = verdict['overall'] == 'full' and verdict.get('name_ok') == 'yes' and verdict.get('type_ok') == 'yes'
                            item = PublicationAiReviewItem(**base, status='recommended' if supported else 'review_required',
                                reason=verdict['reasoning'], consent=PublicationForeignQidConsent(entity_key=entity.entity_key,
                                    qid=snapshot.qid, remote_revision=snapshot.revision, entity_digest=entity.entity_digest) if supported else None)
                except ReviewCancelled:
                    raise
                except Exception:
                    logger.exception('Publication AI review failed for entity %s', entity.entity_key)
                    item = PublicationAiReviewItem(**base, status='error', reason='The evidence lookup or AI review failed. Retry the AI review.')
                report.items.append(item)
                await save()
            await _cancel_check(job_id)
            await db.rollback()
            await checked_plan(db, run_id, publication_id, params['plan_id'], params['plan_digest'], actor)
        await finish_job(job_id, status='succeeded', result={'report': report.model_dump(mode='json')},
            progress={'phase': 'succeeded', 'processed': total, 'total': total, 'message': 'Review the AI report before approval.'})
    except ReviewCancelled:
        await finish_job(job_id, status='cancelled', result={'report': report.model_dump(mode='json')} if report else None)
    except Exception:
        logger.exception('Publication AI review job failed: %s', job_id)
        await finish_job(job_id, status='failed', error='The AI review could not complete. Check that the Release and Plan are current.',
            result={'report': report.model_dump(mode='json')} if report else None)
