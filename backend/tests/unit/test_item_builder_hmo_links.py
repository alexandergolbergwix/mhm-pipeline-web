"""Tests for WikidataItemBuilder's P2888/P973 HMO cross-link (Phase 6
— see dev-docs/hmo-wikibase-studio-plan.md).

Covers both branches: no link before upload (fail closed — Rule W-122)
and the real HMO Wikibase item link once a manuscript has an instance
mapping.
"""

from __future__ import annotations

from converter.wikidata.item_builder import WikidataItemBuilder
from converter.wikidata.property_mapping import HMO_WIKIBASE_BASE_URL

_CN = "990000000000000001"


def _record() -> dict[str, object]:
    return {
        "_control_number": _CN,
        "title": "Test Manuscript",
        "authors": [],
        "contributors": [],
        "subjects": [],
        "dates": {"year": 1500},
        "language": "heb",
    }


def _p2888_and_p973(item) -> dict[str, str]:
    return {s.property_id: s.value for s in item.statements if s.property_id in ("P2888", "P973")}


def test_omits_bridge_when_manuscript_not_yet_uploaded() -> None:
    builder = WikidataItemBuilder(reconciler=None, hmo_instance_qids={})
    item = builder.build_manuscript_item(_record())

    values = _p2888_and_p973(item)
    assert "P2888" not in values
    assert "P973" not in values


def test_uses_real_item_url_once_manuscript_is_uploaded() -> None:
    builder = WikidataItemBuilder(reconciler=None, hmo_instance_qids={_CN: "Q999"})
    item = builder.build_manuscript_item(_record())

    values = _p2888_and_p973(item)
    assert values["P2888"] == f"{HMO_WIKIBASE_BASE_URL}/wiki/Item:Q999"
    assert values["P973"] == f"{HMO_WIKIBASE_BASE_URL}/wiki/Item:Q999"


def test_default_hmo_instance_qids_is_empty_and_safe() -> None:
    builder = WikidataItemBuilder(reconciler=None)
    item = builder.build_manuscript_item(_record())

    values = _p2888_and_p973(item)
    assert values == {}
