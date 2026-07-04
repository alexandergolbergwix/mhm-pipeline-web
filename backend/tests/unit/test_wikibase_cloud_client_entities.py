"""Tests for WikibaseCloudWriter's entity-write API (Phase 1 of HMO
Wikibase Studio — see dev-docs/hmo-wikibase-studio-plan.md).

Item/property/claim writes go through ``wikibaseintegrator`` (the same
library ``converter/wikidata/uploader.py`` already uses for
wikidata.org), not hand-rolled MediaWiki API calls. These tests build
real (offline) WikibaseIntegrator entity objects and monkeypatch only
the network-touching ``.write()``/``.get()`` calls, so label/description/
alias/claim wiring is exercised for real.
"""

from __future__ import annotations

from typing import Any

import pytest
from wikibaseintegrator import WikibaseIntegrator, datatypes
from wikibaseintegrator.entities import ItemEntity, PropertyEntity

from converter.wikibase.cloud_client import (
    EntityEditOutcome,
    WikibaseBotCredentials,
    WikibaseCloudWriter,
    WikibaseEndpointConfig,
    format_wbi_exception,
)


def _writer() -> WikibaseCloudWriter:
    creds = WikibaseBotCredentials(username="bot", bot_name="hmo", password="secret")
    return WikibaseCloudWriter(
        WikibaseEndpointConfig(base_url="https://mhm-hmo.wikibase.cloud"),
        creds,
    )


def _stub_wbi(writer: WikibaseCloudWriter, monkeypatch: pytest.MonkeyPatch) -> WikibaseIntegrator:
    """Point the writer at a real, offline WikibaseIntegrator instance.

    Building items/properties and setting labels/claims works fully
    offline; only ``.write()`` touches the network, so that's what
    individual tests stub out.
    """
    fake_wbi = WikibaseIntegrator()
    monkeypatch.setattr(writer, "_init_wbi", lambda: fake_wbi)
    return fake_wbi


def test_create_item_sets_labels_descriptions_and_returns_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = _writer()
    _stub_wbi(writer, monkeypatch)

    captured: dict[str, Any] = {}

    def fake_write(self: ItemEntity, **kwargs: Any) -> ItemEntity:
        captured["labels"] = {"en": self.labels.get("en").value}
        captured["descriptions"] = {"en": self.descriptions.get("en").value}
        captured["bot"] = kwargs.get("bot")
        self.id = "Q42"
        return self

    monkeypatch.setattr(ItemEntity, "write", fake_write)

    outcome = writer.create_item(
        labels={"en": "Test Manuscript"},
        descriptions={"en": "a manuscript"},
    )

    assert isinstance(outcome, EntityEditOutcome)
    assert outcome.status == "created"
    assert outcome.entity_id == "Q42"
    assert outcome.page_url == "https://mhm-hmo.wikibase.cloud/wiki/Item:Q42"
    assert captured["labels"] == {"en": "Test Manuscript"}
    assert captured["descriptions"] == {"en": "a manuscript"}
    assert captured["bot"] is True


