"""Tests for the background HMO item upload job (progress + cancel)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from fastapi import HTTPException

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline import hmo_item_upload_job as job_module
from converter.wikibase.resolved_models import (
    DeferredItemLink,
    ResolvedClaim,
    ResolvedWikibaseEntity,
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

    def add_claim(self, entity_id, claim):
        return _FakeOutcome(entity_id=entity_id, status="updated")

    def get_entity(self, entity_id):
        return {
            "id": entity_id,
            "labels": {"en": f"Live {entity_id}"},
            "descriptions": {},
            "aliases": {},
            "claims": {},
        }

    def update_item(self, entity_id, **kwargs):
        return _FakeOutcome(entity_id=entity_id, status="updated")


def _entities(n: int = 2) -> list[ResolvedWikibaseEntity]:
    person = ResolvedWikibaseEntity(
        local_id="QDraft_Person1",
        labels={"en": "Test Scribe"},
        descriptions={"en": "a scribe"},
        class_qid="Q2",
        source_uri="http://example.org#Person1",
    )
    manuscripts = [
        ResolvedWikibaseEntity(
            local_id=f"QDraft_MS{i}",
            labels={"en": f"Test MS {i}"},
            descriptions={"en": "a manuscript"},
            class_qid="Q1",
            source_uri=f"http://example.org#MS{i}",
            claims=[ResolvedClaim("P1", "string", f"shelfmark {i}")],
            deferred_links=[DeferredItemLink(f"QDraft_MS{i}", "P2", "QDraft_Person1")],
        )
        for i in range(n - 1)
    ]
    return [*manuscripts, person]


async def _seed_cache(db_session, sample_run, entities) -> None:
    db_session.add(
        HmoStudioItemCache(
            run_id=sample_run["run_id"],
            input_fingerprint="0" * 64,
            resolved_entities=[e.to_dict() for e in entities],
            entity_count=len(entities),
            deferred_link_count=sum(len(e.deferred_links) for e in entities),
            skipped_statement_count=0,
        )
    )
    await db_session.commit()


async def _seed_job(db_session, sample_run, *, params: dict | None = None) -> uuid.UUID:
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind="hmo_item_upload",
        status="queued",
        params=params or {},
        progress={},
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job.id


async def _seed(db_session, sample_run, entities, *, params: dict | None = None) -> uuid.UUID:
    await _seed_cache(db_session, sample_run, entities)
    return await _seed_job(db_session, sample_run, params=params)


@pytest.mark.asyncio
async def test_job_fails_fast_without_server_oauth(sample_run, db_session, monkeypatch):
    monkeypatch.setattr(
        job_module,
        "build_server_wikibase_writer",
        lambda: (_ for _ in ()).throw(HTTPException(status_code=503, detail="not configured")),
    )
    job_id = await _seed(db_session, sample_run, _entities())

    await job_module.run_hmo_item_upload_job(job_id)

    job = await db_session.get(RunJob, job_id)
    assert job.status == JOB_STATUS_FAILED
    assert "not configured" in (job.error or "").lower()


@pytest.mark.asyncio
async def test_job_fails_cleanly_when_no_build_exists(sample_run, db_session, monkeypatch):
    monkeypatch.setattr(job_module, "build_server_wikibase_writer", lambda: _FakeWriter())
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind="hmo_item_upload",
        status="queued",
        params={},
        progress={},
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    await job_module.run_hmo_item_upload_job(job.id)

    refreshed = await db_session.get(RunJob, job.id)
    await db_session.refresh(refreshed)
    assert refreshed.status == JOB_STATUS_FAILED
    assert "build-items" in (refreshed.error or "")


@pytest.mark.asyncio
async def test_job_reports_progress_and_succeeds(sample_run, db_session, monkeypatch):
    monkeypatch.setattr(job_module, "build_server_wikibase_writer", lambda: _FakeWriter())
    job_id = await _seed(db_session, sample_run, _entities(3))

    await job_module.run_hmo_item_upload_job(job_id)

    job = await db_session.get(RunJob, job_id)
    assert job.status == JOB_STATUS_SUCCEEDED
    assert job.result["created"] == 3
    assert job.result["linked"] == 2
    assert job.result["failed"] == 0
    assert len(job.result["outcomes"]) == 3
    assert len(job.result["link_outcomes"]) == 2
    assert job.progress["phase"] == "done"
    # 3 items + 2 links, both passes counted against one shared total.
    assert job.progress["processed"] == job.progress["total"] == 5


@pytest.mark.asyncio
async def test_job_updates_existing_items_when_requested(sample_run, db_session, monkeypatch):
    monkeypatch.setattr(job_module, "build_server_wikibase_writer", lambda: _FakeWriter())
    job_id = await _seed(db_session, sample_run, _entities(3))
    await job_module.run_hmo_item_upload_job(job_id)

    second_job_id = await _seed_job(db_session, sample_run, params={"update_existing": True})
    await job_module.run_hmo_item_upload_job(second_job_id)

    job = await db_session.get(RunJob, second_job_id)
    assert job.status == JOB_STATUS_SUCCEEDED
    assert job.result["created"] == 0
    assert job.result["updated"] == 3
    assert job.result["failed"] == 0
    assert all(o["status"] == "updated" for o in job.result["outcomes"])


@pytest.mark.asyncio
async def test_job_stops_when_cancelled(sample_run, db_session, monkeypatch):
    monkeypatch.setattr(job_module, "build_server_wikibase_writer", lambda: _FakeWriter())
    job_id = await _seed(db_session, sample_run, _entities(5))

    calls = {"n": 0}

    async def fake_is_cancelled(jid):
        calls["n"] += 1
        return calls["n"] > 2

    monkeypatch.setattr(job_module, "is_cancel_requested", fake_is_cancelled)

    await job_module.run_hmo_item_upload_job(job_id)

    job = await db_session.get(RunJob, job_id)
    assert job.status == JOB_STATUS_CANCELLED
    assert job.result["created"] < 5
    assert job.progress["phase"] == "cancelled"
