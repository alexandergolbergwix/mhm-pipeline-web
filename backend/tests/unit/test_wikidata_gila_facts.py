"""Independent source expectations catch losses outside the original audit."""

import pytest
from app.pipeline.wikidata_gila_facts import compare_gila_source_facts


def test_audit_detects_a_lost_second_folio_in_the_final_payload() -> None:
    record = {
        "001": "990001",
        "contents": [
            {"title": "ספר הדרושים", "folio_range": folio, "source_field": "505"}
            for folio in ("א-ב", "ג-ד")
        ],
    }
    item = {
        "entity_type": "manuscript",
        "records": ["990001"],
        "statements": [
            {"property": "P3959", "value": "990001"},
            {
                "property": "P1574",
                "value": "Q123",
                "qualifiers": [
                    {"property": "P1932", "value": "ספר הדרושים"},
                    {"property": "P958", "value": "א-ב"},
                ],
            },
        ],
    }
    report = compare_gila_source_facts([record], [item])
    occurrences = [row for row in report["facts"] if row["property"] == "P1574"]
    assert len(occurrences) == 2
    assert [row["proposal_status"] for row in occurrences] == ["identity_review", "missing"]
    assert occurrences[1]["source_field"] == "505"


def test_audit_rejects_a_generic_script_claim_without_the_source_wording() -> None:
    report = compare_gila_source_facts(
        [{"001": "990001", "958$a": "קראית-אשכנזית"}],
        [
            {
                "entity_type": "manuscript",
                "records": ["990001"],
                "statements": [
                    {"property": "P9302", "value": "Q121094898"},
                ],
            }
        ],
    )
    script = next(row for row in report["facts"] if row["property"] == "P9302")
    assert script["proposal_status"] == "missing"
    assert script["expected_qualifiers"] == {"P1932": "קראית-אשכנזית"}


def test_audit_compares_live_folios_and_keeps_the_revision() -> None:
    source = {
        "001": "990001",
        "contents": [{"title": "ספר הדרושים", "folio_range": "ג-ד", "source_field": "505"}],
    }
    entity = {
        "id": "Q123",
        "lastrevid": 321,
        "claims": {
            "P1574": [
                {
                    "id": "Q123$work",
                    "mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q456"}}},
                    "qualifiers": {
                        "P1932": [{"datavalue": {"value": "ספר הדרושים"}}],
                        "P958": [{"datavalue": {"value": "א-ב"}}],
                    },
                }
            ]
        },
    }
    report = compare_gila_source_facts(
        [source], [], qids_by_cn={"990001": "Q123"}, live_entities={"Q123": entity}
    )
    fact = next(row for row in report["facts"] if row["property"] == "P1574")
    assert fact["live_status"] == "missing"
    assert fact["live_revision"] == 321
    assert fact["live_classification"] == "identity_or_scope_uncertain"
    assert report["corrected_qids"] == []


def test_unresolved_bibliography_is_evidence_not_an_absent_source_value() -> None:
    report = compare_gila_source_facts(
        [{"001": "990001", "581$a": "Weisz, Budapest, 1906, page 123."}], []
    )
    fact = next(row for row in report["facts"] if row["source_field"] == "581")
    assert fact["classification"] == "identity_or_scope_uncertain"
    assert fact["proposal_status"] == "source_evidence"
    assert "1906" in fact["source_value"]


def test_audit_does_not_accept_the_original_wrong_work_target() -> None:
    report = compare_gila_source_facts(
        [
            {
                "001": "990001",
                "contents": [
                    {"title": "אגרת סוכה", "source_field": "505", "wikidata_qid": "Q141754992"}
                ],
            }
        ],
        [
            {
                "entity_type": "manuscript",
                "records": ["990001"],
                "statements": [
                    {
                        "property": "P1574",
                        "value": "Q141175530",
                        "qualifiers": [{"property": "P1932", "value": "אגרת סוכה"}],
                    },
                ],
            }
        ],
    )
    fact = next(row for row in report["facts"] if row["property"] == "P1574")
    assert fact["expected_value"] == "Q141754992"
    assert fact["proposal_status"] == "missing"


def test_audit_requires_review_of_a_target_with_the_wrong_author() -> None:
    report = compare_gila_source_facts(
        [
            {
                "001": "990001",
                "contents": [
                    {"title": "דרושים", "author": "יצחק חיים קנטריני", "wikidata_qid": "Q123"}
                ],
            }
        ],
        [
            {
                "entity_type": "manuscript",
                "records": ["990001"],
                "statements": [
                    {
                        "property": "P1574",
                        "value": "Q123",
                        "qualifiers": [{"property": "P1932", "value": "דרושים"}],
                    },
                ],
            },
            {
                "entity_type": "work",
                "existing_qid": "Q123",
                "local_id": "work",
                "records": ["990001"],
                "statements": [{"property": "P2093", "value": "אברהם הלוי"}],
            },
        ],
    )
    fact = next(row for row in report["facts"] if row["property"] == "P1574")
    assert fact["proposal_status"] == "identity_review"
    assert fact["reason"] == "work_author_or_source_requires_review"


