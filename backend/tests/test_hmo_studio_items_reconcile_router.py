"""Router tests for POST /runs/{run_id}/hmo-studio/items/{local_id}/reconcile."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.pipeline.hmo_item_reconcile import ReconcileOutcome, ReconciliationUnavailableError
from converter.wikibase.resolved_models import ResolvedWikibaseEntity


async def _seed_build_cache(db_session, run_id) -> None:
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


@pytest.mark.asyncio
async def test_reconcile_returns_404_for_unknown_local_id(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    await _seed_build_cache(db_session, run_id)

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/items/NotAnItem/reconcile"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reconcile_reports_not_found(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    await _seed_build_cache(db_session, run_id)

    with patch(
        "app.routers.hmo_studio_items.reconcile_item",
        AsyncMock(return_value=ReconcileOutcome(found=False, message="no match")),
    ):
        response = await sample_run["client"].post(
            f"/api/runs/{run_id}/hmo-studio/items/QDraft_MS1/reconcile"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_found"
    assert body["wikibase_id"] is None


@pytest.mark.asyncio
async def test_reconcile_adopts_found_item_and_records_mapping(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    await _seed_build_cache(db_session, run_id)

    with patch(
        "app.routers.hmo_studio_items.reconcile_item",
        AsyncMock(return_value=ReconcileOutcome(found=True, wikibase_id="Q555")),
    ):
        response = await sample_run["client"].post(
            f"/api/runs/{run_id}/hmo-studio/items/QDraft_MS1/reconcile"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "adopted"
    assert body["wikibase_id"] == "Q555"

    # A second call now short-circuits on the "already_mapped" branch
    # since the mapping row was recorded by the first call.
    response2 = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/items/QDraft_MS1/reconcile"
    )
    assert response2.status_code == 200
    assert response2.json()["status"] == "already_mapped"
    assert response2.json()["wikibase_id"] == "Q555"


@pytest.mark.asyncio
async def test_reconcile_returns_503_when_unavailable(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    await _seed_build_cache(db_session, run_id)

    with patch(
        "app.routers.hmo_studio_items.reconcile_item",
        AsyncMock(side_effect=ReconciliationUnavailableError("SPARQL endpoint unreachable")),
    ):
        response = await sample_run["client"].post(
            f"/api/runs/{run_id}/hmo-studio/items/QDraft_MS1/reconcile"
        )

    assert response.status_code == 503
