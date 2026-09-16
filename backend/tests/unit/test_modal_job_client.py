"""Modal dispatch for heavy job kinds (Rule W-237).

The web process stays the claimer/arbiter; Modal is optional compute.
Every failure mode degrades to local execution (Rule W-15).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import httpx
import pytest

from app.models.run_job import (
    JOB_KIND_RDF_BUILD,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline import modal_job_client
from app.pipeline.modal_job_client import run_on_modal


def _settings(monkeypatch, *, url="https://test--mhm-jobs-run.modal.run", token="tok") -> None:
    from app.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "modal_jobs_url", url, raising=False)
    monkeypatch.setattr(settings, "modal_jobs_token", token, raising=False)


@pytest.mark.asyncio
async def test_run_on_modal_disabled_without_url(db_session, sample_run, monkeypatch) -> None:
    _settings(monkeypatch, url="")
    job = await _add_running_job(db_session, sample_run)
    assert await run_on_modal(job.id, JOB_KIND_RDF_BUILD) is False


@pytest.mark.asyncio
async def test_run_on_modal_kind_not_eligible(db_session, sample_run, monkeypatch) -> None:
    _settings(monkeypatch)
    job = await _add_running_job(db_session, sample_run)
    assert await run_on_modal(job.id, "wikidata_studio_build") is False


@pytest.mark.asyncio
async def test_run_on_modal_dispatch_error_falls_back(db_session, sample_run, monkeypatch) -> None:
    _settings(monkeypatch)
    job = await _add_running_job(db_session, sample_run)

    class _Boom:
        def __init__(self, *a, **kw):
            raise httpx.ConnectError("modal unreachable")

    monkeypatch.setattr(modal_job_client.httpx, "AsyncClient", _Boom)
    assert await run_on_modal(job.id, JOB_KIND_RDF_BUILD) is False


@pytest.mark.asyncio
async def test_run_on_modal_rejected_status_falls_back(db_session, sample_run, monkeypatch) -> None:
    _settings(monkeypatch)
    job = await _add_running_job(db_session, sample_run)

    class _Resp:
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
            return _Resp()

    monkeypatch.setattr(modal_job_client.httpx, "AsyncClient", _Client)
    assert await run_on_modal(job.id, JOB_KIND_RDF_BUILD) is False


@pytest.mark.asyncio
async def test_run_on_modal_polls_until_terminal(db_session, sample_run, monkeypatch) -> None:
    _settings(monkeypatch)
    job = await _add_running_job(db_session, sample_run)

    class _Resp:
        status_code = 202
        text = ""

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
            return _Resp()

    monkeypatch.setattr(modal_job_client.httpx, "AsyncClient", _Client)

    # First poll tick: Modal has not reported yet. Second tick: the
    # detached runner finalized the row as succeeded.
    flips = {"n": 0}

    async def _fake_sleep(_seconds: float) -> None:
        flips["n"] += 1
        if flips["n"] == 1:
            job.status = JOB_STATUS_SUCCEEDED
            job.progress = {"phase": "done", "message": "RDF build complete"}
            await db_session.commit()

    monkeypatch.setattr(modal_job_client.asyncio, "sleep", _fake_sleep)

    progress_seen: list[dict] = []

    async def _on_progress(payload: dict) -> None:
        progress_seen.append(dict(payload))

    assert await run_on_modal(job.id, JOB_KIND_RDF_BUILD, on_progress=_on_progress) is True
    assert progress_seen, "the terminal progress state must be relayed"


@pytest.mark.asyncio
async def _add_running_job(db, sample_run):
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind=JOB_KIND_RDF_BUILD,
        status=JOB_STATUS_RUNNING,
        params={},
        progress={},
        created_by=sample_run["user_id"],
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job
