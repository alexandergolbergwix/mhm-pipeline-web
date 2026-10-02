"""Rebuild-selected router helpers — merge semantics (Rule W-191 parity)."""

from __future__ import annotations

from app.routers.wikidata_studio import _merge_rebuilt_items


def test_merge_replaces_selected_and_shared_new_ids() -> None:
    cached = [
        {"local_id": "work:a", "label": "old a"},
        {"local_id": "person:shared", "label": "old shared"},
        {"local_id": "work:b", "label": "b"},
    ]
    merged = _merge_rebuilt_items(
        cached,
        selected_old_ids={"work:a"},
        new_items=[
            {"local_id": "work:a", "label": "new a"},
            {"local_id": "person:shared", "label": "new shared"},
            {"local_id": "work:c", "label": "new c"},
        ],
    )
    labels = {it["local_id"]: it["label"] for it in merged}
    assert labels == {
        "work:a": "new a",
        "person:shared": "new shared",
        "work:b": "b",
        "work:c": "new c",
    }
    assert len(merged) == 4
