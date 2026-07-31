"""Wikidata verify scope prefers Studio cache and skips SPARQL reconcile."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.routers.wikidata_studio import _fetch_wikidata_verify_items


def _fake_session() -> MagicMock:
    """A fake AsyncSession whose `rollback()` can be awaited (Rule W-40)."""
    session = MagicMock()
    session.rollback = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_verify_fetch_canonicalises_quoted_control_numbers() -> None:
    """Quoted DB control numbers must still join clean Studio record_ids."""
    run_id = uuid.uuid4()
    cached = SimpleNamespace(
        result_items=[
            {
                "local_id": "ms:1",
                "entity_type": "manuscript",
                "labels": {"he": "כותרת"},
                "records": ["990000000000000099"],
                "hmo_wikibase_id": "Q11",
                "authority_evidence": [
                    {"kind": "viaf", "identifier": "999", "accepted": True},
                ],
                "statements": [],
            },
        ],
    )
    db = _fake_session()
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4()))

    with (
        patch(
            "app.pipeline.marc_verify_context.load_run_control_numbers",
            new=AsyncMock(return_value={"990000000000000099"}),
        ),
        patch(
            "app.pipeline.marc_verify_context.load_run_marc_records_scoped",
            new=AsyncMock(return_value=[
                {"title": "כותרת", "_control_number": "990000000000000099"},
            ]),
        ),
        patch(
            "app.routers.wikidata_studio._get_studio_cache_row",
            new=AsyncMock(return_value=cached),
        ),
        patch(
            "app.routers.wikidata_studio.execute_studio_build",
            new=AsyncMock(side_effect=AssertionError("must not rebuild")),
        ),
    ):
        items, marc = await _fetch_wikidata_verify_items(
            db, run_id, auth,
            item_ids=None,
            approved_only=True,
            source="canonical",
        )

    assert len(items) == 1
    assert items[0]["record_ids"] == ["990000000000000099"]
    assert marc[0]["_control_number"] == "990000000000000099"
    assert items[0]["verify_evidence"]["marc_present"] is True
    assert items[0]["verify_evidence"]["viaf"]["authority_rows"][0]["identifier"] == "999"
    assert items[0]["verify_evidence"]["hmo_wikibase"]["hmo_wikibase_id"] == "Q11"
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
    db = _fake_session()
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4()))

    with (
        patch(
            "app.pipeline.marc_verify_context.load_run_control_numbers",
            new=AsyncMock(return_value={"990000000000000001"}),
        ),
        patch(
            "app.pipeline.marc_verify_context.load_run_marc_records_scoped",
            new=AsyncMock(return_value=[]),
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
async def test_verify_fetch_skips_non_public_entity_types() -> None:
    run_id = uuid.uuid4()
    cached = SimpleNamespace(
        result_items=[
            {"local_id": "QDraft_Person_a", "entity_type": "person", "records": []},
            {
                "local_id": "QDraft_CU_1",
                "entity_type": "Codicological_Unit",
                "records": ["990000000000000001"],
            },
        ],
    )
    db = _fake_session()
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4()))

    with (
        patch(
            "app.pipeline.marc_verify_context.load_run_control_numbers",
            new=AsyncMock(return_value=set()),
        ),
        patch(
            "app.pipeline.marc_verify_context.load_run_marc_records_scoped",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.routers.wikidata_studio._get_studio_cache_row",
            new=AsyncMock(return_value=cached),
        ),
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
            approved_only=True,
            source="canonical",
        )

    assert len(items) == 1
    assert items[0]["entity_type"] == "person"


@pytest.mark.asyncio
async def test_verify_fetch_rebuilds_without_reconcile_when_cache_empty() -> None:
    run_id = uuid.uuid4()
    built = SimpleNamespace(result_items=[{"local_id": "ms:1", "entity_type": "manuscript"}])
    db = _fake_session()
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4()))

    with (
        patch(
            "app.pipeline.marc_verify_context.load_run_control_numbers",
            new=AsyncMock(return_value=set()),
        ),
        patch(
            "app.pipeline.marc_verify_context.load_run_marc_records_scoped",
            new=AsyncMock(return_value=[]),
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


@pytest.mark.asyncio
async def test_verify_fetch_ends_its_transaction_before_external_io() -> None:
    """Rule W-40 — no transaction may stay open across the probe / LLM calls.

    Postgres closes a connection idle in a transaction past
    `idle_in_transaction_session_timeout` (2 min). The duplicate probe plus LLM
    extraction take minutes, so leaving the scope transaction open killed the
    connection and the next statement raised
    `InterfaceError: connection is closed`.
    """
    run_id = uuid.uuid4()
    order: list[str] = []
    cached = SimpleNamespace(
        result_items=[
            {
                "local_id": "ms:1",
                "entity_type": "manuscript",
                "labels": {"he": "כותרת"},
                "records": ["990000000000000099"],
                "statements": [],
            },
        ],
    )
    db = _fake_session()
    db.rollback = AsyncMock(side_effect=lambda: order.append("rollback"))
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4()))

    async def fake_probe(_db, _items, **_kwargs):
        order.append("duplicate_probe")
        return {}

    async def fake_extract(_factory, _items, **_kwargs):
        order.append("llm_extract")
        return {}

    with (
        patch(
            "app.pipeline.marc_verify_context.load_run_control_numbers",
            new=AsyncMock(return_value={"990000000000000099"}),
        ),
        patch(
            "app.pipeline.marc_verify_context.load_run_marc_records_scoped",
            new=AsyncMock(return_value=[
                {"title": "כותרת", "_control_number": "990000000000000099"},
            ]),
        ),
        patch(
            "app.routers.wikidata_studio._get_studio_cache_row",
            new=AsyncMock(return_value=cached),
        ),
        patch(
            "app.pipeline.wikidata_duplicate_probe.attach_duplicate_evidence",
            new=fake_probe,
        ),
        patch(
            "app.pipeline.marc_llm_extract.attach_llm_proposals",
            new=fake_extract,
        ),
    ):
        await _fetch_wikidata_verify_items(
            db, run_id, auth, item_ids=None, approved_only=True, source="canonical",
        )

    assert "rollback" in order, "scope transaction was never released"
    assert order.index("rollback") < order.index("duplicate_probe")
    assert order.index("rollback") < order.index("llm_extract")
