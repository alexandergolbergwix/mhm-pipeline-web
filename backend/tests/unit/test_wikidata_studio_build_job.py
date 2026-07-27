"""Wikidata Studio background build job contract."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.run_job import JOB_KIND_WIKIDATA_STUDIO_BUILD, JOB_STATUS_SUCCEEDED, RunJob
from app.pipeline.wikidata_studio_build_job import run_wikidata_studio_build_job


@pytest.mark.asyncio
async def test_build_job_skips_wdqs_reconcile(db_session) -> None:
    run_id = uuid.uuid4()
    job_id = uuid.uuid4()
    db_session.add(
        RunJob(
            id=job_id,
            project_id=uuid.uuid4(),
            run_id=run_id,
            kind=JOB_KIND_WIKIDATA_STUDIO_BUILD,
            status="running",
            params={
                "approved_only": True,
                "force_rebuild": True,
                "source": "canonical",
            },
            progress={},
            created_by=uuid.uuid4(),
        )
    )
    await db_session.commit()

    cached = SimpleNamespace(
        result_items=[{"local_id": "ms1", "entity_type": "manuscript"}],
        summary={"total_items": 1},
        record_count=1,
        approved_match_count=0,
    )

    with (
        patch(
            "app.routers.wikidata_studio.execute_studio_build",
            new=AsyncMock(return_value=cached),
        ) as build,
        patch(
            "app.pipeline.wikidata_studio_build_job.is_cancel_requested",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.pipeline.wikidata_studio_build_job.finish_job",
            new=AsyncMock(),
        ) as finish,
        patch(
            "app.pipeline.wikidata_studio_build_job.update_job_progress",
            new=AsyncMock(),
        ),
    ):
        await run_wikidata_studio_build_job(job_id)

    build.assert_awaited_once()
    assert build.await_args.kwargs["reconcile"] is False
    assert build.await_args.kwargs["source"] == "canonical"
    finish.assert_awaited_once()
    assert finish.await_args.kwargs["status"] == JOB_STATUS_SUCCEEDED
