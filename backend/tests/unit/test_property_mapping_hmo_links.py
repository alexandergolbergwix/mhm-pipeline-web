"""Tests for the HMO Wikibase cross-linking helper (Phase 6 — see
dev-docs/hmo-wikibase-studio-plan.md).
"""

from __future__ import annotations

from converter.wikidata.property_mapping import (
    HMO_WIKIBASE_BASE_URL,
    hmo_wikibase_entity_url,
    hmo_wikibase_page_url,
)


def test_entity_url_none_when_no_mapping_given() -> None:
    assert hmo_wikibase_entity_url("990001", None) is None
    assert hmo_wikibase_entity_url("990001", {}) is None


def test_entity_url_none_when_control_number_not_mapped() -> None:
    assert hmo_wikibase_entity_url("990001", {"990002": "Q5"}) is None


def test_entity_url_builds_real_item_link_when_mapped() -> None:
    url = hmo_wikibase_entity_url("990001", {"990001": "Q42"})
    assert url == f"{HMO_WIKIBASE_BASE_URL}/wiki/Item:Q42"


def test_page_url_slug_fallback_unchanged() -> None:
    assert hmo_wikibase_page_url("990001") == f"{HMO_WIKIBASE_BASE_URL}/wiki/MS_990001"
    assert hmo_wikibase_page_url("") == ""
