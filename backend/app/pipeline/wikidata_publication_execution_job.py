"""Run a queued, durable Wikidata Publication Execution."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

import sqlalchemy

from app.db import session_scope
from app.models.publication import Publication, PublicationExecution
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.run_job_service import (
    finish_job,
    is_cancel_requested,
    update_job_progress,
)
from app.publication.credentials import (
    ExecutionCredentialResolver,
    configured_publication_gateway_factory,
)
from app.publication.gateway import WikidataGateway
from app.publication.runtime import PublicationRuntime, PublicationSummary
from app.publication.types import TargetRef
from app.publication.wikidata_gateway import (
    CurrentWikidataBoundaryFactory,
    WikidataGatewayAdapter,
)

logger = logging.getLogger(__name__)

# Heroku Postgres can drop a connection that a worker has held across long
# network stretches; the next commit/rollback then raises
# "cannot call Transaction.rollback(): the underlying connection is closed"
# and killed the whole pass (2026-10-17, both live executions). The durable
# claim rows survive a worker death, so the fix is to RE-RUN the worker on a
# fresh session instead of crashing.
_DEAD_CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    sqlalchemy.exc.InterfaceError,
    sqlalchemy.exc.OperationalError,
    ConnectionError,
    OSError,
)
_WORKER_ATTEMPTS = 3
_CONNECTION_RETRY_BACKOFF_S = 2.0


def _ledger_entity_type(entity_key: str) -> str:
    """Map a release entity key onto the global ledger key type."""
    key = entity_key.strip()
    if key.startswith("QDraft_MS_"):
        return "marc"
    if key.startswith("QDraft_Person_") or key.startswith("person:"):
        return "person"
    if key.startswith("QDraft_Work_") or key.startswith("work:"):
        return "work"
    return "other"


async def _record_execution_ledger(
    execution_id: uuid.UUID,
    *,
    publication_id: uuid.UUID,
) -> int:
    """Record succeeded write QIDs into the global ledger.

    The phase-2 release resolves ``__LOCAL:`` references through this ledger,
    so without it the cross-item links (works → manuscripts, persons) would
    never materialise after the phase-1 creates (2026-10-15 live runbook).
    Idempotent: re-running after a resume re-records the same mappings.
    """
    from sqlalchemy import select

    from app.models.publication import (
        Publication,
        PublicationEntityRow,
        PublicationExecution,
        PublicationExecutionAction,
        PublicationPlan,
    )
    from app.pipeline.wikidata_qid_ledger import (
        ledger_namespace,
        record_ledger_mapping,
    )

    recorded = 0
    async with session_scope() as db:
        publication_row = await db.get(Publication, publication_id)
        is_test = (
            str(publication_row.target_environment or "") != "production"
            if publication_row is not None
            else True
        )
        namespace = ledger_namespace(is_test=is_test)
        release_id = (
            await db.execute(
                select(PublicationPlan.release_id)
                .join(
                    PublicationExecution,
                    PublicationExecution.plan_id == PublicationPlan.id,
                )
                .where(PublicationExecution.id == execution_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if release_id is None:
            return 0
        rows = (
            await db.execute(
                select(
                    PublicationExecutionAction.entity_key,
                    PublicationExecutionAction.result_qid,
                    PublicationEntityRow.entity_type,
                )
                .join(
                    PublicationEntityRow,
                    (PublicationEntityRow.release_id == release_id)
                    & (PublicationEntityRow.entity_key == PublicationExecutionAction.entity_key),
                )
                .where(
                    PublicationExecutionAction.execution_id == execution_id,
                    PublicationExecutionAction.state == "succeeded",
                    PublicationExecutionAction.result_qid.is_not(None),
                )
            )
        ).all()
        for entity_key, qid, entity_type in rows:
            # The ledger key convention maps manuscript → marc
            # (ledger_key_for_item); person/work keep their names.
            ledger_type = "marc" if entity_type == "manuscript" else entity_type
            await record_ledger_mapping(
                db,
                f"{namespace}:{ledger_type}:{entity_key}",
                str(qid),
                local_key=entity_key,
            )
            recorded += 1
    return recorded


def _make_execution_progress(job_id: uuid.UUID) -> Any:
    """Mirror the runtime's write progress into the job row while writing.

    The runtime only returns at the end, so without this callback the tray
    showed 'starting' for the whole write pass (2026-10-15: 1847 serial
    writes ≈ 90 min with a frozen 0 / 0 display).
    """

    async def progress(processed: int, total: int) -> None:
        await update_job_progress(
            job_id,
            {
                "phase": "running",
                "processed": processed,
                "total": total,
                "unit": "entities",
                "message": f"Writing to Wikidata: {processed} of {total} items.",
            },
        )

    return progress


def _required_param(job: RunJob, name: str) -> str:
    value = str((job.params or {}).get(name) or "").strip()
    if not value:
        raise ValueError(f"The Publication execution job lacks {name}")
    return value


async def _run_with_connection_retries(worker_id: str, attempt_fn):
    """Run ``attempt_fn``; re-run it on a dead database connection.

    The durable claim rows survive a worker death, so the only cost of a
    dropped connection is the retry. Bounded: after ``_WORKER_ATTEMPTS``
    the last error propagates and the execution pauses for a resume.
    """
    last_error: BaseException | None = None
    for attempt in range(_WORKER_ATTEMPTS):
        try:
            return await attempt_fn()
        except _DEAD_CONNECTION_ERRORS as exc:
            last_error = exc
            logger.warning(
                "Publication worker %s lost its database connection "
                "(attempt %d/%d); retrying on a fresh session: %s",
                worker_id, attempt + 1, _WORKER_ATTEMPTS, exc,
            )
            await asyncio.sleep(_CONNECTION_RETRY_BACKOFF_S * (attempt + 1))
    assert last_error is not None
    raise last_error


async def _mark_execution_paused(execution_id: str) -> None:
    """Return a crashed execution to `paused` on a FRESH session.

    The run-scoped session can share the dead connection that just crashed a
    worker; the pause mark must still land or the execution is unrecoverable.
    """
    try:
        async with session_scope() as db:
            execution = await db.get(PublicationExecution, uuid.UUID(execution_id))
            if execution is not None and execution.status in {"queued", "running"}:
                execution.status = "paused"
                await db.commit()
    except Exception:  # noqa: BLE001 — best-effort; the state reaper is the backstop
        logger.exception("Marking execution %s paused failed", execution_id)


def _progress(summary) -> dict[str, object]:
    execution = summary.execution
    if execution is None:
        return {"phase": "failed", "message": "The Publication Execution is absent."}
    return {
        "phase": execution.status,
        "processed": execution.processed,
        "total": execution.total,
        "unit": "entities",
        "current_label": execution.current_entity_label,
        "message": f"Publication Execution is {execution.status}.",
    }


async def run_wikidata_publication_execution_job(job_id: uuid.UUID) -> None:
    """Resume a queued Execution and write only through the gateway seam."""
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        publication_id = _required_param(job, "publication_id")
        execution_id = _required_param(job, "execution_id")
        actor_id = _required_param(job, "actor_id")
        run_id = job.run_id
        if await is_cancel_requested(job_id):
            await finish_job(
                job_id,
                status=JOB_STATUS_CANCELLED,
                result={"publication_id": publication_id, "execution_id": execution_id},
                progress={"phase": "cancelled", "message": "Publication Execution cancelled."},
            )
            return
        envelope = (job.params or {}).get("_publication_credential")
        gateway_factory = configured_publication_gateway_factory
        if envelope is not None:
            if not isinstance(envelope, str) or job.created_by != uuid.UUID(actor_id):
                raise ValueError("The Publication execution credential account is invalid")
            resolver = ExecutionCredentialResolver(envelope,
                publication_id=publication_id, execution_id=execution_id, actor_id=actor_id)

            def credential_gateway_factory(*, target: TargetRef, actor_id: str) -> WikidataGateway:
                return WikidataGatewayAdapter(credential_resolver=resolver,
                    boundary_factory=CurrentWikidataBoundaryFactory())

            gateway_factory = credential_gateway_factory

        runtime = PublicationRuntime(
            session=db,
            gateway_factory=gateway_factory,
            execution_progress=_make_execution_progress(job_id),
        )
        await update_job_progress(
            job_id,
            {
                "phase": "running",
                "processed": 0,
                "total": 0,
                "unit": "entities",
                "message": "Publication Execution is starting.",
            },
        )
        # Parallel writers: the durable claim system already supports
        # multiple workers (leased claims on disjoint actions), so K
        # workers each drive their own gateway session and write
        # concurrently. Serial writes made a 1736-item pass cost ~90 min
        # plus maxlag storms (2026-10-16). Each worker needs its OWN DB
        # session — one AsyncSession is not safe for concurrent use.
        # Default 16 on the Modal container: the write path is
        # network-bound (identity check + write + read-back per item),
        # and the 429/maxlag retries absorb API pressure; 16 keeps
        # every worker busy while staying within Wikidata bot limits.
        worker_count = max(1, min(16, int(
            job.params.get("worker_count")
            or os.environ.get("PUBLICATION_EXECUTION_WORKERS")
            or 16
        )))

        async def _run_worker(index: int) -> PublicationSummary:
            worker_id = f"publication-job:{job_id}:w{index}"

            async def _attempt() -> PublicationSummary:
                async with session_scope() as worker_db:
                    worker_runtime = PublicationRuntime(
                        session=worker_db,
                        gateway_factory=gateway_factory,
                        execution_progress=_make_execution_progress(job_id),
                    )
                    return await worker_runtime.execute(
                        run_id=run_id,
                        publication_id=publication_id,
                        execution_id=execution_id,
                        actor_id=actor_id,
                        worker_id=worker_id,
                    )

            return await _run_with_connection_retries(worker_id, _attempt)

        try:
            worker_summaries = await asyncio.gather(
                *(_run_worker(i) for i in range(worker_count)),
                return_exceptions=True,
            )
            failures = [s for s in worker_summaries if isinstance(s, BaseException)]
            summaries = [s for s in worker_summaries if not isinstance(s, BaseException)]
            if not summaries:
                # Every worker failed: the durable execution must go back to
                # paused so a resume can retry it.
                await _mark_execution_paused(execution_id)
                raise failures[0] if failures else RuntimeError(
                    "The Publication execution produced no worker summary"
                )
        except Exception:
            # Keep the durable execution resumable when a worker fails before
            # the runtime can persist its normal paused state. A fresh
            # session: the outer session may share the dead connection.
            await _mark_execution_paused(execution_id)
            raise
        # Prefer a summary whose execution reached a terminal state; paused
        # beats failed-for-now because the durable rows keep the detail.
        def _rank(summary: PublicationSummary) -> int:
            status = summary.execution.status if summary.execution else ""
            return {"succeeded": 0, "completed": 1, "cancelled": 2}.get(status, 3)

        summary = max(summaries, key=_rank)
        for failure in failures:
            logger.warning("A publication execution worker failed: %s", failure)

    execution = summary.execution
    if execution is None:
        await finish_job(
            job_id,
            status=JOB_STATUS_FAILED,
            error="The Publication Execution is absent.",
            result={"publication_id": publication_id, "execution_id": execution_id},
        )
        return
    # Ledger first: even a paused execution must leave the created QIDs
    # behind, or the phase-2 linking release cannot resolve __LOCAL: refs.
    try:
        recorded = await _record_execution_ledger(
            uuid.UUID(execution_id),
            publication_id=uuid.UUID(publication_id),
        )
        if recorded:
            logger.info("Publication execution recorded %d ledger mappings", recorded)
    except Exception:
        logger.exception(
            "Recording the publication execution ledger mappings failed"
        )
    terminal = {
        "succeeded": JOB_STATUS_SUCCEEDED,
        "cancelled": JOB_STATUS_CANCELLED,
        "failed": JOB_STATUS_FAILED,
    }.get(execution.status, JOB_STATUS_FAILED)
    if execution.status == "paused":
        error = (
            "The Publication Execution processed all safe items and paused with "
            f"{execution.failed} "
            f"{('item' if execution.failed == 1 else 'items')} unresolved. "
            "Resume it to retry the unresolved items."
        )
    elif execution.status == "failed":
        error = (
            f"The Publication Execution finished with {execution.failed} "
            f"{('item' if execution.failed == 1 else 'items')} that "
            f"{('needs' if execution.failed == 1 else 'need')} attention. "
            "Review the audit before starting a new Release."
        )
    else:
        error = None
    await finish_job(
        job_id,
        status=terminal,
        result={"publication_id": publication_id, "execution_id": execution_id},
        error=error,
        progress=_progress(summary),
    )
