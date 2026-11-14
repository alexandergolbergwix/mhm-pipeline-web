from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.event import ProjectEvent
from app.models.item_override import WikidataItemOverride
from app.models.run import RunRecord
from app.routers import wikidata_studio


@pytest.mark.asyncio
async def test_source_choice_needs_no_item_approval_and_records_its_event(
    sample_run, db_session, monkeypatch
):
    client, run_id = sample_run["client"], sample_run["run_id"]
    db_session.add(
        RunRecord(
            run_id=run_id,
            control_number="990001",
            marc={
                "001": "990001",
                "942$a": "Library A|Library B",
                "942$z": "Ms 1|Ms 2",
            },
        )
    )
    await db_session.commit()
    monkeypatch.setattr(
        wikidata_studio,
        "fetch_merged_wikidata_item",
        AsyncMock(
            return_value={
                "local_id": "ms:1",
                "entity_type": "manuscript",
                "records": ["990001"],
                "approved": None,
            }
        ),
    )
    response = await client.get(f"/api/runs/{run_id}/wikidata-studio/source-review?local_id=ms:1")
    assert response.status_code == 200
    record = response.json()["records"][0]
    assert len(record["requirements"]) == 9
    holding = next(
        row
        for row in record["candidates"]
        if row["kind"] == "holding" and row["shelfmark"] == "Ms 2"
    )
    request = {
        "local_id": "ms:1",
        "control_number": "990001",
        "candidate_key": holding["candidate_key"],
        "expected_source_hash": record["source_hash"],
        "evidence_uri": "https://www.nli.org.il/test",
        "reason": "The source identifies the current library.",
    }
    saved = await client.post(f"/api/runs/{run_id}/wikidata-studio/source-choice", json=request)
    assert saved.status_code == 200, saved.text
    assert saved.json()["rebuild_required"] is True
    row = (
        await db_session.execute(
            select(WikidataItemOverride).where(
                WikidataItemOverride.run_id == run_id,
                WikidataItemOverride.local_id == "ms:1",
            )
        )
    ).scalar_one()
    assert row.approved is None
    assert row.source_review_choices[0]["candidate"]["shelfmark"] == "Ms 2"
    events = (
        (
            await db_session.execute(
                select(ProjectEvent).where(ProjectEvent.entity_id == str(row.id))
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].state["source_review_choices"] == row.source_review_choices
    exported = await client.get(f"/api/projects/{sample_run['project_id']}/export")
    assert exported.status_code == 200
    exported_row = next(
        entry for entry in exported.json()["wikidata_overrides"] if entry["local_id"] == "ms:1"
    )
    assert exported_row["source_review_choices"] == row.source_review_choices
    from app.schemas.export import ExportWikidataOverride

    assert (
        ExportWikidataOverride.model_validate(exported_row).source_review_choices
        == row.source_review_choices
    )
    other = next(
        candidate
        for candidate in record["candidates"]
        if candidate["kind"] == "holding" and candidate["shelfmark"] == "Ms 1"
    )
    request = {**request, "candidate_key": other["candidate_key"]}
    replaced = await client.post(f"/api/runs/{run_id}/wikidata-studio/source-choice", json=request)
    assert replaced.status_code == 200
    assert len(replaced.json()["source_review_choices"]) == 1
    cleared = await client.post(
        f"/api/runs/{run_id}/wikidata-studio/source-choice", json={**request, "discard": True}
    )
    assert cleared.status_code == 200
    await db_session.refresh(row)
    assert row.source_review_choices == []


@pytest.mark.asyncio
async def test_source_choice_rejects_a_stale_source_and_generic_injection(
    sample_run, db_session, monkeypatch
):
    client, run_id = sample_run["client"], sample_run["run_id"]
    db_session.add(
        RunRecord(
            run_id=run_id, control_number="990001", marc={"001": "990001", "500$a": "Source text"}
        )
    )
    await db_session.commit()
    monkeypatch.setattr(
        wikidata_studio,
        "fetch_merged_wikidata_item",
        AsyncMock(
            return_value={
                "local_id": "ms:1",
                "entity_type": "manuscript",
                "records": ["990001"],
            }
        ),
    )
    rejected = await client.post(
        f"/api/runs/{run_id}/wikidata-studio/source-choice",
        json={
            "local_id": "ms:1",
            "control_number": "990001",
            "candidate_key": "a" * 64,
            "expected_source_hash": "b" * 64,
            "evidence_uri": "https://www.nli.org.il/test",
            "reason": "The source identifies the current library.",
        },
    )
    assert rejected.status_code == 409
    injected = await client.patch(
        f"/api/runs/{run_id}/wikidata-studio/items/ms:1",
        json={"source_review_choices": [{"target_qid": "Q5"}]},
    )
    assert injected.status_code == 422
    monkeypatch.setattr(wikidata_studio, "fetch_merged_wikidata_item", AsyncMock(return_value=None))
    outside = await client.get(f"/api/runs/{run_id}/wikidata-studio/source-review?local_id=unknown")
    assert outside.status_code == 409
