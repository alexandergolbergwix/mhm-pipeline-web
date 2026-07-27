"""Unit tests for HMO Wikibase bridge URL helpers (Rule W-122)."""

from __future__ import annotations

from converter.wikidata.property_mapping import (
    HMO_WIKIBASE_BASE_URL,
    hmo_wikibase_entity_url,
    hmo_wikibase_item_url,
    hmo_wikibase_page_url,
    is_browseable_hmo_wikibase_url,
    is_hmo_identity_placeholder_url,
    resolve_hmo_bridge_url,
)


def test_entity_url_none_when_missing() -> None:
    assert hmo_wikibase_entity_url("990001", None) is None
    assert hmo_wikibase_entity_url("990001", {}) is None


def test_entity_url_none_when_cn_missing_from_map() -> None:
    assert hmo_wikibase_entity_url("990001", {"990002": "Q5"}) is None


def test_entity_url_builds_item_page() -> None:
    url = hmo_wikibase_entity_url("990001", {"990001": "Q42"})
    assert url == f"{HMO_WIKIBASE_BASE_URL}/wiki/Item:Q42"


def test_entity_url_rejects_non_qid() -> None:
    assert hmo_wikibase_entity_url("990001", {"990001": "MS_1"}) is None
    assert hmo_wikibase_entity_url("990001", {"990001": "Q0"}) is None


def test_page_url_is_empty_fail_closed() -> None:
    assert hmo_wikibase_page_url("990001") == ""
    assert hmo_wikibase_page_url("") == ""


def test_browseable_and_placeholder_classification() -> None:
    item = f"{HMO_WIKIBASE_BASE_URL}/wiki/Item:Q894"
    ontology = "https://w3id.org/mhm/ontology#MS_990001"
    slug = f"{HMO_WIKIBASE_BASE_URL}/wiki/MS_990001"
    assert is_browseable_hmo_wikibase_url(item) is True
    assert is_browseable_hmo_wikibase_url(ontology) is False
    assert is_browseable_hmo_wikibase_url(slug) is False
    assert is_hmo_identity_placeholder_url(ontology) is True
    assert is_hmo_identity_placeholder_url(slug) is True
    assert is_hmo_identity_placeholder_url(item) is False


def test_resolve_prefers_direct_qid() -> None:
    assert resolve_hmo_bridge_url("990001", wikibase_qid="Q12") == (
        f"{HMO_WIKIBASE_BASE_URL}/wiki/Item:Q12"
    )
    assert hmo_wikibase_item_url("Q12") == f"{HMO_WIKIBASE_BASE_URL}/wiki/Item:Q12"
    assert resolve_hmo_bridge_url("990001") == ""
