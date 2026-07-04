"""Tests for wikibase_cloud_writes audit logging."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.wikibase_cloud_write import (
    CHANNEL_ITEM_UPLOAD,
    OPERATION_CREATE,
    TARGET_ITEM,
    WikibaseCloudWrite,
)
from app.services.wikibase_audit import WikibaseAuditContext, record_wikibase_write


@pytest.mark.asyncio
async def test_record_wikibase_write_persists_actor(db_session, sample_run) -> None:
    actor_id = sample_run["user_id"]
    run_id = sample_run["run_id"]
    project_id = sample_run["project_id"]
    ctx = WikibaseAuditContext(
        actor_user_id=actor_id,
        project_id=project_id,
        run_id=run_id,
        channel=CHANNEL_ITEM_UPLOAD,
    )
    await record_wikibase_write(
        db_session, ctx,
        operation=OPERATION_CREATE,
        target_kind=TARGET_ITEM,
        target_key="http://example.org#MS1",
        wikibase_id="Q42",
    )
    row = (
        await db_session.execute(
            select(WikibaseCloudWrite).where(WikibaseCloudWrite.actor_user_id == actor_id)
        )
    ).scalar_one()
    assert row.project_id == project_id
    assert row.run_id == run_id
    assert row.channel == CHANNEL_ITEM_UPLOAD
    assert row.operation == OPERATION_CREATE
    assert row.target_kind == TARGET_ITEM
    assert row.target_key == "http://example.org#MS1"
    assert row.wikibase_id == "Q42"
    assert row.outcome_message == "ok"


@pytest.mark.asyncio
async def test_record_wikibase_write_swallows_db_errors(db_session, monkeypatch) -> None:
    ctx = WikibaseAuditContext(
        actor_user_id=uuid.uuid4(),
        channel=CHANNEL_ITEM_UPLOAD,
    )

    async def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db_session, "commit", _boom)
    await record_wikibase_write(
        db_session, ctx,
        operation=OPERATION_CREATE,
        target_kind=TARGET_ITEM,
        target_key="x",
    )
