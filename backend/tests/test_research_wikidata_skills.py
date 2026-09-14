"""Wikidata upload skills: DB → 'wikidata-uploads', API → 'wikidata-items'."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest


pytestmark = pytest.mark.asyncio


async def _mint(sample_run) -> dict:
    session = await sample_run["client"].post(
        "/api/research-agent/sessions",
        json={"run_id": str(sample_run["run_id"])},
    )
    assert session.status_code == 200, session.text
    return {"headers": {"Authorization": f"Bearer {session.json()['tool_grant']}"},
            "thread_id": session.json()["thread_id"], "session": session.json()}


async def _call(sample_run, headers, name, arguments=None):
    resp = await sample_run["client"].post(
        "/api/research-agent/tools",
        headers=headers,
        json={"name": name, "arguments": arguments or {}},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


async def test_uploaded_items_reads_db(sample_run, db_session):
    """The sample run's AuthorityMatch carries wikidata_qid=Q127398 — the
    skill surfaces QIDs from the DB records, not from live Wikidata."""
    minted = await _mint(sample_run)
    with patch(
        "app.routers.wikidata_studio.studio_items_for_project",
        new=AsyncMock(return_value=[
            {
                "local_id": "ms-1", "existing_qid": "Q1111", "entity_type": "manuscript",
                "labels": {"en": "Ms Jerusalem 8"}, "statements": [1, 2, 3],
            },
            {
                "local_id": "p-1", "existing_qid": "Q127398", "entity_type": "person",
                "labels": {"en": "Moses Maimonides"}, "statements": [1],
            },
            {  # not uploaded — no QID yet
                "local_id": "w-1", "existing_qid": None, "entity_type": "work",
                "labels": {"en": "Guide"}, "statements": [1],
            },
        ]),
    ):
        result = await _call(sample_run, minted["headers"], "wikidata_uploaded_items", {})
    assert result["uploaded_count"] == 2
    assert result["by_type"] == {"manuscript": 1, "person": 1}
    assert result["artifact_key"] == "wikidata-uploads"


async def test_fetch_items_uses_wikidata_api(sample_run, db_session):
    """The skill batches wbgetentities and saves one row per claim property."""
    minted = await _mint(sample_run)
    # Seed the uploads dataset directly.
    await _call(sample_run, minted["headers"], "canvas_upsert_artifact", {
        "artifact_key": "wikidata-uploads",
        "kind": "sparql",
        "title": "Uploaded to Wikidata",
        "content": {
            "columns": ["qid", "local_id", "entity_type", "label", "statements"],
            "rows": [["Q1111", "ms-1", "manuscript", "Ms Jerusalem 8", 3],
                     ["Q127398", "p-1", "person", "Maimonides", 5]],
        },
    })
    slim = {
        "Q1111": {"id": "Q1111", "labels": {"en": "Ms Jerusalem 8"},
                  "claim_properties": ["P31", "P3959"], "claim_count": 2},
        "Q127398": {"id": "Q127398", "labels": {"en": "Moses Maimonides"},
                    "claim_properties": ["P31", "P569", "P570"], "claim_count": 3},
    }
    with patch(
        "app.services.research_agent.wiki.fetch_wikidata_entities_batch",
        new=AsyncMock(return_value=slim),
    ):
        result = await _call(sample_run, minted["headers"], "wikidata_fetch_items", {})
    assert result["fetched"] == 2
    assert result["distinct_properties"] == 4
    assert result["claim_rows"] == 5
    # The dataset artifact now holds the claim rows.
    info = await _call(sample_run, minted["headers"], "data_info", {"artifact_key": "wikidata-items"})
    assert info["row_count"] == 5
    distinct = await _call(sample_run, minted["headers"], "data_distinct", {
        "artifact_key": "wikidata-items", "column": "property",
    })
    counts = {v["value"]: v["count"] for v in distinct["values"]}
    assert counts["P31"] == 2 and counts["P3959"] == 1 and counts["P569"] == 1


async def test_fetch_items_without_uploads_is_404(sample_run):
    minted = await _mint(sample_run)
    resp = await sample_run["client"].post(
        "/api/research-agent/tools",
        headers=minted["headers"],
        json={"name": "wikidata_fetch_items", "arguments": {}},
    )
    assert resp.status_code == 404


async def test_uploaded_items_without_studio_cache_is_empty(sample_run):
    minted = await _mint(sample_run)
    with patch(
        "app.routers.wikidata_studio.studio_items_for_project",
        new=AsyncMock(return_value=[]),
    ):
        result = await _call(sample_run, minted["headers"], "wikidata_uploaded_items", {})
    assert result["uploaded_count"] == 0


async def test_uploaded_items_includes_publication_execution_qids(sample_run, db_session):
    """Succeeded publication execution actions are the upload ground truth
    (Rule R26) — their QIDs surface even with an empty Studio cache."""
    from app.models.publication import (
        Publication,
        PublicationExecution,
        PublicationExecutionAction,
    )

    publication = Publication(
        run_id=sample_run["run_id"],
        source_snapshot_id="snap-1",
        source_revision="rev-1",
        source_digest="d" * 64,
        profile_name="default",
        profile_version="1",
        target_site="https://www.wikidata.org",
        target_environment="live",
        state="executed",
        idempotency_key="uploads-test-1",
        latest_release_id=uuid.uuid4(),
    )
    db_session.add(publication)
    await db_session.flush()
    execution = PublicationExecution(
        id=uuid.uuid4(),
        publication_id=publication.id,
        plan_id=uuid.uuid4(),
        receipt_id=uuid.uuid4(),
        receipt_digest="r" * 64,
        actor_id=str(sample_run["user_id"]),
        idempotency_key="uploads-test-1-exec",
        status="succeeded",
        total_count=2,
        succeeded_count=1,
    )
    db_session.add(execution)
    db_session.add(PublicationExecutionAction(
        execution_id=execution.id,
        action_key="a-1",
        entity_key="ms-1",
        ordinal=0,
        phase="send",
        state="succeeded",
        action="create",
        result_qid="Q2222",
    ))
    db_session.add(PublicationExecutionAction(  # failed → never surfaces
        execution_id=execution.id,
        action_key="a-2",
        entity_key="p-1",
        ordinal=1,
        phase="send",
        state="failed",
        action="create",
    ))
    await db_session.commit()

    minted = await _mint(sample_run)
    with patch(
        "app.routers.wikidata_studio.studio_items_for_project",
        new=AsyncMock(return_value=[]),
    ) as items_mock:
        result = await _call(sample_run, minted["headers"], "wikidata_uploaded_items", {})
    assert result["uploaded_count"] == 1
    assert result["by_type"] == {"unknown": 1}
    # Run ids must cross the boundary as str — asyncpg pgproto.UUID breaks
    # uuid.UUID() downstream on Postgres (AttributeError 'replace').
    for call in items_mock.call_args_list:
        for run_id in call.args[0]:
            assert isinstance(run_id, str)
    info = await _call(sample_run, minted["headers"], "data_info", {"artifact_key": "wikidata-uploads"})
    assert info["row_count"] == 1
    row = (info.get("sample") or [[None] * 6])[0]
    assert row[0] == "Q2222" and row[5] == "publication:create"
