"""Router tests for POST /runs/{run_id}/hmo-studio/upload-items and
GET .../item-status (Phase 5 — see dev-docs/hmo-wikibase-studio-plan.md).
"""

from __future__ import annotations

import uuid

import pytest

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.run_job import RunJob
from app.models.wikibase_entity_mapping import ENTITY_KIND_INSTANCE, WikibaseEntityMapping
from converter.wikibase.resolved_models import ResolvedWikibaseEntity


@pytest.fixture(autouse=True)
def no_server_wikibase_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_ID", "")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_SECRET", "")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN", "")
    from app.settings import get_settings

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_upload_items_requires_a_build_first(sample_run) -> None:
    run_id = sample_run["run_id"]

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/upload-items", json={"dry_run": True}
    )
    assert response.status_code == 409
    assert "build-items" in response.json()["detail"]


@pytest.mark.asyncio
async def test_dry_run_upload_needs_no_credentials(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    entity = ResolvedWikibaseEntity(
        local_id="QDraft_MS1",
        labels={"en": "Test MS"},
        descriptions={"en": "a manuscript"},
        class_qid="Q1",
        source_uri="http://example.org#MS1",
    )
    db_session.add(
        HmoStudioItemCache(
            run_id=run_id,
            input_fingerprint="0" * 64,
            resolved_entities=[entity.to_dict()],
            entity_count=1,
        )
    )
    await db_session.commit()

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/upload-items", json={"dry_run": True}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["outcomes"][0]["status"] == "would_create"
    # Regression: the "Would create: N" headline the frontend renders
    # comes straight off this field — it must count would_create
    # outcomes, not just live writes (see hmo_item_upload.py).
    assert body["created"] == 1


@pytest.mark.asyncio
async def test_dry_run_update_existing_reports_would_update(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    entity = ResolvedWikibaseEntity(
        local_id="QDraft_MS1",
        labels={"en": "Test MS"},
        descriptions={"en": "a manuscript"},
        class_qid="Q1",
        source_uri="http://example.org#MS1",
    )
    db_session.add(
        HmoStudioItemCache(
            run_id=run_id,
            input_fingerprint="0" * 64,
            resolved_entities=[entity.to_dict()],
            entity_count=1,
        )
    )
    db_session.add(
        WikibaseEntityMapping(
            ontology_uri=entity.source_uri,
            entity_kind=ENTITY_KIND_INSTANCE,
            wikibase_id="Q42",
            run_id=run_id,
            label="Test MS",
        )
    )
    await db_session.commit()

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/upload-items",
        json={"dry_run": True, "update_existing": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcomes"][0]["status"] == "would_update"
    assert body["outcomes"][0]["wikibase_id"] == "Q42"
    assert body["updated"] == 1
    assert body["created"] == 0
    assert body["skipped"] == 0


@pytest.mark.asyncio
async def test_live_upload_without_server_oauth_is_rejected(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    db_session.add(
        HmoStudioItemCache(
            run_id=run_id, input_fingerprint="0" * 64, resolved_entities=[], entity_count=0,
        )
    )
    await db_session.commit()

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/upload-items", json={"dry_run": False}
    )
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_live_upload_spawns_background_job(
    sample_run, db_session, monkeypatch,
) -> None:
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_ID", "id")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN", "token")
    from app.settings import get_settings

    get_settings.cache_clear()
    # Keep the job row queued — this test pins the router contract, not
    # the worker (covered by test_hmo_item_upload_job.py).
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", lambda job_id: None)

    run_id = sample_run["run_id"]
    db_session.add(
        HmoStudioItemCache(
            run_id=run_id, input_fingerprint="0" * 64, resolved_entities=[], entity_count=0,
        )
    )
    await db_session.commit()

    first = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/upload-items",
        json={"dry_run": False, "update_existing": True},
    )
    assert first.status_code == 201
    body = first.json()
    assert body["kind"] == "hmo_item_upload"
    assert body["status"] == "queued"

    job_row = await db_session.get(RunJob, uuid.UUID(body["id"]))
    assert job_row is not None
    assert job_row.params.get("update_existing") is True

    second = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/upload-items", json={"dry_run": False}
    )
    assert second.status_code == 409
    assert second.json()["detail"]["job_id"] == body["id"]


@pytest.mark.asyncio
async def test_item_status_before_and_after_build(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]

    before = await sample_run["client"].get(f"/api/runs/{run_id}/hmo-studio/item-status")
    assert before.status_code == 200
    assert before.json()["build_present"] is False

    db_session.add(
        HmoStudioItemCache(
            run_id=run_id, input_fingerprint="0" * 64, resolved_entities=[], entity_count=3,
            deferred_link_count=1,
        )
    )
    await db_session.commit()

    after = await sample_run["client"].get(f"/api/runs/{run_id}/hmo-studio/item-status")
    body = after.json()
    assert body["build_present"] is True
    assert body["entity_count"] == 3
    assert body["deferred_link_count"] == 1
    assert body["uploaded_count"] == 0
