"""Wikidata Studio background build job contract."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.run_job import JOB_KIND_WIKIDATA_STUDIO_BUILD, JOB_STATUS_SUCCEEDED, RunJob
from app.pipeline.wikidata_studio_build_job import (
    BUILD_PHASES,
    _build_progress,
    _phase_plan,
    run_wikidata_studio_build_job,
)


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
async def test_build_job_reports_phase_steps_and_nested_records(db_session, monkeypatch) -> None:
    """Rules W-112 / W-113 — 1-based phases outside, record loop nested inside.

    Reporting only the item loop left the bar on 0/1 for every slow stage that
    runs before it (canonical read-back, transliteration prewarm).
    """
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
        phase, record = kwargs["phase_cb"], kwargs["progress_cb"]
        phase("loading records")
        await asyncio.sleep(0)
        phase("loading canonical entities")
        await asyncio.sleep(0)
        phase("building items")
        for done in (1, 2, 3):
            record(done, 3)
            await asyncio.sleep(0)
        phase("assembling canonical projection")
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
    assert published, "no progress published"

    # The very first write must already be a real step, never 0/1.
    assert published[0]["processed"] >= 1
    assert published[0]["total"] == len(BUILD_PHASES)
    assert published[0]["unit"] == "steps"
    assert not any(p["processed"] == 0 for p in published)

    seen_phases = [p["phase"] for p in published]
    assert "loading canonical entities" in seen_phases
    assert "building items" in seen_phases

    building = [p for p in published if p["phase"] == "building items"]
    assert building, f"item loop never reported: {seen_phases}"
    assert building[-1]["sub_total"] == 3
    assert building[-1]["sub_unit"] == "records"
    assert "3 of 3" in building[-1]["sub_message"]
    assert building[-1]["message"] == f"Step 4 of {len(BUILD_PHASES)}: building items"

    done_progress = finish.await_args.kwargs["progress"]
    assert done_progress["processed"] == 2
    assert done_progress["message"] == "Built 2 items from 3 records"


def test_legacy_source_omits_the_canonical_phases() -> None:
    """A legacy build must not show steps it will never reach."""
    assert _phase_plan("canonical") == BUILD_PHASES
    legacy = _phase_plan("legacy")
    assert "loading canonical entities" not in legacy
    assert "assembling canonical projection" not in legacy
    assert legacy[0] == "loading records"


def test_progress_falls_back_to_the_first_step_for_an_unknown_phase() -> None:
    progress = _build_progress({"phase": "who knows"}, BUILD_PHASES)
    assert progress["processed"] == 1
    assert progress["total"] == len(BUILD_PHASES)
    assert "sub_total" not in progress
