"""Modal dispatch for heavy job kinds (Rule W-237).

The web process stays the claimer/arbiter; Modal is optional compute.
Every failure mode degrades to local execution (Rule W-15). The wait is
event-driven: the Modal container POSTs the completion webhook and the
poller wakes via ``notify_modal_finished`` — no busy waiting.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import httpx
import pytest

from app.models.run_job import (
    JOB_KIND_RDF_BUILD,
    JOB_KIND_WIKIDATA_VERIFY,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline import modal_job_client
from app.pipeline.modal_job_client import run_on_modal
from app.pipeline.run_job_service import _now

WORKER_ID = "web.1:test"


def _settings(monkeypatch, *, url="https://test--mhm-jobs-run.modal.run", token="tok") -> None:
    from app.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "modal_jobs_url", url, raising=False)
    monkeypatch.setattr(settings, "modal_jobs_token", token, raising=False)
    # The lease hand-off requires claimed_by == WORKER_ID — pin the real
    # constant to the test value so _add_running_job's claim matches.
    monkeypatch.setattr("app.pipeline.run_job_service.WORKER_ID", WORKER_ID)


class _Resp:
    status_code = 202
    text = ""

    def json(self):
        return {}


class _Client:
    """httpx.AsyncClient stub recording dispatches, answering 202."""

    dispatches: list[dict] = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, *a, **kw):
        _Client.dispatches.append({"url": url})
        return _Resp()


def _install_client(monkeypatch) -> list[dict]:
    _Client.dispatches = []
    monkeypatch.setattr(modal_job_client.httpx, "AsyncClient", _Client)
    return _Client.dispatches


def _fast_safety_tick(monkeypatch) -> None:
    monkeypatch.setattr(modal_job_client, "_SAFETY_TICK_S", 0.02)


async def _add_running_job(db, sample_run, *, claimed_by=WORKER_ID):
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind=JOB_KIND_RDF_BUILD,
        status=JOB_STATUS_RUNNING,
        params={},
        progress={},
        claimed_by=claimed_by,
        created_by=sample_run["user_id"],
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def _finish_soon(db, job, delay: float = 0.05) -> None:
    """Simulate the Modal container finalizing the row + webhook wake."""
    await asyncio.sleep(delay)
    job.status = JOB_STATUS_SUCCEEDED
    job.progress = {"phase": "done", "message": "RDF build complete"}
    await db.commit()
    modal_job_client.notify_modal_finished(job.id)


@pytest.mark.asyncio
async def test_run_on_modal_disabled_without_url(db_session, sample_run, monkeypatch) -> None:
    _settings(monkeypatch, url="")
    job = await _add_running_job(db_session, sample_run)
    assert await run_on_modal(job.id, sample_run["run_id"], JOB_KIND_RDF_BUILD) is False


@pytest.mark.asyncio
async def test_run_on_modal_kind_not_eligible(db_session, sample_run, monkeypatch) -> None:
    _settings(monkeypatch)
    job = await _add_running_job(db_session, sample_run)
    assert await run_on_modal(job.id, sample_run["run_id"], "wikidata_studio_build") is False


@pytest.mark.asyncio
async def test_run_on_modal_dispatch_error_falls_back(db_session, sample_run, monkeypatch) -> None:
    _settings(monkeypatch)
    job = await _add_running_job(db_session, sample_run)

    class _Boom:
        def __init__(self, *a, **kw):
            raise httpx.ConnectError("modal unreachable")

    monkeypatch.setattr(modal_job_client.httpx, "AsyncClient", _Boom)
    assert await run_on_modal(job.id, sample_run["run_id"], JOB_KIND_RDF_BUILD) is False


@pytest.mark.asyncio
async def test_run_on_modal_rejected_status_falls_back(db_session, sample_run, monkeypatch) -> None:
    _settings(monkeypatch)
    job = await _add_running_job(db_session, sample_run)

    class _Rejected:
        status_code = 409
        text = "job is cancelled, expected running"

        def json(self):
            return {}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return _Rejected()

    monkeypatch.setattr(modal_job_client.httpx, "AsyncClient", _Client)
    assert await run_on_modal(job.id, sample_run["run_id"], JOB_KIND_RDF_BUILD) is False


@pytest.mark.asyncio
async def test_run_on_modal_accepts_200_ok_body(db_session, sample_run, monkeypatch) -> None:
    """The real Modal endpoint answers 200 {"ok": true, "spawned": true};
    a 200 with ok=true must count as an accepted dispatch (2026-09-17:
    the 202-only check rejected every real dispatch and double-ran the
    job locally + on Modal)."""
    _settings(monkeypatch)
    _fast_safety_tick(monkeypatch)
    job = await _add_running_job(db_session, sample_run)
    dispatches = _install_client(monkeypatch)

    class _Resp200:
        status_code = 200
        text = '{"ok":true,"spawned":true}'

        def json(self):
            return {"ok": True, "spawned": True}

    class _Client200:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            _Client.dispatches.append({"url": kw.get("url", "")})
            return _Resp200()

    monkeypatch.setattr(modal_job_client.httpx, "AsyncClient", _Client200)

    async def _finish():
        await asyncio.sleep(0.05)
        job.status = JOB_STATUS_SUCCEEDED
        await db_session.commit()
        modal_job_client.notify_modal_finished(job.id)

    finisher = asyncio.create_task(_finish())
    assert await run_on_modal(job.id, sample_run["run_id"], JOB_KIND_RDF_BUILD) is True
    await finisher
    assert len(dispatches) == 1


@pytest.mark.asyncio
async def test_run_on_modal_wakes_on_webhook_and_relays_progress(
    db_session, sample_run, monkeypatch,
) -> None:
    _settings(monkeypatch)
    _fast_safety_tick(monkeypatch)
    job = await _add_running_job(db_session, sample_run)
    dispatches = _install_client(monkeypatch)

    async def _finish():
        await _finish_soon(db_session, job)

    runner = asyncio.create_task(run_on_modal(
        job.id, sample_run["run_id"], JOB_KIND_RDF_BUILD,
        on_progress=_finish,  # doubles as the progress relay recorder
    ))
    await _finish()
    relayed: list[dict] = []
    # The relay target IS _finish; collect what it saw via a second waiter.
    result = await runner
    assert result is True
    assert dispatches and "modal-event" not in dispatches[0]["url"]


@pytest.mark.asyncio
async def test_fresh_modal_executor_prevents_rival_dispatch(
    db_session, sample_run, monkeypatch,
) -> None:
    """W-237: a live modal container's lease must not be re-dispatched —
    the web process waits for the container's own webhook instead. This is
    what stops the double-writer progress jumps (4400/260/4465/300)."""
    _settings(monkeypatch)
    _fast_safety_tick(monkeypatch)
    job = await _add_running_job(
        db_session, sample_run, claimed_by="modal-executor:live1",
    )
    dispatches = _install_client(monkeypatch)

    async def _finish():
        await asyncio.sleep(0.08)
        job.status = JOB_STATUS_SUCCEEDED
        job.progress = {"phase": "done"}
        await db_session.commit()
        modal_job_client.notify_modal_finished(job.id)

    finisher = asyncio.create_task(_finish())
    assert await run_on_modal(job.id, sample_run["run_id"], JOB_KIND_RDF_BUILD) is True
    await finisher
    assert dispatches == [], "a live modal lease must not be re-dispatched"


@pytest.mark.asyncio
async def test_stale_modal_executor_is_taken_over(
    db_session, sample_run, monkeypatch,
) -> None:
    """A dead container's stale executor claim may be re-dispatched."""
    _settings(monkeypatch)
    _fast_safety_tick(monkeypatch)
    job = await _add_running_job(
        db_session, sample_run, claimed_by="modal-executor:dead1",
    )
    job.updated_at = _now() - timedelta(seconds=600)
    await db_session.commit()

    dispatches = _install_client(monkeypatch)

    async def _finish():
        await asyncio.sleep(0.05)
        job.status = JOB_STATUS_SUCCEEDED
        await db_session.commit()
        modal_job_client.notify_modal_finished(job.id)

    finisher = asyncio.create_task(_finish())
    assert await run_on_modal(job.id, sample_run["run_id"], JOB_KIND_RDF_BUILD) is True
    await finisher
    assert len(dispatches) == 1
