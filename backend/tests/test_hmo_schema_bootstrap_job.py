"""Tests for the background HMO schema bootstrap job (progress + cancel).

See dev-docs/hmo-wikibase-studio-plan.md and the user-reported "live
bootstrap looks like it does nothing" issue this closes — the live pass
now runs as a run_jobs background task instead of blocking the HTTP
request past Heroku's 30s timeout.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from app.models.run_job import JOB_STATUS_CANCELLED, JOB_STATUS_FAILED, JOB_STATUS_SUCCEEDED, RunJob
from app.pipeline import hmo_schema_bootstrap_job as job_module
from converter.wikibase.ontology_schema_reader import OntologyClassEntry, OntologySchema


def _tiny_schema(n: int = 3) -> OntologySchema:
    return OntologySchema(
        classes=[
            OntologyClassEntry(
                uri=f"http://example.org#Class{i}",
                local_name=f"Class{i}",
                label=f"Class {i}",
                description="A class.",
            )
            for i in range(n)
        ],
        properties=[],
    )


@dataclass
class _FakeOutcome:
    entity_id: str | None
    status: str = "created"
    message: str = "ok"


class _FakeWriter:
    def __init__(self) -> None:
        self._next_q = 1

    def create_item(self, **kwargs):
        qid = f"Q{self._next_q}"
        self._next_q += 1
        return _FakeOutcome(entity_id=qid)

    def create_property(self, **kwargs):
        return _FakeOutcome(entity_id=None, status="failed", message="unused")


async def _seed_job(
    db_session, run_id, project_id, *, username="u", password="p",  # noqa: S107
) -> uuid.UUID:
    job = RunJob(
        project_id=project_id,
        run_id=run_id,
        kind="hmo_schema_bootstrap",
        status="queued",
        params={
            "dry_run": False,
            "_wikibase_bot_username": username,
            "_wikibase_bot_password": password,
        },
        progress={},
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job.id


@pytest.mark.asyncio
async def test_job_fails_fast_without_credentials(sample_run, db_session, monkeypatch):
    monkeypatch.setattr(
        "converter.wikibase.ontology_schema_reader.read_hmo_schema", lambda: _tiny_schema()
    )
    job_id = await _seed_job(
        db_session, sample_run["run_id"], sample_run["project_id"], username="", password="",
    )

    await job_module.run_hmo_schema_bootstrap_job(job_id)

    job = await db_session.get(RunJob, job_id)
    assert job.status == JOB_STATUS_FAILED
    assert "credentials" in (job.error or "").lower()


@pytest.mark.asyncio
async def test_job_reports_progress_and_succeeds(sample_run, db_session, monkeypatch):
    monkeypatch.setattr(
        "converter.wikibase.ontology_schema_reader.read_hmo_schema", lambda: _tiny_schema(3)
    )
    fake_writer = _FakeWriter()
    monkeypatch.setattr(
        "converter.wikibase.cloud_client.WikibaseCloudWriter",
        lambda *a, **k: fake_writer,
    )
    job_id = await _seed_job(db_session, sample_run["run_id"], sample_run["project_id"])

    await job_module.run_hmo_schema_bootstrap_job(job_id)

    job = await db_session.get(RunJob, job_id)
    assert job.status == JOB_STATUS_SUCCEEDED
    assert job.result == {"created": 3, "skipped": 0, "failed": 0}
    assert job.progress["phase"] == "done"
    assert job.progress["processed"] == job.progress["total"] == 3


@pytest.mark.asyncio
async def test_job_stops_when_cancelled(sample_run, db_session, monkeypatch):
    monkeypatch.setattr(
        "converter.wikibase.ontology_schema_reader.read_hmo_schema", lambda: _tiny_schema(5)
    )
    fake_writer = _FakeWriter()
    monkeypatch.setattr(
        "converter.wikibase.cloud_client.WikibaseCloudWriter",
        lambda *a, **k: fake_writer,
    )
    job_id = await _seed_job(db_session, sample_run["run_id"], sample_run["project_id"])

    calls = {"n": 0}
    real_is_cancelled = job_module.is_cancel_requested

    async def fake_is_cancelled(jid):
        calls["n"] += 1
        return calls["n"] > 2  # cancel after a couple of progress checks

    monkeypatch.setattr(job_module, "is_cancel_requested", fake_is_cancelled)

    await job_module.run_hmo_schema_bootstrap_job(job_id)

    job = await db_session.get(RunJob, job_id)
    assert job.status == JOB_STATUS_CANCELLED
    assert job.result["created"] < 5

    monkeypatch.setattr(job_module, "is_cancel_requested", real_is_cancelled)
