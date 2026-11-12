"""Exact historical proposals preserve facts and reject stale evidence."""

from copy import deepcopy

import pytest

from app.pipeline.wikidata_gila_repairs import (
    build_gila_repair_proposals,
    validate_gila_repair_proposal,
)


def _reference(url: str) -> dict[str, object]:
    return {
        "snaks": {
            "P854": [
                {
                    "snaktype": "value",
                    "property": "P854",
                    "datatype": "url",
                    "datavalue": {"type": "string", "value": url},
                }
            ]
        }
    }


def _entity() -> dict[str, object]:
    first = _reference("https://example.org/first")
    return {
        "id": "Q123",
        "lastrevid": 1234,
        "claims": {
            "P571": [
                {
                    "id": "Q123$date",
                    "rank": "preferred",
                    "mainsnak": {
                        "property": "P571",
                        "snaktype": "value",
                        "datatype": "time",
                        "datavalue": {
                            "type": "time",
                            "value": {"time": "+1650-00-00T00:00:00Z", "precision": 9},
                        },
                    },
                    "references": [
                        first,
                        deepcopy(first),
                        _reference("https://example.org/second"),
                    ],
                }
            ]
        },
    }


def test_reference_repair_retains_distinct_sources_and_claim_metadata() -> None:
    entity = _entity()
    proposals = build_gila_repair_proposals({"Q123": entity})
    proposal = proposals[0]
    change = proposal["changes"][0]
    assert proposal["revision"] == 1234
    assert proposal["requires_review"] is True
    assert len(change["before"]["references"]) == 3
    assert len(change["after"]["references"]) == 2
    assert change["after"]["rank"] == "preferred"
    assert entity == _entity()
    validate_gila_repair_proposal(proposal, entity)
    repaired = deepcopy(entity)
    repaired["claims"]["P571"][0] = change["after"]
    repaired["lastrevid"] = 1235
    assert build_gila_repair_proposals({"Q123": repaired}) == []


def test_repair_rejects_new_revision_or_changed_claim() -> None:
    entity = _entity()
    proposal = build_gila_repair_proposals({"Q123": entity})[0]
    with pytest.raises(ValueError, match="revision"):
        validate_gila_repair_proposal(proposal, {**entity, "lastrevid": 1235})
    changed = deepcopy(entity)
    changed["claims"]["P571"][0]["rank"] = "normal"
    with pytest.raises(ValueError, match="digest"):
        validate_gila_repair_proposal(proposal, changed)


def test_repair_rejects_an_uncertain_previous_write_outcome() -> None:
    entity = _entity()
    proposal = build_gila_repair_proposals({"Q123": entity})[0]
    with pytest.raises(ValueError, match="uncertain"):
        validate_gila_repair_proposal(proposal, entity, prior_outcome="uncertain")


def test_clean_entity_produces_no_reference_repair() -> None:
    assert (
        build_gila_repair_proposals({"Q123": {"id": "Q123", "lastrevid": 1234, "claims": {}}}) == []
    )


@pytest.mark.parametrize("wrapper", ["{text}", '"{text}"', '"{text}".', '""{text}"".'])
def test_duplicate_colophon_needs_source_text_and_keeps_all_references(wrapper: str) -> None:
    text = 'השלמתי בשנת ת"י שמואל(?)'
    correct = {
        "id": "Q123$correct",
        "rank": "normal",
        "mainsnak": {
            "property": "P1684",
            "snaktype": "value",
            "datatype": "monolingualtext",
            "datavalue": {"type": "monolingualtext", "value": {"text": text, "language": "he"}},
        },
        "qualifiers": {
            "P3831": [{"snaktype": "value", "datavalue": {"value": {"id": "Q372474"}}}],
            "P7416": [{"snaktype": "value", "datavalue": {"value": "21א"}}],
        },
        "references": [
            _reference("https://www.nli.org.il/en/manuscripts/NNL_ALEPH990000802880205171")
        ],
    }
    old = deepcopy(correct)
    old["id"] = "Q123$old"
    old.pop("qualifiers")
    legacy_text = wrapper.format(text=text.replace('"', '""'))
    old["mainsnak"]["datavalue"]["value"]["text"] = legacy_text
    entity = {"id": "Q123", "lastrevid": 1234, "claims": {"P1684": [correct, old]}}
    assert build_gila_repair_proposals({"Q123": entity}) == []
    proposal = build_gila_repair_proposals({"Q123": entity}, colophons_by_qid={"Q123": text})[0]
    assert any(change["kind"] == "remove_duplicate_colophon" for change in proposal["changes"])
    validate_gila_repair_proposal(proposal, entity)
    old["mainsnak"]["datavalue"]["value"]["text"] = legacy_text.replace("שמואל(?)", "שמואל")
    assert build_gila_repair_proposals({"Q123": entity}, colophons_by_qid={"Q123": text}) == []
    old["mainsnak"]["datavalue"]["value"]["text"] = legacy_text
    correct["rank"] = "deprecated"
    assert build_gila_repair_proposals({"Q123": entity}, colophons_by_qid={"Q123": text}) == []
    correct["rank"] = "normal"
    old["rank"] = "preferred"
    assert build_gila_repair_proposals({"Q123": entity}, colophons_by_qid={"Q123": text}) == []
    old["rank"] = "normal"
    old["mainsnak"]["datavalue"]["value"]["language"] = "en"
    assert build_gila_repair_proposals({"Q123": entity}, colophons_by_qid={"Q123": text}) == []
    old["mainsnak"]["datavalue"]["value"]["language"] = "he"
    correct["qualifiers"]["P3831"][0]["snaktype"] = "novalue"
    assert build_gila_repair_proposals({"Q123": entity}, colophons_by_qid={"Q123": text}) == []
    correct["qualifiers"]["P3831"][0]["snaktype"] = "value"
    correct["mainsnak"]["snaktype"] = "novalue"
    assert build_gila_repair_proposals({"Q123": entity}, colophons_by_qid={"Q123": text}) == []
    correct["mainsnak"]["snaktype"] = "value"
    correct["qualifiers"]["P7416"] = [{}]
    assert build_gila_repair_proposals({"Q123": entity}, colophons_by_qid={"Q123": text}) == []
