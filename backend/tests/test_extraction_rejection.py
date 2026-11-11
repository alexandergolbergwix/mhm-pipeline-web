"""Explicit rejection remains distinct from an unreviewed prediction."""

import json

import pytest
from app.models.extraction_approval import ExtractionApproval
from sqlalchemy import select


@pytest.mark.asyncio
async def test_reject_survives_list_read_and_approve_clears_it(sample_run, db_session) -> None:
    from app.routers.extraction import _entity_id

    run_id = sample_run["run_id"]
    entity_id = _entity_id(
        control_number=sample_run["control_number"],
        source="contents_ner",
        text="ספר הדרושים",
        start=0,
        end=13,
    )
    url = f"/api/runs/{run_id}/extraction/entities/{entity_id}"
    response = await sample_run["client"].patch(url, json={"approved": False, "rejected": True})
    assert response.status_code == 200
    assert response.json()["rejected"] is True
    row = (
        await db_session.execute(
            select(ExtractionApproval).where(
                ExtractionApproval.run_id == run_id,
                ExtractionApproval.text == "ספר הדרושים",
            )
        )
    ).scalar_one()
    assert row.rejected is True
    listed = await sample_run["client"].get(f"/api/runs/{run_id}/extraction/entities")
    assert any(
        entity["rejected"] for entity in listed.json()["entities"] if entity["id"] == entity_id
    )
    approved = await sample_run["client"].patch(url, json={"approved": True})
    assert approved.status_code == 200
    assert approved.json()["approved"] is True
    assert approved.json()["rejected"] is False


@pytest.mark.asyncio
async def test_unapprove_is_pending_and_conflicting_decisions_are_invalid(sample_run) -> None:
    from app.routers.extraction import _entity_id

    run_id = sample_run["run_id"]
    entity_id = _entity_id(
        control_number=sample_run["control_number"],
        source="contents_ner",
        text="ספר הדרושים",
        start=0,
        end=13,
    )
    url = f"/api/runs/{run_id}/extraction/entities/{entity_id}"
    pending = await sample_run["client"].patch(url, json={"approved": False})
    assert pending.status_code == 200
    assert pending.json()["rejected"] is False
    invalid = await sample_run["client"].patch(url, json={"approved": True, "rejected": True})
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_rejection_export_import_and_legacy_import_preserve_decision(sample_run) -> None:
    from app.routers.extraction import _entity_id

    client = sample_run["client"]
    run_id = sample_run["run_id"]
    entity_id = _entity_id(
        control_number=sample_run["control_number"],
        source="contents_ner",
        text="ספר הדרושים",
        start=0,
        end=13,
    )
    base = f"/api/runs/{run_id}/extraction"
    await client.patch(f"{base}/entities/{entity_id}", json={"rejected": True})
    exported = await client.get(f"{base}/export?format=json")
    assert exported.status_code == 200
    payload = exported.json()
    rows = payload if isinstance(payload, list) else payload["entities"]
    rejected = next(row for row in rows if row["text"] == "ספר הדרושים")
    assert rejected["rejected"] is True
    response = await client.post(
        f"{base}/import",
        files={
            "file": ("review.json", json.dumps([rejected]), "application/json"),
        },
    )
    assert response.status_code == 200
    assert not response.json()["errors"]
    legacy = {key: value for key, value in rejected.items() if key != "rejected"}
    response = await client.post(
        f"{base}/import",
        files={
            "file": ("legacy.json", json.dumps([legacy]), "application/json"),
        },
    )
    assert response.status_code == 200
    listed = await client.get(f"{base}/entities")
    assert next(row for row in listed.json()["entities"] if row["id"] == entity_id)["rejected"]
    rejected["approved"] = True
    invalid = await client.post(
        f"{base}/import",
        files={
            "file": ("invalid.json", json.dumps([rejected]), "application/json"),
        },
    )
    assert invalid.status_code == 200
    assert invalid.json()["skipped"] == 1
    assert invalid.json()["errors"]


@pytest.mark.asyncio
async def test_fresh_rejected_import_can_later_receive_approval(sample_run, db_session) -> None:
    client = sample_run["client"]
    run_id = sample_run["run_id"]
    row = {
        "control_number": sample_run["control_number"],
        "source": "manual",
        "text": "New source work",
        "approved": False,
        "rejected": True,
    }
    url = f"/api/runs/{run_id}/extraction/import"
    response = await client.post(
        url,
        files={
            "file": ("review.json", json.dumps([row]), "application/json"),
        },
    )
    assert response.status_code == 200
    assert response.json()["imported"] == 1
    entity = (
        await db_session.execute(
            select(ExtractionApproval).where(
                ExtractionApproval.run_id == run_id,
                ExtractionApproval.text == row["text"],
            )
        )
    ).scalar_one()
    assert entity.rejected and not entity.approved
    row["approved"] = True
    row.pop("rejected")
    approved = await client.post(
        url,
        files={
            "file": ("review.json", json.dumps([row]), "application/json"),
        },
    )
    assert approved.status_code == 200
    await db_session.refresh(entity)
    assert entity.approved and not entity.rejected


@pytest.mark.asyncio
async def test_bulk_rejection_changes_the_cache_fingerprint_and_blocks_auto_approval(
    sample_run, db_session
) -> None:
    from pathlib import Path

    from app.pipeline.extraction_entities_cache import compute_entities_fingerprint
    from app.routers.extraction import _entity_id

    run_id = sample_run["run_id"]
    entity_id = _entity_id(
        control_number=sample_run["control_number"],
        source="contents_ner",
        text="ספר הדרושים",
        start=0,
        end=13,
    )
    before = await compute_entities_fingerprint(db_session, run_id, Path("/nonexistent/ner.json"))
    base = f"/api/runs/{run_id}/extraction/entities"
    rejected = await sample_run["client"].post(
        f"{base}/bulk-approve",
        json={
            "entity_ids": [entity_id],
            "approved": False,
            "rejected": True,
        },
    )
    assert rejected.status_code == 200
    after = await compute_entities_fingerprint(db_session, run_id, Path("/nonexistent/ner.json"))
    assert after != before
    applied = await sample_run["client"].post(
        f"{base}/auto-approve",
        json={
            "sources": ["contents_ner"],
            "types": ["WORK"],
            "min_confidence": 0,
        },
    )
    assert applied.status_code == 200
    row = (
        await db_session.execute(
            select(ExtractionApproval).where(
                ExtractionApproval.run_id == run_id,
                ExtractionApproval.text == "ספר הדרושים",
            )
        )
    ).scalar_one()
    await db_session.refresh(row)
    assert row.rejected and not row.approved
