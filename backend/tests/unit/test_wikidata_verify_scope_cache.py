"""Wikidata verify scope prefers Studio cache and skips SPARQL reconcile."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.routers.wikidata_studio import _fetch_wikidata_verify_items


@pytest.mark.asyncio
async def test_verify_fetch_uses_existing_cache_without_rebuild() -> None:
    run_id = uuid.uuid4()
    cached = SimpleNamespace(
        result_items=[
            {
                "local_id": "QDraft_Person_a",
                "entity_type": "person",
                "labels": {"en": "A"},
                "records": ["990000000000000001"],
            },
        ],
    )
    db = MagicMock()
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4()))

    with (
        patch(
            "app.routers.wikidata_studio.select",
            return_value=MagicMock(),
        ),
        patch.object(
            db, "execute", new=AsyncMock(return_value=MagicMock(
                scalars=lambda: MagicMock(all=lambda: [
                    SimpleNamespace(control_number="990000000000000001", marc={}),
                ]),
            )),
        ),
        patch(
            "app.routers.wikidata_studio._get_studio_cache_row",
            new=AsyncMock(return_value=cached),
        ) as get_cache,
        patch(
            "app.routers.wikidata_studio.execute_studio_build",
            new=AsyncMock(side_effect=AssertionError("must not rebuild")),
        ) as build,
        patch(
            "app.routers.wikidata_studio.attach_local_reference_targets",
            lambda items: items,
        ),
        patch(
            "app.routers.wikidata_studio.record_ids_for_wikidata_item",
            return_value=["990000000000000001"],
        ),
    ):
        items, _marc = await _fetch_wikidata_verify_items(
            db, run_id, auth,
            item_ids=["QDraft_Person_a"],
            approved_only=True,
            source="canonical",
        )

    assert len(items) == 1
    assert items[0]["_local_id"] == "QDraft_Person_a"
    get_cache.assert_awaited()
    build.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_fetch_rebuilds_without_reconcile_when_cache_empty() -> None:
    run_id = uuid.uuid4()
    built = SimpleNamespace(result_items=[{"local_id": "ms:1", "entity_type": "manuscript"}])
    db = MagicMock()
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4()))

    with (
        patch("app.routers.wikidata_studio.select", return_value=MagicMock()),
        patch.object(
            db, "execute", new=AsyncMock(return_value=MagicMock(
                scalars=lambda: MagicMock(all=lambda: []),
            )),
        ),
        patch(
            "app.routers.wikidata_studio._get_studio_cache_row",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.routers.wikidata_studio.execute_studio_build",
            new=AsyncMock(return_value=built),
        ) as build,
        patch(
            "app.routers.wikidata_studio.attach_local_reference_targets",
            lambda items: items,
        ),
        patch(
            "app.routers.wikidata_studio.record_ids_for_wikidata_item",
            return_value=[],
        ),
    ):
        items, _marc = await _fetch_wikidata_verify_items(
            db, run_id, auth,
            item_ids=None,
            approved_only=False,
            source="canonical",
        )

    assert len(items) == 1
    assert build.await_args.kwargs["reconcile"] is False
