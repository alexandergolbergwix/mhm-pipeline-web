"""Test-wiki job boundary tags plain live item values in the explicit wikidata.org form."""

import pytest

from app.pipeline.wikidata_upload_job import _tag_explicit_live_items
from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.test_wiki_compat import (
    WikiTestAdaptStats,
    pid_map_key,
    reference_write_issues_for_test_wiki,
)
from converter.wikidata.uploader import WikidataUploader

EXPLICIT_Q87167 = "https://www.wikidata.org/entity/Q87167"


def manuscript_with_p31_and_qualifier(value_type: str = "item") -> WikidataItem:
    return WikidataItem(
        local_id="ms:1",
        entity_type="manuscript",
        statements=[WikidataStatement(
            "P31", "Q87167", value_type,
            qualifiers=[{"property": "P3831", "value": "Q87167", "type": "item"}],
        )],
    )


@pytest.mark.parametrize("value_type", ["item", "wikibase-item"])
def test_test_mode_tags_plain_statement_and_qualifier_values(value_type: str) -> None:
    item = manuscript_with_p31_and_qualifier(value_type)
    tagged = _tag_explicit_live_items(item, is_test=True)
    assert tagged.statements[0].value == EXPLICIT_Q87167
    assert tagged.statements[0].qualifiers == [
        {"property": "P3831", "value": EXPLICIT_Q87167, "type": "item"},
    ]
    assert item.statements[0].value == "Q87167"
    assert item.statements[0].qualifiers[0]["value"] == "Q87167"


@pytest.mark.parametrize("qualifier", [
    {"property": "P3831", "value": "Q87167", "type": "item", "value_namespace": "local"},
    {"property": "P3831", "value": "Q87167", "type": "string"},
    {"property": "P3831", "value": "__LOCAL:work:source", "type": "item"},
])
def test_test_mode_leaves_non_live_qualifier_values_unchanged(qualifier: dict[str, object]) -> None:
    item = WikidataItem(local_id="ms:1", statements=[
        WikidataStatement("P31", "Q87167", "item", qualifiers=[qualifier]),
    ])
    tagged = _tag_explicit_live_items(item, is_test=True)
    assert tagged.statements[0].qualifiers == [qualifier]


def test_live_mode_keeps_plain_live_values() -> None:
    item = manuscript_with_p31_and_qualifier()
    assert _tag_explicit_live_items(item, is_test=False) is item
    assert item.statements[0].value == "Q87167"
    assert item.statements[0].qualifiers[0]["value"] == "Q87167"


def test_bare_reference_qid_is_not_tagged_and_stays_refused() -> None:
    reference = {"property": "P248", "value": "Q87167", "type": "item"}
    item = WikidataItem(local_id="ms:1", statements=[
        WikidataStatement("P31", "Q87167", "item", references=[reference]),
    ])
    tagged = _tag_explicit_live_items(item, is_test=True)
    assert tagged.statements[0].references == [reference]
    assert reference_write_issues_for_test_wiki(tagged) == [
        "P248 reference item has an ambiguous wiki namespace",
    ]


@pytest.fixture
def mapped_uploader(monkeypatch: pytest.MonkeyPatch) -> WikidataUploader:
    uploader = WikidataUploader(token="Fixture@Bot:not-a-credential", is_test=True)
    uploader._test_pid_map = {
        pid_map_key("P31", "item"): "P31",
        pid_map_key("P3831", "item"): "P3831",
    }
    uploader._test_property_datatypes = {"P31": "wikibase-item", "P3831": "wikibase-item"}
    monkeypatch.setattr(
        uploader, "_ensure_test_maps_for_item", lambda _item, **_kwargs: WikiTestAdaptStats(),
    )
    monkeypatch.setattr(uploader, "_ensure_test_entity_exists_flags", lambda _qids: None)
    return uploader


def test_tagged_values_pass_write_gate_and_map_to_test_qid(
    mapped_uploader: WikidataUploader,
) -> None:
    mapped_uploader._test_qid_map = {"Q87167": "Q9999"}
    mapped_uploader._test_entity_exists = {"Q9999": True}
    plain = manuscript_with_p31_and_qualifier()
    assert mapped_uploader._test_local_target_issues(plain) == [
        "P31 item has an ambiguous wiki namespace",
        "P31 qualifier has an ambiguous wiki namespace",
    ]
    tagged = _tag_explicit_live_items(plain, is_test=True)
    assert mapped_uploader._test_local_target_issues(tagged) == []
    adapted, outcome = mapped_uploader._adapt_item_for_test_wiki(tagged)
    assert outcome.skipped == []
    assert adapted.statements[0].value == "Q9999"
    assert adapted.statements[0].qualifiers[0]["value"] == "Q9999"
    assert mapped_uploader._test_local_target_issues(adapted) == []


def test_tagged_value_without_test_mapping_is_still_refused(
    mapped_uploader: WikidataUploader,
) -> None:
    mapped_uploader._test_qid_map = {}
    tagged = _tag_explicit_live_items(manuscript_with_p31_and_qualifier(), is_test=True)
    _, outcome = mapped_uploader._adapt_item_for_test_wiki(tagged)
    assert "Q87167 has no verified test item mapping" in outcome.skipped
