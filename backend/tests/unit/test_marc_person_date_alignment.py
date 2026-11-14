"""Person dates require intact occurrence slots in flattened MARC sources."""

from copy import deepcopy

import pytest

from app.pipeline.marc_ingest import extract_named_entities, prepare_record_for_pipeline
from app.pipeline.wikidata_gila_audit import build_gila_audit_report
from app.pipeline.wikidata_gila_facts import compare_gila_source_facts


def test_hizzuk_dates_do_not_move_to_the_scribe_or_the_wrong_contributor() -> None:
    source = {
        "001": "990000885680205171",
        "700$a": "פיבש יואל בן שמואל שליט|אליהו בן אשר,|משה בן יהודה,|מינסטר, סבסטין,",
        "700$e": "(מעתיק)|(ממנו)|(מוזכר)|(אליו)",
        "700$d": "1469-1549|1489-1552",
    }
    prepared = prepare_record_for_pipeline(source)

    assert all("dates" not in person for person in prepared["contributors"])
    assert all("dates" not in person for person in extract_named_entities(prepared))
    assert prepared["person_subfield_reviews"] == [
        {
            "source_field": "700$d",
            "names": source["700$a"].split("|"),
            "values": ["1469-1549", "1489-1552"],
            "reason": "person_date_alignment_unproven",
        }
    ]
    assert prepare_record_for_pipeline(prepared) == prepared


@pytest.mark.parametrize("tag", ["100", "700", "800", "600"])
def test_an_explicit_empty_date_slot_does_not_shift_the_next_date(tag: str) -> None:
    prepared = prepare_record_for_pipeline(
        {
            "001": "slots",
            f"{tag}$a": "First|Second|Third",
            f"{tag}$d": '"|1600-1670|"',
        }
    )
    field = "authors" if tag == "100" else "subjects" if tag == "600" else "contributors"
    people = prepared[field]
    assert "dates" not in people[0]
    assert people[1]["dates"] == "1600-1670"
    assert "dates" not in people[2]
    assert not prepared.get("person_subfield_reviews")


def test_complete_date_arrays_keep_their_person_associations() -> None:
    prepared = prepare_record_for_pipeline(
        {
            "001": "complete",
            "700$a": "First|Second",
            "700$d": "1500-1570|1600-1670",
        }
    )
    assert [person["dates"] for person in prepared["contributors"]] == ["1500-1570", "1600-1670"]


def test_a_corrected_raw_source_clears_its_previous_alignment_case() -> None:
    prepared = prepare_record_for_pipeline(
        {
            "001": "repair",
            "700$a": "First|Second",
            "700$d": "1600-1670",
        }
    )
    repaired = deepcopy(prepared)
    repaired["700$d"] = "|1600-1670"
    result = prepare_record_for_pipeline(repaired)
    assert not result["person_subfield_reviews"]
    assert "dates" not in result["contributors"][0]
    assert result["contributors"][1]["dates"] == "1600-1670"


def test_structured_pipe_entries_do_not_guess_the_date_owner() -> None:
    source = {
        "_control_number": "structured",
        "contributors": [{"name": "First|Second", "dates": "1600-1670", "field": "700"}],
    }
    result = prepare_record_for_pipeline(source)
    assert all("dates" not in person for person in result["contributors"])
    assert result["person_subfield_reviews"][0]["values"] == ["1600-1670"]
    assert prepare_record_for_pipeline(result) == result


def test_a_subject_date_without_a_name_remains_source_evidence() -> None:
    result = prepare_record_for_pipeline({"001": "orphan-date", "600$d": "1600-1670"})
    assert result["person_subfield_reviews"] == [
        {
            "source_field": "600$d",
            "names": [],
            "values": ["1600-1670"],
            "reason": "person_date_alignment_unproven",
        }
    ]


def test_raw_subjects_replace_stale_derived_date_associations() -> None:
    result = prepare_record_for_pipeline(
        {
            "001": "stale-subjects",
            "600$a": "First|Second",
            "600$d": "|1600-1670",
            "subjects": [
                {"name": "First", "type": "person", "field": "600", "dates": "1600-1670"},
                {"name": "Second", "type": "person", "field": "600"},
                {"name": "Medicine", "type": "topic", "field": "650"},
            ],
        }
    )
    subjects = {row["name"]: row for row in result["subjects"]}
    assert set(subjects) == {"First", "Second", "Medicine"}
    assert "dates" not in subjects["First"]
    assert subjects["Second"]["dates"] == "1600-1670"
    assert subjects["Medicine"]["field"] == "650"


def test_the_audit_retains_unassigned_dates_without_a_false_public_fact() -> None:
    result = compare_gila_source_facts(
        [{"001": "audit", "700$a": "First|Second", "700$d": "1600-1670"}],
        [],
    )
    facts = [fact for fact in result["facts"] if fact["scope"] == "person_date_alignment"]
    assert len(facts) == 1
    assert facts[0]["source_field"] == "700$d"
    assert facts[0]["proposal_status"] == "source_evidence"
    assert facts[0]["property"] == ""
    assert "1600-1670" in facts[0]["source_value"]


def test_the_record_inventory_classifies_the_date_group_under_email_point_five() -> None:
    report = build_gila_audit_report(
        [{"001": "audit", "700$a": "First|Second", "700$d": "1600-1670"}],
        [],
    )
    cases = [
        case
        for case in report["records"][0]["cases"]
        if case["reason"] == "person_date_alignment_unproven"
    ]
    assert cases == [
        {
            "point": 5,
            "classification": "identity_or_scope_uncertain",
            "field": "700$d",
            "reason": "person_date_alignment_unproven",
        }
    ]
