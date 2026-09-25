"""`_canonical_missing_detail` — the curator-facing WHY for an empty
durable canonical store.

The Wikidata Studio build fails closed when `hmo_canonical_entities` is
empty for the run. The detail must say what actually blocks the canonical
read-back: with live mappings present, the last succeeded upload job's
failed/unresolved counts name the gate (`hmo_item_upload.py` persists
canonical state only after a fully clean two-pass upload).
"""

from __future__ import annotations

import uuid

import pytest

from app.models.hmo_studio_item_row import HmoStudioItemRow
from app.models.run_job import (
    JOB_KIND_HMO_ITEM_UPLOAD,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.models.wikibase_entity_mapping import (
    ENTITY_KIND_INSTANCE,
    WikibaseEntityMapping,
)
from app.routers.wikidata_studio import _canonical_missing_detail
from converter.wikibase.resolved_models import (
    DeferredItemLink,
    ResolvedClaim,
    ResolvedWikibaseEntity,
)

CANONICAL_PREFIX = "no durable HMO canonical entities"


def _entity(n: int = 1) -> ResolvedWikibaseEntity:
    return ResolvedWikibaseEntity(
        local_id=f"QDraft_MS{n}",
        labels={"en": f"MS {n}"},
        descriptions={"en": "a manuscript"},
        class_qid="Q1",
        source_uri=f"http://example.org#MS{n}",
        claims=[ResolvedClaim("P1", "string", f"shelfmark {n}")],
        deferred_links=[DeferredItemLink(f"QDraft_MS{n}", "P2", "QDraft_Person1")],
    )


async def _seed_mapping(db_session, sample_run, qid: str, uri: str) -> None:
    db_session.add(
        WikibaseEntityMapping(
            ontology_uri=uri,
            entity_kind=ENTITY_KIND_INSTANCE,
            wikibase_id=qid,
            run_id=sample_run["run_id"],
            label=f"Test MS {qid}",
        )
    )
    await db_session.commit()


async def _seed_upload_job(db_session, sample_run, *, result: dict) -> uuid.UUID:
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind=JOB_KIND_HMO_ITEM_UPLOAD,
        status=JOB_STATUS_SUCCEEDED,
        params={"dry_run": False},
        progress={},
        result=result,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job.id


@pytest.mark.asyncio
async def test_detail_names_last_upload_failures(db_session, sample_run):
    """uploaded>0 with a dirty last upload → counts + zero-clean gate note."""
    await _seed_mapping(db_session, sample_run, "Q100", "http://example.org#MS1")
    await _seed_upload_job(
        db_session,
        sample_run,
        result={
            "created": 1,
            "failed": 2104,
            "unresolved_links": 16912,
            "cancelled": False,
        },
    )

    detail = await _canonical_missing_detail(db_session, sample_run["run_id"])

    assert detail.startswith(CANONICAL_PREFIX)
    assert "15095" not in detail  # counts come from this run's data, not the prod run
    assert "1 live instances" in detail
    assert "2104 failed item(s)" in detail
    assert "16912 unresolved link(s)" in detail
    assert "persists only after a fully clean upload" in detail
    assert "fix those in HMO Studio" in detail


@pytest.mark.asyncio
async def test_detail_without_gate_note_when_last_upload_clean(db_session, sample_run):
    await _seed_mapping(db_session, sample_run, "Q100", "http://example.org#MS1")
    await _seed_upload_job(
        db_session,
        sample_run,
        result={
            "created": 1,
            "failed": 0,
            "unresolved_links": 0,
            "cancelled": False,
        },
    )

    detail = await _canonical_missing_detail(db_session, sample_run["run_id"])

    assert detail.startswith(CANONICAL_PREFIX)
    assert "failed item" not in detail
    assert "re-upload in HMO Studio" in detail


@pytest.mark.asyncio
async def test_detail_built_not_uploaded(db_session, sample_run):
    db_session.add(
        HmoStudioItemRow(
            run_id=sample_run["run_id"],
            local_id="QDraft_MS1",
            label_en="MS 1",
        )
    )
    await db_session.commit()

    detail = await _canonical_missing_detail(db_session, sample_run["run_id"])

    assert detail.startswith(CANONICAL_PREFIX)
    assert "built but not yet uploaded" in detail


@pytest.mark.asyncio
async def test_detail_no_build_at_all(db_session, sample_run):
    detail = await _canonical_missing_detail(db_session, sample_run["run_id"])

    assert detail.startswith(CANONICAL_PREFIX)
    assert "no HMO Studio item build exists" in detail
