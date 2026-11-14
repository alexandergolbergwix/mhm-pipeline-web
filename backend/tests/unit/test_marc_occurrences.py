"""Original field occurrences prevent cross-holding and person associations."""

from copy import deepcopy

import pytest

from app.pipeline.marc_ingest import parse_marc_upload, prepare_record_for_pipeline
from app.pipeline.marc_occurrences import as_marc_fields, from_marc_record


def test_marcxml_keeps_indicators_order_and_empty_source_slots():
    raw = b"""<collection xmlns="http://www.loc.gov/MARC21/slim"><record>
    <controlfield tag="001">990001</controlfield>
    <datafield tag="700" ind1="1" ind2=" "><subfield code="a">First</subfield>
    <subfield code="e">scribe</subfield>
    </datafield>
    <datafield tag="700" ind1="1" ind2=" "><subfield code="a">Second</subfield>
    <subfield code="d">1600-1670</subfield>
    </datafield>
    <datafield tag="880" ind1=" " ind2=" "><subfield code="6">700-01</subfield>
    <subfield code="a">Other script</subfield>
    </datafield>
    </record></collection>"""
    record = parse_marc_upload(raw, filename="source.xml")[0]
    assert [row["tag"] for row in record["marc_occurrences"]] == ["001", "700", "700", "880"]
    assert record["700$d"] == "|1600-1670"
    assert record["marc_occurrences"][1]["indicators"] == ["1", " "]
    assert record["contributors"][0]["role"] == "scribe"
    assert "dates" not in record["contributors"][0]
    assert record["contributors"][1]["dates"] == "1600-1670"


def test_marcxml_rejects_a_dtd():
    with pytest.raises(ValueError, match="DTD"):
        parse_marc_upload(b'<!DOCTYPE record [<!ENTITY x "text">]><record/>', filename="source.xml")


def test_removed_source_fields_cannot_leave_old_derived_values():
    record = prepare_record_for_pipeline(
        {"fields": [{"001": "990001"}, {"942": {"subfields": [{"a": "Library"}, {"z": "Ms 1"}]}}]}
    )
    record["marc_occurrences"] = [row for row in record["marc_occurrences"] if row["tag"] != "942"]
    updated = prepare_record_for_pipeline(record)
    assert not updated.get("holding_942_name")
    assert not updated.get("shelfmark")
    assert "942$a" not in updated


def test_sparse_flat_person_roles_remain_unresolved():
    result = prepare_record_for_pipeline(
        {"001": "990001", "700$a": "First|Second", "700$e": "scribe"}
    )
    assert {row["role"] for row in result["contributors"]} == {"contributor"}
    assert any(
        row["reason"] == "person_role_alignment_unproven"
        for row in result["person_subfield_reviews"]
    )


def test_enhanced_505_responsibility_stays_with_its_original_title():
    raw = {
        "fields": [
            {"001": "990001"},
            {"505": {"subfields": [{"t": "עץ חיים"}, {"r": "חיים ויטאל"}]}},
            {"505": {"subfields": [{"t": "ספר הזוהר"}, {"r": "שמעון בר יוחאי"}]}},
        ]
    }
    result = prepare_record_for_pipeline(raw)
    assert [(row["title"], row["author"], row["source_key"]) for row in result["contents"]] == [
        ("עץ חיים", "חיים ויטאל", "505:1:t:1:1"),
        ("ספר הזוהר", "שמעון בר יוחאי", "505:2:t:1:1"),
    ]
    assert prepare_record_for_pipeline(result) == result


def test_a_single_enhanced_title_retains_its_explicit_folio():
    result = prepare_record_for_pipeline(
        {
            "fields": [
                {"001": "990001"},
                {"505": {"subfields": [{"g": "21ב–22ב"}, {"t": "עץ חיים"}, {"r": "חיים ויטאל"}]}},
            ]
        }
    )
    assert result["contents"][0]["folio_range"] == "21ב–22ב"
    assert not result["contents"][0].get("scope_alignment_unproven")


def test_a_person_field_retains_its_titles_and_all_raw_authority_ids():
    result = prepare_record_for_pipeline(
        {
            "fields": [
                {"001": "990001"},
                {
                    "700": {
                        "subfields": [
                            {"a": "Person"},
                            {"c": "Rabbi"},
                            {"0": "(NLI)987001"},
                            {"0": "(VIAF)123"},
                        ]
                    }
                },
            ]
        }
    )
    person = result["contributors"][0]
    assert person["raw_name_a"] == "Person"
    assert person["title_associated"] == "Rabbi"
    assert person["authority_id"] == "(NLI)987001"
    assert person["authority_ids_raw"] == ["(NLI)987001", "(VIAF)123"]


def test_flat_content_preparation_is_idempotent_and_replaces_stale_derived_text():
    raw = {
        "001": "990001",
        "505$a": "עץ חיים -- ספר הזוהר",
        "581$a": "Catalogue 1906",
        "590$a": "רשומה זמנית",
    }
    first = prepare_record_for_pipeline(raw)
    second = prepare_record_for_pipeline(first)
    assert second == first
    first["505$a"] = "חזקוני"
    first["581$a"] = "Other catalogue 1920"
    first["590$a"] = "רשומה חדשה"
    updated = prepare_record_for_pipeline(first)
    assert {
        entry["title"] for entry in updated["contents"] if entry.get("source_field") == "505"
    } == {"חזקוני"}
    assert not any("1906" in ref["text"] for ref in updated["catalog_references"])
    assert updated["record_status_notes"] == ["רשומה חדשה"]