def test_create_property_sets_datatype(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _writer()
    _stub_wbi(writer, monkeypatch)

    def fake_write(self: PropertyEntity, **kwargs: Any) -> PropertyEntity:
        self.id = "P7"
        return self

    monkeypatch.setattr(PropertyEntity, "write", fake_write)

    outcome = writer.create_property(
        labels={"en": "has folio count"},
        descriptions={"en": "number of folios"},
        datatype="quantity",
    )

    assert outcome.entity_id == "P7"
    assert outcome.page_url == "https://mhm-hmo.wikibase.cloud/wiki/Property:P7"


def test_create_item_attaches_claims_before_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _writer()
    _stub_wbi(writer, monkeypatch)

    captured: dict[str, Any] = {}

    def fake_write(self: ItemEntity, **kwargs: Any) -> ItemEntity:
        captured["claim_count"] = len(self.claims)
        self.id = "Q99"
        return self

    monkeypatch.setattr(ItemEntity, "write", fake_write)

    claim = datatypes.Item(prop_nr="P31", value="Q5")
    outcome = writer.create_item(
        labels={"en": "X"}, descriptions={"en": "y"}, claims=[claim]
    )

    assert outcome.status == "created"
    assert captured["claim_count"] == 1


def test_create_item_reports_failure_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _writer()
    _stub_wbi(writer, monkeypatch)

    def fake_write(self: ItemEntity, **kwargs: Any) -> ItemEntity:
        raise RuntimeError("permissiondenied: nope")

    monkeypatch.setattr(ItemEntity, "write", fake_write)

    outcome = writer.create_item(labels={"en": "X"}, descriptions={"en": "y"})

    assert outcome.status == "failed"
    assert outcome.entity_id is None
    assert "nope" in outcome.message


def test_add_claim_loads_existing_item_and_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _writer()
    fake_wbi = _stub_wbi(writer, monkeypatch)

    existing = fake_wbi.item.new()
    existing.id = "Q1"
    monkeypatch.setattr(fake_wbi.item, "get", lambda entity_id: existing)

    def fake_write(self: ItemEntity, **kwargs: Any) -> ItemEntity:
        return self

    monkeypatch.setattr(ItemEntity, "write", fake_write)

    claim = datatypes.Item(prop_nr="P31", value="Q2")
    outcome = writer.add_claim("Q1", claim)

    assert outcome.status == "updated"
    assert outcome.entity_id == "Q1"
    assert len(existing.claims) == 1


def test_add_claim_dispatches_to_property_get_for_p_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _writer()
    fake_wbi = _stub_wbi(writer, monkeypatch)

    existing = fake_wbi.property.new(datatype="string")
    existing.id = "P1"
    get_calls: list[str] = []
    monkeypatch.setattr(
        fake_wbi.property, "get", lambda entity_id: (get_calls.append(entity_id), existing)[1]
    )

    def fake_write(self: PropertyEntity, **kwargs: Any) -> PropertyEntity:
        return self

    monkeypatch.setattr(PropertyEntity, "write", fake_write)

    claim = datatypes.String(prop_nr="P2", value="hello")
    outcome = writer.add_claim("P1", claim)

    assert outcome.status == "updated"
    assert get_calls == ["P1"]


def test_add_claim_reports_failure_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _writer()
    fake_wbi = _stub_wbi(writer, monkeypatch)

    def raise_get(entity_id: str) -> ItemEntity:
        raise RuntimeError("no-such-entity: missing")

    monkeypatch.setattr(fake_wbi.item, "get", raise_get)

    claim = datatypes.Item(prop_nr="P31", value="Q2")
    outcome = writer.add_claim("Q404", claim)

    assert outcome.status == "failed"
    assert "missing" in outcome.message


def test_update_item_refreshes_labels_and_merges_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _writer()
    fake_wbi = _stub_wbi(writer, monkeypatch)

    existing = fake_wbi.item.new()
    existing.id = "Q1"
    existing.labels.set("en", "Old label")
    # A curator-added statement not present in the new build — must
    # survive the merge, never be wiped by an update_item() call.
    hand_added_claim = datatypes.String(prop_nr="P99", value="hand-added")
    existing.claims.add(hand_added_claim)
    monkeypatch.setattr(fake_wbi.item, "get", lambda entity_id: existing)

    def fake_write(self: ItemEntity, **kwargs: Any) -> ItemEntity:
        return self

    monkeypatch.setattr(ItemEntity, "write", fake_write)

    new_claim = datatypes.Item(prop_nr="P31", value="Q5")
    outcome = writer.update_item(
        "Q1",
        labels={"en": "New label"},
        descriptions={"en": "a manuscript"},
        claims=[new_claim],
    )

    assert outcome.status == "updated"
    assert outcome.entity_id == "Q1"
    assert existing.labels.get("en").value == "New label"
    assert existing.descriptions.get("en").value == "a manuscript"
    assert len(existing.claims) == 2
    assert existing.claims.get("P99")[0].mainsnak.datavalue["value"] == "hand-added"


def test_update_item_reports_failure_when_fetch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _writer()
    fake_wbi = _stub_wbi(writer, monkeypatch)

    def raise_get(entity_id: str) -> ItemEntity:
        raise RuntimeError("no-such-entity: missing")

    monkeypatch.setattr(fake_wbi.item, "get", raise_get)

    outcome = writer.update_item("Q404", labels={"en": "X"}, descriptions={"en": "y"})

    assert outcome.status == "failed"
    assert outcome.entity_id == "Q404"
    assert "missing" in outcome.message


def test_update_item_reports_failure_when_write_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _writer()
    fake_wbi = _stub_wbi(writer, monkeypatch)

    existing = fake_wbi.item.new()
    existing.id = "Q1"
    monkeypatch.setattr(fake_wbi.item, "get", lambda entity_id: existing)

    def fake_write(self: ItemEntity, **kwargs: Any) -> ItemEntity:
        raise RuntimeError("permissiondenied: nope")

    monkeypatch.setattr(ItemEntity, "write", fake_write)

    outcome = writer.update_item("Q1", labels={"en": "X"}, descriptions={"en": "y"})

    assert outcome.status == "failed"
    assert outcome.entity_id == "Q1"
    assert "nope" in outcome.message


def test_format_wbi_exception_includes_code_and_conflicts() -> None:
    from wikibaseintegrator.wbi_exceptions import MWApiError

    exc = MWApiError({
        "code": "modification-failed",
        "info": "Label in language en already in use.",
        "messages": [{"name": "wikibase-validator-label-conflict", "parameters": ["en", "P12"]}],
    })
    msg = format_wbi_exception(exc)
    assert "modification-failed" in msg
    assert "Label in language en already in use" in msg


def test_get_entity_returns_none_when_lookup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _writer()
    fake_wbi = _stub_wbi(writer, monkeypatch)

    def raise_get(entity_id: str) -> ItemEntity:
        raise RuntimeError("missing")

    monkeypatch.setattr(fake_wbi.item, "get", raise_get)

    assert writer.get_entity("Q404") is None


def test_get_entity_returns_json_on_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _writer()
    fake_wbi = _stub_wbi(writer, monkeypatch)

    existing = fake_wbi.item.new()
    existing.id = "Q42"
    monkeypatch.setattr(fake_wbi.item, "get", lambda entity_id: existing)

    entity = writer.get_entity("Q42")

    assert entity is not None
    assert entity["id"] == "Q42"
