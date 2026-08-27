"""Tests for Wikidata export quality gate."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.pipeline.wikidata_export_quality_gate import assert_wikidata_export_quality


@dataclass
class _Stmt:
    property_id: str
    value: str = ""


@dataclass
class _Item:
    entity_type: str = "manuscript"
    labels: dict = field(default_factory=dict)
    statements: list = field(default_factory=list)
    local_id: str = "ms-1"


def test_missing_label_raises() -> None:
    with pytest.raises(ValueError, match="MISSING_LABEL"):
        assert_wikidata_export_quality([_Item(labels={})])


def test_known_bad_p31_raises() -> None:
    item = _Item(
        labels={"en": "Palimpsest MS"},
        statements=[
            _Stmt("P31", "Q179808"),
            _Stmt("P3959", "990000403370205171"),
        ],
    )
    with pytest.raises(ValueError, match="P31_WRONG_QID"):
        assert_wikidata_export_quality([item])


def test_missing_p3959_raises() -> None:
    item = _Item(
        labels={"en": "Hebrew manuscript, NLI, MS 1"},
        statements=[_Stmt("P31", "Q87167")],
    )
    with pytest.raises(ValueError, match="MISSING_P3959"):
        assert_wikidata_export_quality([item])


def test_clean_item_passes() -> None:
    assert_wikidata_export_quality([
        _Item(
            labels={"en": "Hebrew manuscript, NLI, MS 1"},
            statements=[
                _Stmt("P31", "Q87167"),
                _Stmt("P3959", "990000403370205171"),
            ],
        ),
    ]) is None


def test_source_backed_work_without_author_claim_raises() -> None:
    item = _Item(
        entity_type="work",
        labels={"he": "מנחת יהודה"},
        statements=[_Stmt("P1476", "מנחת יהודה")],
        local_id="work:מנחת_יהודה",
    )
    item.records = ["1"]
    with pytest.raises(ValueError, match="WORK_MISSING_AUTHOR_CLAIM"):
        assert_wikidata_export_quality(
            [item],
            marc_records=[{
                "_control_number": "1",
                "title": "מנחת יהודה",
                "authors": [{"name": "מחבר"}],
            }],
        )
