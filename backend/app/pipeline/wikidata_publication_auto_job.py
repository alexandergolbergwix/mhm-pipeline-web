"""Automatic evidence checks, conservative subset, policy approval, and dry-run."""
import logging
import uuid
from datetime import UTC, datetime
from sqlalchemy import select
from app.db import session_scope
from app.models.publication import Publication, PublicationPlanAction
from app.models.run_job import RunJob
from app.pipeline.run_job_service import finish_job, update_job_progress
from app.pipeline.wikidata_publication_ai_review_job import _judge, _cancel_check, ReviewCancelled
from app.publication.ai_review import checked_plan, review_version
from app.publication.automatic_evidence import collect_evidence, search_candidates
from app.publication.automatic_policy import POLICY_VERSION, decide
from app.publication.automatic_projection import build_documents
from app.publication.credentials import ExecutionCredentialResolver
from app.publication.digests import thaw_json, canonical_digest
from app.publication.runtime import PublicationRuntime, _snapshot_parts, _has_blocking_item_issue
from app.publication.sql_repository import SqlAlchemyPublicationRepository
from app.publication.wikidata_gateway import CurrentWikidataBoundaryFactory, WikidataGatewayAdapter
from app.schemas.publication import PreparePublicationRequest, AdvancePublicationRequest
from app.schemas.publication_ai_review import PublicationAiReport, PublicationAiReviewItem

logger = logging.getLogger(__name__)


async def _automatic_judge(*args):
    for attempt in range(2):
        try:
            return await _judge(*args)
        except ReviewCancelled:
            raise
        except Exception:
            if attempt == 1:
                raise
            logger.warning('Automatic judge output failed validation or delivery; retry once.', exc_info=True)


