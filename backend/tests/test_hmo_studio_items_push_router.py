"""Router tests for POST /runs/{run_id}/hmo-studio/items/{local_id}/push.

Pins: a curator can push exactly one item live (e.g. right after applying
an AI-suggested fix) without re-running the whole corpus upload.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.pipeline.hmo_item_reconcile import ReconcileOutcome
from converter.wikibase.resolved_models import ResolvedWikibaseEntity


@dataclass
class _FakeOutcome:
    local_id: str
    source_uri: str
    status: str
    wikibase_id: str | None = None
    message: str = ""


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
async def test_push_returns_404_for_unknown_local_id(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    await _seed_build_cache(db_session, run_id)

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/items/NotAnItem/push"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_push_returns_409_when_no_build_exists(sample_run) -> None:
    run_id = sample_run["run_id"]

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/items/QDraft_MS1/push"
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_push_creates_item_and_returns_outcome(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    await _seed_build_cache(db_session, run_id)

    fake_outcome = _FakeOutcome(
        local_id="QDraft_MS1", source_uri="http://example.org#MS1",
        status="created", wikibase_id="Q123",
    )

    with (
        patch(
            "app.services.wikibase_credentials.build_server_wikibase_writer",
            MagicMock(return_value=MagicMock()),
        ),
        patch(
            "app.pipeline.hmo_item_reconcile.resolve_source_uri_pid",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.pipeline.hmo_item_upload.push_single_item",
            AsyncMock(return_value=fake_outcome),
        ) as push_mock,
    ):
        response = await sample_run["client"].post(
            f"/api/runs/{run_id}/hmo-studio/items/QDraft_MS1/push"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "created"
    assert body["wikibase_id"] == "Q123"
    push_mock.assert_awaited_once()
    call_kwargs = push_mock.call_args.kwargs
    # A curator pushing a single fixed item always wants the current
    # override-merged state applied live, even to an already-mapped item.
    assert call_kwargs["update_existing"] is True
    assert call_kwargs["existing_qid"] is None


@pytest.mark.asyncio
async def test_push_returns_503_when_wikibase_cloud_not_configured(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    await _seed_build_cache(db_session, run_id)

    from fastapi import HTTPException, status as http_status

    def _raise_unconfigured():
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Wikibase Cloud is not configured on this server.",
        )

    with patch(
        "app.services.wikibase_credentials.build_server_wikibase_writer",
        MagicMock(side_effect=_raise_unconfigured),
    ):
        response = await sample_run["client"].post(
            f"/api/runs/{run_id}/hmo-studio/items/QDraft_MS1/push"
        )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_push_passes_existing_qid_when_item_already_mapped(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    await _seed_build_cache(db_session, run_id)

    from app.models.wikibase_entity_mapping import ENTITY_KIND_INSTANCE, WikibaseEntityMapping

    db_session.add(
        WikibaseEntityMapping(
            ontology_uri="http://example.org#MS1",
            entity_kind=ENTITY_KIND_INSTANCE,
            wikibase_id="Q999",
            run_id=run_id,
            label="Test MS",
        )
    )
    await db_session.commit()

    fake_outcome = _FakeOutcome(
        local_id="QDraft_MS1", source_uri="http://example.org#MS1",
        status="updated", wikibase_id="Q999",
    )

    with (
        patch(
            "app.services.wikibase_credentials.build_server_wikibase_writer",
            MagicMock(return_value=MagicMock()),
        ),
        patch(
            "app.pipeline.hmo_item_upload.push_single_item",
            AsyncMock(return_value=fake_outcome),
        ) as push_mock,
    ):
        response = await sample_run["client"].post(
            f"/api/runs/{run_id}/hmo-studio/items/QDraft_MS1/push"
        )

    assert response.status_code == 200
    assert response.json()["status"] == "updated"
    call_kwargs = push_mock.call_args.kwargs
    assert call_kwargs["existing_qid"] == "Q999"
    # Already mapped -> no reconcile PID lookup needed.
    assert call_kwargs["reconcile_pid"] is None
