"""The connection pass retains every source fact it can resolve safely."""

from copy import deepcopy

import pytest

from app.pipeline.wikidata_deferred_payload import build_deferred_link_payload
from converter.wikidata.item_models import WikidataItem, WikidataStatement


def test_complete_payload_keeps_known_links_and_excludes_unresolved_links() -> None:
    source = WikidataItem(statements=[
        WikidataStatement("P31", "Q87167", "item"),
        WikidataStatement("P1574", "__LOCAL:old", "item"),
        WikidataStatement("P1574", "__LOCAL:new", "item"),
        WikidataStatement("P1574", "__LOCAL:unknown", "item"),
        WikidataStatement("P528", "catalogue-code", "string"),
    ])
    before = deepcopy(source)
    payload = build_deferred_link_payload(
        source, source_qid="Q10", resolved=[WikidataStatement("P1574", "Q30", "item")],
        known_qids={"old": "Q20", "new": "Q30"}, is_test=False,
    )
    assert source == before
    assert [(s.property_id, s.value) for s in payload.statements] == [
        ("P31", "Q87167"), ("P1574", "Q20"), ("P1574", "Q30"),
        ("P528", "catalogue-code"),
    ]


def test_author_fallback_does_not_replace_other_source_facts() -> None:
    source = WikidataItem(statements=[
        WikidataStatement("P31", "Q47461344", "item"),
        WikidataStatement("P50", "__LOCAL:anonymous", "item"),
        WikidataStatement("P1476", "ספר", "monolingualtext"),
    ])
    payload = build_deferred_link_payload(
        source, source_qid="Q10", resolved=[WikidataStatement("P2093", "מחבר", "string")],
        known_qids={}, is_test=False,
    )
    assert [(s.property_id, s.value) for s in payload.statements] == [
        ("P31", "Q47461344"), ("P1476", "ספר"), ("P2093", "מחבר"),
    ]


def test_a_partial_source_cannot_trigger_property_replacement() -> None:
    with pytest.raises(ValueError, match="complete native item"):
        build_deferred_link_payload(
            WikidataItem(statements=[WikidataStatement("P1574", "Q30", "item")]),
            source_qid="Q10", resolved=[], known_qids={}, is_test=False,
        )


def test_an_unresolved_connection_cannot_enter_the_final_payload() -> None:
    with pytest.raises(ValueError, match="unresolved local target"):
        build_deferred_link_payload(
            WikidataItem(statements=[WikidataStatement("P31", "Q87167", "item")]),
            source_qid="Q10", resolved=[WikidataStatement("P1574", "__LOCAL:x", "item")],
            known_qids={}, is_test=False,
        )
