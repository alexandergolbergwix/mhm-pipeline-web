"""Tests for wikibase_cloud_writes audit logging."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.wikibase_cloud_write import (
    CHANNEL_ITEM_UPLOAD,
    OPERATION_ADOPT,
    OPERATION_CREATE,
    OPERATION_UPDATE,
    TARGET_ITEM,
    WikibaseCloudWrite,
)
from app.services.wikibase_audit import (
    WikibaseAuditContext,
    fetch_latest_wikibase_writes,
    record_wikibase_write,
)


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


@pytest.mark.asyncio
async def test_fetch_latest_wikibase_writes_returns_only_the_newest_row_per_target(
    db_session, sample_run,
) -> None:
    """A target_key written twice (e.g. create then a later update) must
    surface only the newest row — the review table shows the *current*
    outcome, not the full history (that lives in ProjectEvent/history)."""
    run_id = sample_run["run_id"]
    actor_id = sample_run["user_id"]
    now = datetime.now(timezone.utc)

    db_session.add_all([
        WikibaseCloudWrite(
            actor_user_id=actor_id, run_id=run_id, channel=CHANNEL_ITEM_UPLOAD,
            operation=OPERATION_ADOPT, target_kind=TARGET_ITEM,
            target_key="http://example.org#MS1", wikibase_id="Q1",
            outcome_message="adopted via reconcile: found live",
            created_at=now - timedelta(minutes=5),
        ),
        WikibaseCloudWrite(
            actor_user_id=actor_id, run_id=run_id, channel=CHANNEL_ITEM_UPLOAD,
            operation=OPERATION_UPDATE, target_kind=TARGET_ITEM,
            target_key="http://example.org#MS1", wikibase_id="Q1",
            outcome_message="ok", created_at=now,
        ),
        WikibaseCloudWrite(
            actor_user_id=actor_id, run_id=run_id, channel=CHANNEL_ITEM_UPLOAD,
            operation=OPERATION_CREATE, target_kind=TARGET_ITEM,
            target_key="http://example.org#Person1", wikibase_id="Q2",
            outcome_message="ok", created_at=now,
        ),
    ])
    await db_session.commit()

    latest = await fetch_latest_wikibase_writes(
        db_session, run_id, channel=CHANNEL_ITEM_UPLOAD, target_kind=TARGET_ITEM,
    )

    assert set(latest.keys()) == {
        "http://example.org#MS1", "http://example.org#Person1",
    }
    # MS1's most recent write is the update, not the earlier adopt.
    assert latest["http://example.org#MS1"].operation == OPERATION_UPDATE
    assert latest["http://example.org#Person1"].operation == OPERATION_CREATE


@pytest.mark.asyncio
async def test_fetch_latest_wikibase_writes_scopes_by_channel_and_target_kind(
    db_session, sample_run,
) -> None:
    run_id = sample_run["run_id"]
    actor_id = sample_run["user_id"]
    db_session.add(
        WikibaseCloudWrite(
            actor_user_id=actor_id, run_id=run_id, channel="other_channel",
            operation=OPERATION_CREATE, target_kind=TARGET_ITEM,
            target_key="http://example.org#MS1", wikibase_id="Q1",
        )
    )
    await db_session.commit()

    latest = await fetch_latest_wikibase_writes(
        db_session, run_id, channel=CHANNEL_ITEM_UPLOAD, target_kind=TARGET_ITEM,
    )
    assert latest == {}
