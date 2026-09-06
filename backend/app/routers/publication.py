"""Run-scoped HTTP seam for durable Wikidata Publication operations."""

from __future__ import annotations

import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.run_job import (
    JOB_KIND_WIKIDATA_PUBLICATION_EXECUTION,
    JOB_KIND_WIKIDATA_PUBLICATION_PREPARE,
)
from app.pipeline.run_job_service import ActiveJobError, create_job
from app.publication.core import (
    BlockingFindingsError,
    EmptySelectionError,
    InvalidCursorError,
    StaleDigestError,
)
from app.publication.credentials import SavedPublicationCredentialResolver, seal_execution_credential
from app.publication.wikidata_gateway import CurrentWikidataBoundaryFactory, WikidataGatewayAdapter
from app.publication.types import TargetRef
from app.publication.gateway import WikidataGateway
from app.models.publication import Publication as PublicationRow
from app.publication.repository import PublicationNotFoundError, ReleaseNotFoundError
from app.publication.runtime import (
    PublicationGatewayFactory,
    PublicationGatewayUnavailableError,
    PublicationRuntime,
    PublicationSourceError,
    UnsupportedPublicationActionError,
)
from app.routers.runs import _lookup_run_with_access
from app.schemas.publication import (
    AdvancePublicationRequest,
    PreparePublicationRequest,
    PublicationAuditPage,
    PublicationEntityPage,
    PublicationMutationResponse,
    PublicationOperation,
    PublicationOperationRead,
    PublicationSummaryRead,
    PublishPublicationCommand,
    ReadPublicationRequest,
    ResumePublicationCommand,
)

router = APIRouter(prefix="/runs", tags=["wikidata-publications"])


def get_publication_gateway_factory(
    auth: Annotated[AuthContext, Depends(current_auth)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> PublicationGatewayFactory:
    """Use the signed-in user's saved credential at the external boundary."""
    resolver = SavedPublicationCredentialResolver(db, auth.user.id, auth.kek)

    def factory(*, target: TargetRef, actor_id: str) -> WikidataGateway:
        if actor_id != str(auth.user.id):
            raise ValueError("The Publication credential belongs to another account")
        return WikidataGatewayAdapter(credential_resolver=resolver,
            boundary_factory=CurrentWikidataBoundaryFactory())

    return factory


CurrentAuth = Annotated[AuthContext, Depends(current_auth)]
DatabaseSession = Annotated[AsyncSession, Depends(get_session)]
GatewayFactory = Annotated[
    PublicationGatewayFactory,
    Depends(get_publication_gateway_factory),
]


def _runtime(
    db: AsyncSession,
    gateway_factory: PublicationGatewayFactory,
) -> PublicationRuntime:
    return PublicationRuntime(session=db, gateway_factory=gateway_factory)


def _raise_http_error(error: Exception) -> NoReturn:
    if isinstance(error, (PublicationNotFoundError, ReleaseNotFoundError)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publication not found",
        ) from error
    if isinstance(error, PublicationGatewayUnavailableError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    if isinstance(
        error,
        (
            BlockingFindingsError,
            EmptySelectionError,
            InvalidCursorError,
            PublicationSourceError,
            StaleDigestError,
            UnsupportedPublicationActionError,
            ValueError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    raise error


@router.post(
    "/{run_id}/wikidata-publications/prepare",
    response_model=PublicationMutationResponse,
)
async def prepare_publication(
    run_id: uuid.UUID,
    payload: PreparePublicationRequest,
    auth: CurrentAuth,
    db: DatabaseSession,
    gateway_factory: GatewayFactory,
) -> PublicationMutationResponse:
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    try:
        if payload.target == "live" and payload.source.projection_source != "canonical":
            raise PublicationSourceError("Live publication requires the canonical source")
        job = await create_job(
            db,
            project_id=run.project_id,
            run_id=run_id,
            kind=JOB_KIND_WIKIDATA_PUBLICATION_PREPARE,
            params={"actor_id": str(auth.user.id), "request": payload.model_dump(mode="json")},
            created_by=auth.user.id,
        )
        return PublicationMutationResponse(
            operation=PublicationOperation(
                operation_id=str(job.id),
                command="prepare",
                status="queued",
                progress=None,
                error=None,
            )
        )
    except Exception as error:
        await db.rollback()
        _raise_http_error(error)


@router.post(
    "/{run_id}/wikidata-publications/{publication_id}/advance",
    response_model=PublicationMutationResponse,
)
async def advance_publication(
    run_id: uuid.UUID,
    publication_id: str,
    payload: AdvancePublicationRequest,
    auth: CurrentAuth,
    db: DatabaseSession,
    gateway_factory: GatewayFactory,
) -> PublicationMutationResponse:
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    try:
        credential = None
        if isinstance(payload.command, (PublishPublicationCommand, ResumePublicationCommand)):
            row = await db.get(PublicationRow, uuid.UUID(publication_id))
            if row is None or row.run_id != run_id:
                raise PublicationNotFoundError("Publication not found")
            credential = await SavedPublicationCredentialResolver(db, auth.user.id, auth.kek).resolve(
                f"wikidata:{row.target_environment}:{auth.user.id}"
            )
        response = await _runtime(db, gateway_factory).advance(
            run_id=run_id,
            publication_id=publication_id,
            request=payload,
            actor_id=str(auth.user.id),
        )
        if isinstance(payload.command, (PublishPublicationCommand, ResumePublicationCommand)):
            execution = response.publication.execution
            if execution is None or credential is None:
                raise ValueError("The Publication command did not create an authorized Execution")
            try:
                await create_job(
                    db,
                    project_id=run.project_id,
                    run_id=run_id,
                    kind=JOB_KIND_WIKIDATA_PUBLICATION_EXECUTION,
                    params={
                        "publication_id": publication_id,
                        "execution_id": execution.execution_id,
                        "actor_id": str(auth.user.id),
                        "_publication_credential": seal_execution_credential(
                            credential, publication_id=publication_id,
                            execution_id=execution.execution_id,
                        ),
                    },
                    created_by=auth.user.id,
                )
            except ActiveJobError:
                # An attached job already owns this Execution. The immutable
                # Execution id remains the operation id for the client.
                pass
        return response
    except Exception as error:
        await db.rollback()
        _raise_http_error(error)


@router.post(
    "/{run_id}/wikidata-publications/{publication_id}/read",
    response_model=(
        PublicationSummaryRead
        | PublicationOperationRead
        | PublicationEntityPage
        | PublicationAuditPage
    ),
)
async def read_publication(
    run_id: uuid.UUID,
    publication_id: str,
    payload: ReadPublicationRequest,
    auth: CurrentAuth,
    db: DatabaseSession,
    gateway_factory: GatewayFactory,
) -> (
    PublicationSummaryRead
    | PublicationOperationRead
    | PublicationEntityPage
    | PublicationAuditPage
):
    await _lookup_run_with_access(db, run_id, auth, write=False)
    try:
        return await _runtime(db, gateway_factory).read(
            run_id=run_id,
            publication_id=publication_id,
            query=payload.query,
            actor_id=str(auth.user.id),
        )
    except Exception as error:
        await db.rollback()
        _raise_http_error(error)
