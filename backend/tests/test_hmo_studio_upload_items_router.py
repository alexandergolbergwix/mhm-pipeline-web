"""Router tests for POST /runs/{run_id}/hmo-studio/upload-items and
GET .../item-status (Phase 5 — see dev-docs/hmo-wikibase-studio-plan.md).
"""

from __future__ import annotations

import pytest

from app.models.hmo_studio_item_cache import HmoStudioItemCache
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
