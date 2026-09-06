"""Read-only AI work enters the durable queue through an editor-only route."""
import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.publication import Publication
from app.pipeline.run_job_service import ActiveJobError, create_job
from app.pipeline.run_job_params import _resolve_gemini_key
from app.pipeline.judge_models import resolve_tier1_model, ensure_tier1_credentials
from app.publication.ai_review import KIND, checked_plan, latest_job, job_view, review_version
from app.publication.credentials import SavedPublicationCredentialResolver, seal_execution_credential
from app.publication.wikidata_gateway import CredentialMaterial
from app.routers.runs import _lookup_run_with_access
from app.schemas.publication_ai_review import StartPublicationAiReview, PublicationAiReviewState

router = APIRouter(prefix='/runs', tags=['wikidata-publications'])
Auth = Annotated[AuthContext, Depends(current_auth)]
Db = Annotated[AsyncSession, Depends(get_session)]
PATH = '/{run_id}/wikidata-publications/{publication_id}/ai-review'


@router.get(PATH, response_model=PublicationAiReviewState)
async def read_ai_review(run_id: uuid.UUID, publication_id: str, auth: Auth, db: Db):
    await _lookup_run_with_access(db, run_id, auth, write=False)
    try:
        return job_view(await latest_job(db, run_id, publication_id, auth.user.id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post(PATH, response_model=PublicationAiReviewState)
async def start_ai_review(run_id: uuid.UUID, publication_id: str, request: StartPublicationAiReview, auth: Auth, db: Db):
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    try:
        await checked_plan(db, run_id, publication_id, request.plan_id, request.plan_digest, str(auth.user.id))
        previous = await latest_job(db, run_id, publication_id, auth.user.id)
        spec = resolve_tier1_model(request.tier_model)
        if previous is not None and (previous.status in {'queued', 'running'} or (
            previous.status == 'succeeded' and not request.force_refresh
            and previous.params.get('tier_model') == spec.id
            and previous.params.get('review_version') == review_version())):
            return job_view(previous)
        key = await _resolve_gemini_key(db, auth)
        ensure_tier1_credentials(spec, gemini_key=key)
        publication = await db.get(Publication, uuid.UUID(publication_id))
        credential = await SavedPublicationCredentialResolver(db, auth.user.id, auth.kek).resolve(
            f'wikidata:{publication.target_environment}:{auth.user.id}')
        scope = f'ai-review:{uuid.uuid4()}'
        params = {**request.model_dump(mode='json'), 'tier_model': spec.id, 'review_version': review_version(),
            'publication_id': publication_id, 'actor_id': str(auth.user.id), 'credential_scope_id': scope,
            '_publication_credential': seal_execution_credential(credential, publication_id=publication_id, execution_id=scope)}
        if key:
            params['_ai_credential'] = seal_execution_credential(CredentialMaterial(
                credential_id=credential.credential_id, target=credential.target, secret=key),
                publication_id=publication_id, execution_id=scope)
        job = await create_job(db, project_id=run.project_id, run_id=run_id, kind=KIND, params=params, created_by=auth.user.id)
        return job_view(job)
    except ActiveJobError as exc:
        raise HTTPException(409, 'An AI review is already active for this run.') from exc
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, str(exc)) from exc
