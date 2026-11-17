from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.pipeline.wikidata_reference_repair_job import (
    ReferenceJobCancelledError,
    assert_admin_job,
    checkpoint_for,
)
from app.publication.reference_queue_lease import reference_queue_lease


def test_checkpoint_keeps_only_the_completed_prefix_and_pending_exact_plan(tmp_path):
    plan = {"authorized_qids": ["Q456"], "before_entities": {"Q456": {"lastrevid": 9}}}
    import json

    (tmp_path / "plan-Q456.json").write_text(json.dumps(plan))
    completed = [{"qid": "Q123", "status": "verified", "corrected": True}]
    saved = checkpoint_for("Q456", completed, tmp_path)
    assert saved["current_plan"] == plan
    assert saved["results"] == completed
    paused = checkpoint_for(
        "Q456", completed + [{"qid": "Q456", "status": "pre_send_retryable"}], tmp_path
    )
    assert paused["results"] == completed
    assert paused["current_plan"] == plan
    assert paused["halted_status"] == "pre_send_retryable"
    finished = checkpoint_for(
        "Q456", completed + [{"qid": "Q456", "status": "verified", "corrected": True}], tmp_path
    )
    assert finished["current_plan"] is None


@pytest.mark.asyncio
async def test_current_role_and_cancel_state_gate_each_send():
    from types import SimpleNamespace

    actor = "actor"
    job = SimpleNamespace(created_by=actor, status="running", cancel_requested_at=None)
    user = SimpleNamespace(role="admin")
    db = SimpleNamespace(
        get=AsyncMock(side_effect=lambda model, identity, **kw: job if identity == "job" else user)
    )
    await assert_admin_job(db, "job", actor)
    user.role = "editor"
    with pytest.raises(ValueError, match="administrator"):
        await assert_admin_job(db, "job", actor)
    user.role = "admin"
    job.cancel_requested_at = "now"
    with pytest.raises(ReferenceJobCancelledError):
        await assert_admin_job(db, "job", actor)


@pytest.mark.asyncio
async def test_postgres_lease_refuses_another_host_and_releases_after_failure():
    from types import SimpleNamespace

    connection = SimpleNamespace(
        execution_options=AsyncMock(),
        scalar=AsyncMock(return_value=False),
        execute=AsyncMock(),
    )
    connection.execution_options.return_value = connection

    @asynccontextmanager
    async def connect():
        yield connection

    engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"), connect=connect)
    with pytest.raises(ValueError, match="Another host"):
        async with reference_queue_lease(engine, "run"):
            pytest.fail("A second host received the lease")
    connection.execute.assert_not_called()
    connection.scalar.return_value = True
    with pytest.raises(RuntimeError):
        async with reference_queue_lease(engine, "run"):
            raise RuntimeError("The worker stopped")
    connection.execute.assert_awaited_once()
    connection.execution_options.assert_awaited_with(isolation_level="AUTOCOMMIT")
