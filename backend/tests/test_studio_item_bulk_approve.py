"""Studio bulk-approve job: params + worker behaviour."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.hmo_studio_item_override import HmoStudioItemOverride
from app.models.item_override import WikidataItemOverride
from app.models.run_job import (
    JOB_KIND_HMO_ITEM_BULK_APPROVE,
    JOB_KIND_WIKIDATA_ITEM_BULK_APPROVE,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.run_job_params import prepare_job_params
from app.pipeline.studio_item_bulk_approve import MAX_BULK_APPROVE_IDS, bulk_approve_items
from app.pipeline.studio_item_bulk_approve_job import run_studio_item_bulk_approve_job


def _fake_auth() -> AsyncMock:
    auth = AsyncMock()
    auth.user.id = uuid.uuid4()
    auth.kek = b"x" * 32
    return auth


@pytest.mark.asyncio
async def test_bulk_approve_params_require_local_ids(db_session, sample_run) -> None:
    with pytest.raises(HTTPException) as exc:
        await prepare_job_params(
            db_session,
            _fake_auth(),
            run_id=sample_run["run_id"],
            kind=JOB_KIND_HMO_ITEM_BULK_APPROVE,
            params={},
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_bulk_approve_params_cap_local_ids(db_session, sample_run) -> None:
    too_many = [f"id-{i}" for i in range(MAX_BULK_APPROVE_IDS + 1)]
    with pytest.raises(HTTPException) as exc:
        await prepare_job_params(
            db_session,
            _fake_auth(),
            run_id=sample_run["run_id"],
            kind=JOB_KIND_WIKIDATA_ITEM_BULK_APPROVE,
            params={"local_ids": too_many},
        )
    assert exc.value.status_code == 400
    assert "max" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_bulk_approve_params_dedupe_and_clean(db_session, sample_run) -> None:
    merged = await prepare_job_params(
        db_session,
        _fake_auth(),
        run_id=sample_run["run_id"],
        kind=JOB_KIND_HMO_ITEM_BULK_APPROVE,
        params={"local_ids": [" A ", "A", "B", "", "B"]},
    )
    assert merged["local_ids"] == ["A", "B"]
    assert merged["approved"] is True


@pytest.mark.asyncio
async def test_bulk_approve_hmo_items(db_session, sample_run) -> None:
    run_id = sample_run["run_id"]
    user_id = sample_run["user_id"]

    existing = HmoStudioItemOverride(
        run_id=run_id,
        local_id="QDraft_Already",
        approved=True,
        updated_by=user_id,
    )
    pending = HmoStudioItemOverride(
        run_id=run_id,
        local_id="QDraft_Pending",
        approved=None,
        updated_by=user_id,
    )
    db_session.add_all([existing, pending])
    await db_session.commit()

    result = await bulk_approve_items(
        db_session,
        run_id=run_id,
        channel="hmo",
        local_ids=["QDraft_Already", "QDraft_Pending", "QDraft_New"],
        actor_id=user_id,
    )
    assert result["approved"] == 2
    assert result["unchanged"] == 1
    assert result["failed"] == 0

    rows = (
        await db_session.execute(
            select(HmoStudioItemOverride).where(HmoStudioItemOverride.run_id == run_id)
        )
    ).scalars().all()
    by_id = {r.local_id: r.approved for r in rows}
    assert by_id["QDraft_Already"] is True
    assert by_id["QDraft_Pending"] is True
    assert by_id["QDraft_New"] is True


@pytest.mark.asyncio
async def test_bulk_approve_wikidata_job_runner(db_session, sample_run, monkeypatch) -> None:
    run_id = sample_run["run_id"]
    project_id = sample_run["project_id"]
    user_id = sample_run["user_id"]

    db_session.add(WikidataItemOverride(
        run_id=run_id,
        local_id="manuscript::A",
        approved=False,
        updated_by=user_id,
    ))
    job = RunJob(
        project_id=project_id,
        run_id=run_id,
        kind=JOB_KIND_WIKIDATA_ITEM_BULK_APPROVE,
        status="running",
        params={"local_ids": ["manuscript::A", "person::B"]},
        created_by=user_id,
        progress={},
    )
    db_session.add(job)
    await db_session.commit()
    job_id = job.id

    async def _noop_progress(*_a, **_k):
        return None

    async def _never_cancel(_jid):
        return False

    monkeypatch.setattr(
        "app.pipeline.studio_item_bulk_approve_job.update_job_progress",
        _noop_progress,
    )
    monkeypatch.setattr(
        "app.pipeline.studio_item_bulk_approve_job.is_cancel_requested",
        _never_cancel,
    )

    await run_studio_item_bulk_approve_job(job_id)

    db_session.expire_all()
    refreshed = await db_session.get(RunJob, job_id)
    assert refreshed is not None
    assert refreshed.status == JOB_STATUS_SUCCEEDED
    assert refreshed.result is not None
    assert refreshed.result["approved"] == 2

    rows = (
        await db_session.execute(
            select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
        )
    ).scalars().all()
    assert {r.local_id for r in rows} == {"manuscript::A", "person::B"}
    assert all(r.approved is True for r in rows)