async def run_automatic_publication(job_id):
    report = None
    try:
        async with session_scope() as db:
            job = await db.get(RunJob, job_id)
            if job is None:
                return
            params = dict(job.params or {})
            actor, run_id = str(params.get('actor_id')), job.run_id
            base_id, scope = params['publication_id'], params['credential_scope_id']
            if job.created_by != uuid.UUID(actor) or not scope.startswith('ai-review:') or params.get('review_version') != review_version():
                raise ValueError('The automatic review scope or policy changed.')
            await _cancel_check(job_id)
            plan = await checked_plan(db, run_id, base_id, params['plan_id'], params['plan_digest'], actor)
            publication = await db.get(Publication, uuid.UUID(base_id))
            source_snapshot_id = publication.source_snapshot_id
            ref = f'wikidata:{publication.target_environment}:{actor}'
            resolver_args = dict(publication_id=base_id, execution_id=scope, actor_id=actor)
            resolver = ExecutionCredentialResolver(params['_publication_credential'], **resolver_args)
            credential = await resolver.resolve(ref)
            api_key = None
            if params.get('_ai_credential'):
                api_key = (await ExecutionCredentialResolver(params['_ai_credential'], **resolver_args).resolve(ref)).secret
            boundary = await CurrentWikidataBoundaryFactory().open(credential)
            actions = (await db.execute(select(PublicationPlanAction).where(PublicationPlanAction.plan_id == plan.id)
                .order_by(PublicationPlanAction.entity_key).limit(501))).scalars().all()
            if len(actions) > 500:
                raise ValueError('Automatic resolution supports at most 500 entities per Release.')
            saved = (job.progress or {}).get('report') or (job.result or {}).get('report')
            if not saved and params.get('resume_from_job_id') and not params.get('force_refresh'):
                previous = await db.get(RunJob, uuid.UUID(params['resume_from_job_id']))
                if (previous and previous.created_by == job.created_by and previous.run_id == run_id
                    and previous.params.get('plan_digest') == params['plan_digest']
                    and previous.params.get('review_version') == params['review_version']
                    and previous.params.get('tier_model') == params['tier_model']
                    and previous.params.get('verification_model') == params.get('verification_model')):
                    saved = (previous.result or {}).get('report') or (previous.progress or {}).get('report')
            report = PublicationAiReport.model_validate(saved) if saved else PublicationAiReport(
                publication_id=base_id, plan_id=str(plan.id), plan_digest=plan.plan_digest,
                release_digest=plan.release_digest, tier_model=params['tier_model'], created_at=datetime.now(UTC),
                automatic=True, policy_version=POLICY_VERSION)
            if report.plan_digest != plan.plan_digest or report.policy_version != POLICY_VERSION:
                raise ValueError('The saved resolution belongs to another Plan or policy.')
            report.items = [row for row in report.items if not (row.resolution or {}).get('retryable')]
            done = {row.entity_key for row in report.items}
            repository = SqlAlchemyPublicationRepository(db)
            documents = {}
            async def save(phase='automatic_review'):
                await update_job_progress(job_id, {'phase': phase, 'processed': len(report.items), 'total': len(actions),
                    'message': {'prepare_subset': 'Prepare the supported subset.', 'approve_subset': 'Record automatic policy approval.',
                        'dry_run': 'Check the retained subset against Wikidata.'}.get(phase, 'Resolve identities and claims automatically.'),
                    'report': report.model_dump(mode='json')})
            await db.commit()
            for action in actions:
                await _cancel_check(job_id)
                entity = await repository.get_release_entity(str(plan.release_id), action.entity_key)
                document = thaw_json(entity.document)
                document['statements'] = document.get('policy_original_statements') or (
                    list(document.get('statements') or []) + list(document.get('deferred_statements') or []))
                document['deferred_statements'] = []
                documents[entity.entity_key] = document
                if entity.entity_key in done:
                    continue
                await db.commit()
                evidence, decisions = [], []
                qid, revision = action.target_qid, action.target_revision
                resolution = {'action': 'defer', 'statement_indices': [], 'reason': 'The source or provider check did not complete.', 'retryable': True}
                try:
                    if _has_blocking_item_issue(document):
                        resolution.update(reason='A deterministic source or identity check failed.', retryable=False)
                        raise ValueError('The original source has a blocking finding.')
                    observation = None
                    for _ in range(2):
                        await _cancel_check(job_id)
                        observations = await boundary.reconcile_batch((entity,))
                        observation = next((row for row in observations if row.entity_key == entity.entity_key), None)
                        if observation is not None and observation.status != 'unknown':
                            break
                    status = observation.status if observation else 'unknown'
                    qid = observation.qid if observation else None
                    revision = observation.remote_revision if observation else None
                    remote = None
                    if status in {'present_owned', 'present_foreign'}:
                        snapshot = await boundary.fetch_entity(qid)
                        if snapshot is None or snapshot.qid != qid or snapshot.revision != revision or snapshot.document is None:
                            raise ValueError('The target changed during evidence collection.')
                        remote = dict(snapshot.document)
                    evidence = await collect_evidence(db, document, uuid.UUID(actor), bool(params.get('force_refresh')), run_id=run_id)
                    fixture = {**document, 'local_id': entity.entity_key, 'entity_type': entity.entity_type,
                        'publication_review': {'automatic': True, 'target': credential.target.site,
                            'qid': qid, 'revision': revision, 'entity_digest': entity.entity_digest,
                            'observation': status, 'proposed_entity': document, 'remote_entity': remote, 'evidence': [{key: value for key, value in row.items() if key != 'retrieved_at'} for row in evidence]}}
                    if status != 'unknown' and any(row.get('kind') == 'primary' for row in evidence):
                        for check in ('assessment', 'independent_verification'):
                            await _cancel_check(job_id)
                            fixture['publication_review']['check'] = check
                            fixture['publication_review']['instruction'] = ('Test the identity and each claim against sources. Seek contradictions.'
                                if check == 'independent_verification' else 'Assess the supplied sources.')
                            model = (params.get('verification_model') or params['tier_model']) if check == 'independent_verification' else params['tier_model']
                            verdict = await _automatic_judge(db, job_id, fixture, model, api_key, bool(params.get('force_refresh')), actor)
                            decisions.append(verdict.get('publication_decision') or {})
                        resolution = decide(document, remote, status, decisions[0], decisions[1], evidence)
                        resolution['retryable'] = False
                        if qid and all(value.get('identity') == 'different_entity' for value in decisions):
                            rejected_qid = qid
                            resolution.update(rejected_qid=qid, retryable=True)
                            labels = document.get('labels') or {}
                            candidates = await search_candidates(db, credential.target.site,
                                str(labels.get('he') or labels.get('en') or ''), uuid.UUID(actor), bool(params.get('force_refresh')))
                            for candidate_qid in candidates:
                                if candidate_qid == rejected_qid:
                                    continue
                                await _cancel_check(job_id)
                                candidate = await boundary.fetch_entity(candidate_qid)
                                if candidate is None or candidate.document is None:
                                    continue
                                pack = fixture['publication_review']
                                pack.update(qid=candidate.qid, revision=candidate.revision,
                                    remote_entity=dict(candidate.document), observation='present_foreign')
                                checks = []
                                for check in ('assessment', 'independent_verification'):
                                    pack['check'] = check
                                    model = (params.get('verification_model') or params['tier_model']) if check == 'independent_verification' else params['tier_model']
                                    verdict = await _automatic_judge(db, job_id, fixture, model, api_key, bool(params.get('force_refresh')), actor)
                                    checks.append(verdict.get('publication_decision') or {})
                                alternative = decide(document, dict(candidate.document), 'present_foreign', checks[0], checks[1], evidence)
                                if alternative['action'] == 'reuse_existing':
                                    resolution, decisions = alternative, checks
                                    qid, revision = candidate.qid, candidate.revision
                                    resolution['rejected_qid'] = rejected_qid
                                    break
                            resolution['retryable'] = False
                    else:
                        resolution['reason'] = 'The duplicate check or primary evidence is unavailable.'
                except ReviewCancelled:
                    raise
                except Exception:
                    logger.exception('Automatic Publication assessment deferred %s', entity.entity_key)
                resolution.update(qid=qid, remote_revision=revision)
                report.items.append(PublicationAiReviewItem(entity_key=entity.entity_key,
                    label=str((document.get('labels') or {}).get('en') or (document.get('labels') or {}).get('he') or entity.entity_key),
                    qid=qid, status='deferred' if resolution['action'] == 'defer' else resolution['action'],
                    reason=resolution['reason'], resolution=resolution, decisions=decisions,
                    evidence=[{**row, 'digest': canonical_digest(row), 'text': str(row.get('text') or '')[:1200]} for row in evidence]))
                await save()
            await _cancel_check(job_id)
            await db.rollback()
            await checked_plan(db, run_id, base_id, params['plan_id'], params['plan_digest'], actor)
            _, source, approved_only = _snapshot_parts(source_snapshot_id)
            target = 'live' if credential.target.site == 'www.wikidata.org' else 'test'
            async def dry_run_progress(processed, total):
                await _cancel_check(job_id)
                await update_job_progress(job_id, {'phase': 'dry_run', 'processed': len(report.items),
                    'total': len(actions), 'message': f'Fresh dry-run: {processed} / {total} retained items.',
                    'report': report.model_dump(mode='json')})
            runtime = PublicationRuntime(session=db, dry_run_progress=dry_run_progress, gateway_factory=lambda **kwargs: WikidataGatewayAdapter(
                credential_resolver=resolver, boundary_factory=CurrentWikidataBoundaryFactory()))
            # A bounded retry excludes newly blocked actions instead of weakening a gate.
            for attempt in range(3):
                await _cancel_check(job_id)
                resolutions = {row.entity_key: row.resolution for row in report.items}
                projected = build_documents(documents, resolutions, job_id)
                for row in report.items:
                    reason = projected[row.entity_key].get('publication_deferred')
                    if reason:
                        row.status = 'deferred'
                        row.reason = str(reason)
                        row.resolution = {**row.resolution, 'action': 'defer', 'reason': row.reason}
                if not any(not item.get('publication_deferred') for item in projected.values()):
                    report.result_publication_id = None
                    break
                await save('prepare_subset')
                prepared = (await runtime.prepare(run_id=run_id, actor_id=actor,
                    request=PreparePublicationRequest(profile_id='mhm-wikidata', profile_version='1-nodes', target=target,
                        source={'kind': 'run', 'projection_source': source, 'approved_only': approved_only}),
                    automatic_documents=projected, idempotency_key=f'automatic:{job_id}:{attempt}')).publication
                report.result_publication_id = prepared.publication_id
                await save('approve_subset')
                reviewed = (await runtime.advance(run_id=run_id, publication_id=prepared.publication_id,
                    actor_id=f'policy:{POLICY_VERSION}', request=AdvancePublicationRequest.model_validate({'command': {
                        'type': 'review', 'release_id': prepared.current_release.release_id,
                        'expected_release_digest': prepared.current_release.release_digest,
                        'selection': {'mode': 'eligible_release'}, 'decision': 'approve',
                        'reason': f'Automatic policy {POLICY_VERSION}; evidence report {job_id}. No human item review asserted.'}}))).publication
                await _cancel_check(job_id)
                await save('dry_run')
                checked = (await runtime.advance(run_id=run_id, publication_id=prepared.publication_id, actor_id=actor,
                    request=AdvancePublicationRequest.model_validate({'command': {'type': 'dry_run', 'force_refresh': True,
                        'approval_set_id': reviewed.approval_set.approval_set_id,
                        'expected_approval_digest': reviewed.approval_set.approval_digest}}))).publication
                if checked.dry_run_receipt.status == 'valid':
                    break
                blocked = (await db.execute(select(PublicationPlanAction.entity_key).where(
                    PublicationPlanAction.plan_id == uuid.UUID(checked.plan.plan_id), PublicationPlanAction.action == 'block'))).scalars().all()
                for row in report.items:
                    if row.entity_key in blocked:
                        row.status = 'deferred'
                        row.reason = 'Fresh Wikidata checks blocked this action; the automatic policy deferred it.'
                        row.resolution = {**row.resolution, 'action': 'defer', 'reason': row.reason}
                if attempt == 2:
                    raise ValueError('Remote state did not stabilize within the retry budget.')
            await save('succeeded')
        await finish_job(job_id, status='succeeded', result={'report': report.model_dump(mode='json')},
            progress={'phase': 'succeeded', 'processed': len(report.items), 'total': len(report.items),
                'message': 'Automatic assessment complete. Deferred items remain in the report; no Wikidata writes occurred.'})
    except ReviewCancelled:
        await finish_job(job_id, status='cancelled', result={'report': report.model_dump(mode='json')} if report else None)
    except Exception:
        logger.exception('Automatic Publication resolution failed: %s', job_id)
        await finish_job(job_id, status='failed', error='Automatic resolution stopped. Its saved evidence remains available for retry.',
            result={'report': report.model_dump(mode='json')} if report else None)
