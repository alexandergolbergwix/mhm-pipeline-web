"""Wikidata verify heap release + scoped MARC (Rule W-132)."""

from __future__ import annotations

from app.pipeline.marc_verify_context import load_run_marc_records_scoped
from app.pipeline.wikidata_verify_fixture import (
    release_wikidata_verify_heap,
    slim_item_for_verdict_persist,
)


def test_slim_item_for_verdict_persist_drops_bulk_statements() -> None:
    heavy = {
        "_local_id": "ms:1",
        "local_id": "ms:1",
        "entity_type": "manuscript",
        "labels": {"he": "כותרת"},
        "statements": [{"property": "P31", "value": "Q87167"}] * 100,
        "verify_evidence": {"marc": {"title": "x"}, "marc_present": True},
        "_marc_context": {"title": "x"},
    }
    slim = slim_item_for_verdict_persist(heavy)
    assert len(slim["statements"]) == 40
    assert "marc" not in slim["verify_evidence"]
    assert slim["_marc_context"]["title"] == "x"


def test_release_wikidata_verify_heap_replaces_items_and_clears_marc() -> None:
    item = {
        "_local_id": "ms:1",
        "local_id": "ms:1",
        "entity_type": "manuscript",
        "labels": {"he": "כותרת"},
        "statements": [{"property": "P31", "value": "Q87167"}] * 50,
        "verify_evidence": {"marc_present": True},
        "_marc_context": {},
    }
    items = [item]
    items_by_id = {"ms:1": item}
    marc_records = [{"_control_number": "9901", "title": "t"}]
    release_wikidata_verify_heap(
        items=items,
        items_by_id=items_by_id,
        marc_records=marc_records,
    )
    assert marc_records == []
    assert len(items[0]["statements"]) == 40
    assert items_by_id["ms:1"] is items[0]


async def test_load_run_marc_records_scoped_filters_quoted_control_numbers() -> None:
    from unittest.mock import AsyncMock, MagicMock

    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    db = MagicMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=MagicMock(all=lambda: [
        ('"990000000000000099"', {"title": "כותרת"}),
        ("990000000000000002", {"title": "other"}),
    ]))
    rows = await load_run_marc_records_scoped(
        db,
        uuid.uuid4(),
        {"990000000000000099"},
    )
    assert len(rows) == 1
    assert rows[0]["_control_number"] == "990000000000000099"
