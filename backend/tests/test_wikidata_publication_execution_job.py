"""Regression tests for the queued Publication Execution worker."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models.run_job import (
    JOB_KIND_WIKIDATA_PUBLICATION_EXECUTION,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.wikidata_publication_execution_job import (
    run_wikidata_publication_execution_job,
)


@pytest.mark.asyncio
async def test_execution_worker_uses_references_and_never_a_plaintext_secret(
    sample_run,
    db_session,
    monkeypatch,
) -> None:
    execution_id = str(uuid.uuid4())
    publication_id = str(uuid.uuid4())
    actor_id = str(sample_run["user_id"])
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind=JOB_KIND_WIKIDATA_PUBLICATION_EXECUTION,
        status=JOB_STATUS_RUNNING,
        params={
            "publication_id": publication_id,
            "execution_id": execution_id,
            "actor_id": actor_id,
        },
        progress={},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()

    seen: dict[str, object] = {}

    class FakeRuntime:
        def __init__(self, *, session, gateway_factory) -> None:
            seen["session"] = session
            seen["gateway_factory"] = gateway_factory

        async def execute(self, **kwargs):
            seen["execute"] = kwargs
            return SimpleNamespace(
                execution=SimpleNamespace(
                    execution_id=execution_id,
                    status="succeeded",
                    processed=2,
                    total=2,
                    current_entity_label=None,
                )
            )

    monkeypatch.setattr(
        "app.pipeline.wikidata_publication_execution_job.PublicationRuntime",
        FakeRuntime,
    )
    await run_wikidata_publication_execution_job(job.id)

    await db_session.refresh(job)
    assert job.status == JOB_STATUS_SUCCEEDED
    assert job.params == {
        "publication_id": publication_id,
        "execution_id": execution_id,
        "actor_id": actor_id,
    }
    assert seen["execute"] == {
        "run_id": sample_run["run_id"],
        "publication_id": publication_id,
        "execution_id": execution_id,
        "actor_id": actor_id,
        "worker_id": f"publication-job:{job.id}",
    }
