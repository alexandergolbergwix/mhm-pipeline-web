"""Review-table merge over a cache that already holds build-time overrides."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from app.pipeline.wikidata_item_views import (
    _content_override_newer_than_build,
    _merge_one_wikidata_item,
)

BUILT_AT = datetime(2026, 11, 1, tzinfo=UTC)
ADDED_STATEMENT = {"property_id": "P2", "value": "extra"}
RAW_ITEM: dict[str, Any] = {
    "local_id": "QDraft_MS_1",
    "entity_type": "manuscript",
    "labels": {"en": "Built"},
    "statements": [{"property_id": "P1", "value": "built"}],
}


def _override(**fields: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "labels": {}, "descriptions": {}, "aliases": {},
        "add_statements": [], "remove_statements": [], "statement_edits": {},
        "approved": None, "accept_foreign_modify": False, "accepted_foreign_qid": None,
        "ai_verdict": None, "ai_verdict_at": None,
        "updated_at": BUILT_AT - timedelta(hours=1),
    }
    values.update(fields)
    return SimpleNamespace(**values)


def _merge(
    raw: dict[str, Any], ov_row: SimpleNamespace, *,
    overrides_applied: bool, rebuild_required: bool = False,
) -> dict[str, Any]:
    return _merge_one_wikidata_item(
        raw, ov_row=ov_row, ledger={}, latest_writes={}, overrides_applied=overrides_applied,
        rebuild_required=rebuild_required,
    )


def test_cached_item_shows_current_curator_label() -> None:
    row = _merge(
        RAW_ITEM, _override(labels={"en": "Curator"}), overrides_applied=True,
    )

    assert row["labels"] == {"en": "Curator"}


def test_cached_item_does_not_apply_statement_edits_twice() -> None:
    built_with_add = {**RAW_ITEM, "statements": [*RAW_ITEM["statements"], ADDED_STATEMENT]}

    row = _merge(
        built_with_add, _override(add_statements=[ADDED_STATEMENT]), overrides_applied=True,
    )

    assert row["statements"] == built_with_add["statements"]


def test_uncached_item_applies_statement_edits_once() -> None:
    row = _merge(
        RAW_ITEM, _override(add_statements=[ADDED_STATEMENT]), overrides_applied=False,
    )

    assert row["statements"] == [*RAW_ITEM["statements"], ADDED_STATEMENT]


def test_cached_item_with_newer_content_override_is_flagged_for_rebuild() -> None:
    built_with_add = {**RAW_ITEM, "statements": [*RAW_ITEM["statements"], ADDED_STATEMENT]}
    ov_row = _override(
        add_statements=[ADDED_STATEMENT], updated_at=BUILT_AT + timedelta(hours=1),
    )

    row = _merge(
        built_with_add, ov_row, overrides_applied=True,
        rebuild_required=_content_override_newer_than_build(ov_row, BUILT_AT),
    )

    assert row["rebuild_required"] is True
    assert row["statements"] == built_with_add["statements"]


def test_content_override_older_than_build_does_not_require_rebuild() -> None:
    ov_row = _override(
        add_statements=[ADDED_STATEMENT], updated_at=BUILT_AT - timedelta(hours=1),
    )

    assert _content_override_newer_than_build(ov_row, BUILT_AT) is False


def test_approval_only_newer_override_does_not_require_rebuild() -> None:
    ov_row = _override(approved=False, updated_at=BUILT_AT + timedelta(hours=1))

    assert _content_override_newer_than_build(ov_row, BUILT_AT) is False


def test_uncached_item_is_not_flagged_for_rebuild() -> None:
    row = _merge(RAW_ITEM, _override(add_statements=[ADDED_STATEMENT]), overrides_applied=False)

    assert row["rebuild_required"] is False
