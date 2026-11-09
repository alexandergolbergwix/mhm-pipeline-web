"""The test-map prewarm gate must accept plain live QIDs that are not mapped yet."""

import logging
from types import SimpleNamespace

import pytest

from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.test_wiki_compat import WikiTestAdaptStats, pid_map_key
from converter.wikidata.uploader import WikidataUploader


@pytest.fixture
def uploader(monkeypatch: pytest.MonkeyPatch) -> WikidataUploader:
    up = WikidataUploader(token="Fixture@Bot:not-a-credential", is_test=True)
    monkeypatch.setattr(up, "_init_wbi", lambda: SimpleNamespace())
    monkeypatch.setattr(up, "_login", SimpleNamespace())
    monkeypatch.setattr(up, "_fetch_live_english_labels", lambda _qids: {})
    return up


def manuscript_with_instance_of(value: object) -> WikidataItem:
    return WikidataItem(
        local_id="ms:1",
        entity_type="manuscript",
        statements=[WikidataStatement("P31", value, "item")],
    )


def record_ensure_calls(uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    calls: list[tuple[str, bool]] = []

    def _ensure(item: WikidataItem, *, allow_unmapped_live: bool = False) -> object:
        calls.append((item.local_id, allow_unmapped_live))
        return SimpleNamespace()

    monkeypatch.setattr(uploader, "_ensure_test_maps_for_item", _ensure)
    return calls


def test_prewarm_does_not_skip_plain_live_qid_before_mapping(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    calls = record_ensure_calls(uploader, monkeypatch)
    assert uploader._test_qid_map == {}
    with caplog.at_level(logging.WARNING):
        uploader.warm_test_maps_for_items([manuscript_with_instance_of("Q213")])
    assert calls == [("ms:1", True)]
    assert "Skipping test-map prewarm" not in caplog.text


@pytest.mark.parametrize("value", [
    {"id": "Q213", "value_namespace": "mystery"},
    "https://example.org/entity/Q213",
])
def test_prewarm_still_skips_unknown_namespace(
    uploader: WikidataUploader,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    value: object,
) -> None:
    calls = record_ensure_calls(uploader, monkeypatch)
    with caplog.at_level(logging.WARNING):
        uploader.warm_test_maps_for_items([manuscript_with_instance_of(value)])
    assert calls == []
    assert "Skipping test-map prewarm for ms:1: P31 item has an ambiguous wiki namespace" in caplog.text


def test_write_time_validation_still_rejects_plain_live_qid(uploader: WikidataUploader) -> None:
    item = manuscript_with_instance_of("Q213")
    assert uploader._test_local_target_issues(item) == ["P31 item has an ambiguous wiki namespace"]
    assert uploader._test_local_target_issues(item, allow_unmapped_live=True) == []


def stub_adapt_maps(uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uploader, "_ensure_test_maps_for_item", lambda _item: WikiTestAdaptStats())
    monkeypatch.setattr(uploader, "_ensure_test_entity_exists_flags", lambda _qids: None)


def test_adapt_accepts_prewarmed_plain_live_qid(uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch) -> None:
    uploader._test_qid_map = {"Q213": "Q9999"}
    uploader._test_pid_map = {pid_map_key("P31", "item"): "P31"}
    uploader._test_property_datatypes = {"P31": "wikibase-item"}
    uploader._test_entity_exists = {"Q9999": True}
    stub_adapt_maps(uploader, monkeypatch)
    adapted, outcome = uploader._adapt_item_for_test_wiki(manuscript_with_instance_of("Q213"))
    assert outcome.skipped == []
    assert adapted.statements[0].value == "Q9999"
    assert uploader._test_local_target_issues(adapted) == []


def test_adapt_rejects_unmapped_plain_live_qid(uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch) -> None:
    uploader._test_pid_map = {pid_map_key("P31", "item"): "P31"}
    uploader._test_property_datatypes = {"P31": "wikibase-item"}
    stub_adapt_maps(uploader, monkeypatch)
    _, outcome = uploader._adapt_item_for_test_wiki(manuscript_with_instance_of("Q213"))
    assert outcome.skipped == ["P31 item has an ambiguous wiki namespace"]


def manuscript_with_qualifier(qualifier_value: str) -> WikidataItem:
    return WikidataItem(
        local_id="ms:1",
        entity_type="manuscript",
        statements=[WikidataStatement(
            "P31", "Q213", "item",
            qualifiers=[{"property": "P217", "value": qualifier_value, "type": "item"}],
        )],
    )


def test_adapt_binds_prewarmed_qualifier_qid_and_passes_write_check(
    uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploader._test_qid_map = {"Q213": "Q9999", "Q214": "Q9998"}
    uploader._test_pid_map = {pid_map_key("P31", "item"): "P31", pid_map_key("P217", "item"): "P217"}
    uploader._test_property_datatypes = {"P31": "wikibase-item", "P217": "wikibase-item"}
    uploader._test_entity_exists = {"Q9999": True, "Q9998": True}
    stub_adapt_maps(uploader, monkeypatch)
    adapted, outcome = uploader._adapt_item_for_test_wiki(manuscript_with_qualifier("Q214"))
    assert outcome.skipped == []
    assert adapted.statements[0].qualifiers[0]["value"] == "Q9998"
    assert uploader._test_local_target_issues(adapted) == []


def test_adapt_rejects_unmapped_plain_qualifier_qid(uploader: WikidataUploader, monkeypatch: pytest.MonkeyPatch) -> None:
    uploader._test_qid_map = {"Q213": "Q9999"}
    uploader._test_pid_map = {pid_map_key("P31", "item"): "P31", pid_map_key("P217", "item"): "P217"}
    uploader._test_property_datatypes = {"P31": "wikibase-item", "P217": "wikibase-item"}
    uploader._test_entity_exists = {"Q9999": True}
    stub_adapt_maps(uploader, monkeypatch)
    item = manuscript_with_qualifier("Q214")
    _, outcome = uploader._adapt_item_for_test_wiki(item)
    assert outcome.skipped == ["P31 qualifier has an ambiguous wiki namespace"]
    assert "P31 qualifier has an ambiguous wiki namespace" in uploader._test_local_target_issues(item)