def test_a_correct_local_fact_with_a_live_gap_still_requires_review() -> None:
    report = compare_gila_source_facts(
        [{"001": "990001"}],
        [
            {
                "entity_type": "manuscript",
                "records": ["990001"],
                "statements": [{"property": "P3959", "value": "990001"}],
            }
        ],
        qids_by_cn={"990001": "Q123"},
        live_entities={"Q123": {"id": "Q123", "lastrevid": 1234, "claims": {}}},
    )
    fact = next(row for row in report["facts"] if row["property"] == "P3959")
    assert fact["proposal_status"] == "present"
    assert fact["live_status"] == "missing"
    assert fact["review_status"] == "pending"
    assert fact["live_classification"] == "historical_live_defect"


@pytest.mark.parametrize(
    "reference_kind", ["missing", "ambiguous_flat", "source_mismatch", "source_bound"]
)
def test_value_presence_does_not_certify_source_references(reference_kind: str) -> None:
    cn = "990001"
    value = cn if reference_kind != "source_mismatch" else "990002"
    block = [{"property": "P3959", "value": value, "type": "external-id"}]
    claim = {"property": "P217", "value": "Ms. 1"}
    if reference_kind == "ambiguous_flat":
        claim["references"] = block
    elif reference_kind != "missing":
        claim["reference_blocks"] = [block]
    report = compare_gila_source_facts(
        [{"001": cn, "holding_942_shelfmark": "Ms. 1"}],
        [{"entity_type": "manuscript", "records": [cn], "statements": [claim]}],
    )
    fact = next(row for row in report["facts"] if row["property"] == "P217")
    assert fact["proposal_status"] == "present"
    assert fact["proposal_provenance"] == reference_kind
    assert fact["review_status"] == (
        "not_required" if reference_kind == "source_bound" else "pending"
    )


def test_english_description_and_physical_unit_checks_are_explicit() -> None:
    report = compare_gila_source_facts(
        [{"001": "990001", "300$a": "21 דף"}],
        [
            {
                "entity_type": "manuscript",
                "records": ["990001"],
                "descriptions": {"en": "manuscript of ספר הדרושים"},
                "statements": [{"property": "P1104", "value": 21, "unit": "Q1069725"}],
            }
        ],
    )
    description = next(row for row in report["facts"] if row["property"] == "description:en")
    extent = next(row for row in report["facts"] if row["property"] == "P1104")
    assert description["proposal_status"] == "missing"
    assert extent["expected_unit"] == "Q107256474"
    assert extent["proposal_status"] == "missing"


def test_missing_live_references_require_review_even_when_the_value_is_correct() -> None:
    report = compare_gila_source_facts(
        [{"001": "990001", "holding_942_shelfmark": "Ms. 1"}],
        [
            {
                "entity_type": "manuscript",
                "records": ["990001"],
                "statements": [
                    {
                        "property": "P217",
                        "value": "Ms. 1",
                        "reference_blocks": [
                            [
                                {"property": "P3959", "value": "990001", "type": "external-id"},
                            ]
                        ],
                    },
                ],
            }
        ],
        qids_by_cn={"990001": "Q123"},
        live_entities={
            "Q123": {
                "id": "Q123",
                "lastrevid": 1234,
                "claims": {
                    "P217": [
                        {
                            "id": "Q123$shelfmark",
                            "mainsnak": {"snaktype": "value", "datavalue": {"value": "Ms. 1"}},
                        },
                    ]
                },
            }
        },
    )
    fact = next(row for row in report["facts"] if row["property"] == "P217")
    assert fact["proposal_status"] == fact["live_status"] == "present"
    assert fact["proposal_provenance"] == "source_bound"
    assert fact["live_provenance"] == "missing"
    assert fact["review_status"] == "pending"


def test_a_century_with_a_named_year_boundary_remains_a_source_review_case() -> None:
    report = compare_gila_source_facts([{"001": "990001", "260$c": 'מאה י"ז (לפני 1618)'}], [])
    fact = next(row for row in report["facts"] if row["property"] == "P571")
    assert fact["proposal_status"] == "source_evidence"
    assert fact["expected_value"] is None
    assert fact["reason"] == "source_date_conflict_or_boundary_requires_review"


def test_an_unresolved_manuscript_qid_does_not_become_a_false_live_defect() -> None:
    report = compare_gila_source_facts([{"001": "990001"}], [], live_entities={})
    fact = next(row for row in report["facts"] if row["property"] == "P3959")
    assert fact["live_status"] == "not_checked"
    assert fact["live_classification"] == "identity_or_scope_uncertain"
    assert fact["live_reason"] == "manuscript_qid_not_resolved"


def test_native_time_uses_the_uploader_calendar_default_and_keeps_precision() -> None:
    source = {"001": "990001", "260$c": "1603"}
    claim = {
        "property": "P571",
        "value": "+1603-00-00T00:00:00Z",
        "value_type": "time",
        "precision": 9,
    }
    item = {"entity_type": "manuscript", "records": ["990001"], "statements": [claim]}
    report = compare_gila_source_facts([source], [item])
    assert (
        next(row for row in report["facts"] if row["property"] == "P571")["proposal_status"]
        == "present"
    )
    claim["precision"] = 11
    report = compare_gila_source_facts([source], [item])
    assert (
        next(row for row in report["facts"] if row["property"] == "P571")["proposal_status"]
        == "missing"
    )
