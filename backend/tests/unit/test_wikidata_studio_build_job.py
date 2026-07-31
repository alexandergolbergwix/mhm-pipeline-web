"""Wikidata Studio background build job contract."""

from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_build_job_reports_per_record_progress(db_session, monkeypatch) -> None:
    """Rule W-112 — the bar shows record x/n, never a fixed 0/1."""
    run_id, job_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        RunJob(
            id=job_id,
            project_id=uuid.uuid4(),
            run_id=run_id,
            kind=JOB_KIND_WIKIDATA_STUDIO_BUILD,
            status="running",
            params={"approved_only": True, "source": "canonical"},
            progress={},
            created_by=uuid.uuid4(),
        )
    )
    await db_session.commit()

    cached = SimpleNamespace(
        result_items=[{"local_id": "ms1"}, {"local_id": "ms2"}],
        summary={},
        record_count=3,
        approved_match_count=0,
    )

    async def fake_build(_db, **kwargs):
        # Mimic the threadpool builder reporting each record as it completes.
        for done in (1, 2, 3):
            kwargs["progress_cb"](done, 3)
            await asyncio.sleep(0)
        return cached

    monkeypatch.setattr(
        "app.pipeline.wikidata_studio_build_job.PROGRESS_INTERVAL_SECONDS", 0,
    )

    with (
        patch("app.routers.wikidata_studio.execute_studio_build", new=fake_build),
        patch(
            "app.pipeline.wikidata_studio_build_job.is_cancel_requested",
            new=AsyncMock(return_value=False),
        ),
        patch("app.pipeline.wikidata_studio_build_job.finish_job", new=AsyncMock()) as finish,
        patch(
            "app.pipeline.wikidata_studio_build_job.update_job_progress",
            new=AsyncMock(),
        ) as progress,
    ):
        await run_wikidata_studio_build_job(job_id)

    published = [c.args[1] for c in progress.await_args_list]
    per_record = [p for p in published if p.get("unit") == "records"]
    assert per_record, f"no per-record progress published: {published}"
    assert per_record[-1]["total"] == 3
    assert per_record[-1]["processed"] > 0
    assert "3/3" in per_record[-1]["message"]
    # Terminal progress counts items and names the record total.
    done_progress = finish.await_args.kwargs["progress"]
    assert done_progress["processed"] == 2
    assert done_progress["message"] == "Built 2 items from 3 records"