def test_sparse_holding_columns_cannot_borrow_a_former_city() -> None:
    result = prepare_record_for_pipeline(
        {
            "001": "holding",
            "942$a": "Former Library|Current Library",
            "942$x": "Former City",
            "942$c": "Former Country",
            "942$z": "Former 1|Current 2",
            "710$a": "Current Library|Former Library",
            "710$e": "current owner|former owner",
        }
    )
    assert result["holding_942_name"] == "Current Library"
    assert result["holding_942_shelfmark"] == "Current 2"
    assert not result.get("holding_942_city")
    assert not result.get("holding_942_country")
    assert {row["source_field"] for row in result["holding_subfield_reviews"]} == {"942$x", "942$c"}
    assert prepare_record_for_pipeline(result) == result


def test_structured_occurrences_keep_missing_person_subfields_in_their_field() -> None:
    raw = {
        "fields": [
            {"001": "structured"},
            {"700": {"ind1": "1", "ind2": " ", "subfields": [{"a": "First"}, {"e": "scribe"}]}},
            {
                "700": {
                    "ind1": "1",
                    "ind2": " ",
                    "subfields": [{"a": "Second"}, {"d": "1600-1670"}, {"e": "owner"}],
                }
            },
        ]
    }
    before = deepcopy(raw)
    result = prepare_record_for_pipeline(raw)
    assert raw == before
    assert result["_control_number"] == "structured"
    assert result["contributors"][0]["name"] == "First"
    assert "dates" not in result["contributors"][0]
    assert result["contributors"][1]["dates"] == "1600-1670"
    assert [p["source_occurrence"] for p in result["contributors"]] == ["700:1", "700:2"]
    assert result["700$d"] == "|1600-1670"
    assert prepare_record_for_pipeline(result) == result


def test_structured_holdings_use_one_complete_occurrence() -> None:
    result = prepare_record_for_pipeline(
        {
            "fields": [
                {"001": "holdings"},
                {
                    "942": {
                        "subfields": [{"a": "Former Library"}, {"x": "Former City"}, {"z": "Old 1"}]
                    }
                },
                {"942": {"subfields": [{"a": "Current Library"}, {"z": "New 2"}]}},
                {"710": {"subfields": [{"a": "Former Library"}]}},
                {"710": {"subfields": [{"a": "Current Library"}, {"e": "current owner"}]}},
            ]
        }
    )
    assert result["holding_942_name"] == "Current Library"
    assert result["holding_942_shelfmark"] == "New 2"
    assert not result.get("holding_942_city")
    assert result["former_shelfmarks"] == [{"holder": "Former Library", "shelfmark": "Old 1"}]


def test_source_change_clears_obsolete_holding_fields() -> None:
    first = prepare_record_for_pipeline(
        {"001": "x", "942$a": "Library", "942$x": "City", "942$c": "Country", "942$z": "MS 1"}
    )
    first.pop("942$x")
    first.pop("942$c")
    result = prepare_record_for_pipeline(first)
    assert "holding_942_city" not in result
    assert "holding_942_country" not in result


def test_original_subfield_order_survives_the_desktop_bridge() -> None:
    from converter.parser.marc_reader import MarcField, MarcRecord

    field = MarcField(
        "700",
        ("1", " "),
        {"a": ["First", "Second"], "e": ["scribe"]},
        ordered_subfields=[("a", "First"), ("e", "scribe"), ("a", "Second")],
    )
    record = MarcRecord("x", {"700": [field]}, field_order=[("700", 0)])
    rows = from_marc_record(record)
    assert [(s["code"], s["value"]) for s in rows[0]["subfields"]] == field.ordered_subfields
    assert (
        as_marc_fields({"marc_occurrences": rows})["700"][0].ordered_subfields
        == field.ordered_subfields
    )


def test_binary_marc_upload_retains_occurrences() -> None:
    import pymarc

    record = pymarc.Record()
    record.add_field(pymarc.Field(tag="001", data="binary"))
    for name, fields in [("First", []), ("Second", [pymarc.Subfield("d", "1600-1670")])]:
        record.add_field(
            pymarc.Field(
                tag="700", indicators=["1", " "], subfields=[pymarc.Subfield("a", name), *fields]
            )
        )
    result = parse_marc_upload(record.as_marc(), filename="source.mrc")[0]
    assert len([r for r in result["marc_occurrences"] if r["tag"] == "700"]) == 2
    assert "dates" not in result["contributors"][0]
    assert result["contributors"][1]["dates"] == "1600-1670"


def test_duplicate_occurrence_identifiers_fail_closed() -> None:
    with pytest.raises(ValueError, match="unique"):
        prepare_record_for_pipeline(
            {
                "marc_occurrences": [
                    {"tag": "700", "occurrence": 1, "subfields": []},
                    {"tag": "700", "occurrence": 1, "subfields": []},
                ]
            }
        )
