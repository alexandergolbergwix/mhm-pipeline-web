"""Repeated retrieval dates do not establish independent source citations."""

from copy import deepcopy

import pytest

from app.pipeline.wikidata_gila_repairs import (
    build_gila_repair_proposals,
    validate_gila_repair_proposal,
)
from converter.wikidata.reference_retrieval import current_reference_indices, reference_source_key


def _reference(day: str, url: str = "https://www.nli.org.il/record/990001580110205171") -> dict:
    return {
        "snaks": {
            "P854": [
                {
                    "snaktype": "value",
                    "property": "P854",
                    "datatype": "url",
                    "datavalue": {"value": url, "type": "string"},
                }
            ],
            "P813": [
                {
                    "snaktype": "value",
                    "property": "P813",
                    "datatype": "time",
                    "datavalue": {
                        "type": "time",
                        "value": {
                            "time": f"+{day}T00:00:00Z",
                            "precision": 11,
                            "timezone": 0,
                            "before": 0,
                            "after": 0,
                            "calendarmodel": "http://www.wikidata.org/entity/Q1985727",
                        },
                    },
                }
            ],
        }
    }


def test_the_four_dates_in_the_screenshot_keep_the_latest_recorded_retrieval() -> None:
    references = [
        _reference(day) for day in ["2026-08-11", "2026-11-05", "2026-11-10", "2026-11-11"]
    ]
    before = deepcopy(references)
    assert current_reference_indices(references) == [3]
    assert references == before


def test_an_older_incoming_retrieval_cannot_replace_the_current_date() -> None:
    assert current_reference_indices([_reference("2026-11-11"), _reference("2026-08-11")]) == [0]


def test_different_sources_remain_independent() -> None:
    references = [_reference("2026-11-11"), _reference("2026-11-12", "https://example.org/other")]
    assert current_reference_indices(references) == [0, 1]


def test_distinct_pages_quotes_and_source_metadata_remain_independent() -> None:
    for pid in ["P304", "P1683", "P577"]:
        first, second = _reference("2026-11-11"), _reference("2026-11-12")
        second["snaks"][pid] = [
            {
                "snaktype": "value",
                "property": pid,
                "datatype": "string",
                "datavalue": {"value": "Different evidence", "type": "string"},
            }
        ]
        assert current_reference_indices([first, second]) == [0, 1]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("precision", 9),
        ("before", 1),
        ("after", 1),
        ("timezone", 60),
        ("calendarmodel", "http://www.wikidata.org/entity/Q1985786"),
        ("time", "+2026-02-30T00:00:00Z"),
    ],
)
def test_a_partial_or_uncertain_retrieval_date_is_not_coalesced(field: str, value: object) -> None:
    first, second = _reference("2026-11-11"), _reference("2026-11-12")
    second["snaks"]["P813"][0]["datavalue"]["value"][field] = value
    assert current_reference_indices([first, second]) == [0, 1]


def test_multiple_retrieval_dates_remain_independent() -> None:
    first, second = _reference("2026-11-11"), _reference("2026-11-12")
    second["snaks"]["P813"].append(deepcopy(first["snaks"]["P813"][0]))
    assert current_reference_indices([first, second]) == [0, 1]


@pytest.mark.parametrize("field", ["property", "value_type"])
def test_a_malformed_retrieval_snak_does_not_supply_a_date_identity(field: str) -> None:
    first, second = _reference("2026-11-11"), _reference("2026-11-12")
    for reference in [first, second]:
        snak = reference["snaks"]["P813"][0]
        if field == "property":
            snak["property"] = "P577"
        else:
            snak["datavalue"]["type"] = "string"
    assert current_reference_indices([first, second]) == [0, 1]


def test_a_source_without_a_locator_does_not_merge_on_a_date_alone() -> None:
    first, second = _reference("2026-11-11"), _reference("2026-11-12")
    first["snaks"].pop("P854")
    second["snaks"].pop("P854")
    assert current_reference_indices([first, second]) == [0, 1]


def test_the_append_only_key_detects_a_repeated_source_without_a_deletion() -> None:
    assert reference_source_key(_reference("2026-08-11")) == reference_source_key(
        _reference("2026-11-11")
    )


def test_test_wiki_mode_does_not_interpret_live_retrieved_property_ids() -> None:
    assert current_reference_indices(
        [_reference("2026-11-11"), _reference("2026-11-12")], merge_retrieval=False
    ) == [0, 1]


def test_historical_cleanup_preserves_the_whole_claim_and_requires_a_fresh_revision() -> None:
    claim = {
        "id": "Q123$title",
        "rank": "preferred",
        "type": "statement",
        "mainsnak": {
            "property": "P1476",
            "snaktype": "value",
            "datatype": "monolingualtext",
            "datavalue": {
                "value": {"text": "אור הרופאים", "language": "he"},
                "type": "monolingualtext",
            },
        },
        "references": [_reference("2026-08-11"), _reference("2026-11-11")],
    }
    entity = {"id": "Q123", "lastrevid": 123, "claims": {"P1476": [claim]}}
    proposal = build_gila_repair_proposals({"Q123": entity})[0]
    change = proposal["changes"][0]
    assert change["kind"] == "coalesce_retrieval_references"
    assert change["retrieval_variants"] == 1
    expected = deepcopy(claim)
    expected["references"] = [claim["references"][1]]
    assert change["after"] == expected
    validate_gila_repair_proposal(proposal, entity)


def test_a_redirect_response_does_not_supply_a_repair_for_the_requested_qid() -> None:
    entity = {
        "id": "Q124",
        "lastrevid": 123,
        "redirects": {"from": "Q123", "to": "Q124"},
        "claims": {
            "P31": [
                {
                    "id": "Q124$claim",
                    "references": [_reference("2026-08-11"), _reference("2026-11-11")],
                }
            ]
        },
    }
    assert build_gila_repair_proposals({"Q123": entity}) == []
    entity["redirects"] = {"from": "Q999", "to": "Q124"}
    with pytest.raises(ValueError, match="exact claim ID"):
        build_gila_repair_proposals({"Q123": entity})


def test_q141747600_title_keeps_one_latest_source_for_the_four_recorded_dates() -> None:
    references = [
        _reference(day, "https://www.nli.org.il/en/discover/manuscripts/hebrew-manuscripts/"
                   "viewerpage?vid=NNL_ALEPH990001285000205171")
        for day in ["2026-11-03", "2026-11-05", "2026-11-10", "2026-11-11"]
    ]
    assert current_reference_indices(references) == [3]
