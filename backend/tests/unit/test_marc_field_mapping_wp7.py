"""WP7 mapping corrections — 035 identity fallback and MARC 880 linking.

Both were silent losses: a record with no ``001`` was unjoinable, and every
``880`` row was dropped even though ``880$6`` names the tag it mirrors.

Rule W-67 (docs/architecture/rules/wikidata-studio.md) requires fail-closed
handling here — an invented control number joins a record to the wrong
Wikidata item, and a guessed linked tag projects romanized text as a
vernacular claim.
"""
from __future__ import annotations

from app.pipeline.marc_ingest import (
    control_number_from_035,
    prepare_record_for_pipeline,
)

# ── WP7.2: 035$a → P3959 identity channel ────────────────────────────────


def test_035_extracts_the_nli_aleph_number() -> None:
    assert control_number_from_035('"(IsJJNL)000118870NNL01-Aleph"') == "000118870"


def test_035_accepts_a_bare_number() -> None:
    assert control_number_from_035("000118870") == "000118870"


def test_035_strips_an_agency_prefix_without_suffix() -> None:
    assert control_number_from_035("(DLC)   12345") == "12345"


def test_035_fails_closed_on_non_numeric_values() -> None:
    assert control_number_from_035("") == ""
    assert control_number_from_035(None) == ""
    assert control_number_from_035("not a control number") == ""
    # Digits mixed with words are not a control number we can name.
    assert control_number_from_035("MS 12 vol 3") == ""


def test_001_stays_the_primary_identity_channel() -> None:
    record = {"001": "990001188700205171", "035$a": "(IsJJNL)000118870NNL01-Aleph"}
    assert prepare_record_for_pipeline(record)["_control_number"] == "990001188700205171"


def test_035_fills_the_channel_when_001_is_absent() -> None:
    record = {"035$a": "(IsJJNL)000118870NNL01-Aleph", "245$a": "כותרת"}
    assert prepare_record_for_pipeline(record)["_control_number"] == "000118870"


# ── WP7.3: 880 projects as its linked tag ────────────────────────────────


def test_880_title_variant_feeds_the_romanized_title_channel() -> None:
    record = {
        "001": "990001238680205171",
        "245$a": "תרגום ערבי לתרגום הארמי לשיר השירים.",
        "880$6": "245-01/(B",
        "880$a": "Targum ʽArvi la-Targum ha-Arami le-Shir ha-Shirim",
    }
    prepared = prepare_record_for_pipeline(record)
    assert prepared["marc_880"] == "Targum ʽArvi la-Targum ha-Arami le-Shir ha-Shirim"
    assert prepared["title_romanized"] == prepared["marc_880"]
    # The vernacular title is preserved — 880 is an additional script, not a
    # replacement (Rule W-65: romanization never overwrites the source title).
    assert prepared["title"].startswith("תרגום ערבי לתרגום הארמי")


def test_880_records_the_linked_tag_it_mirrors() -> None:
    record = {"001": "x", "880$6": "245-01/(B", "880$a": "Sefer ha-ḥaruzim", "880$b": "perush"}
    linked = prepare_record_for_pipeline(record)["linked_880"]
    assert linked == [
        {
            "linked_tag": "245",
            "script": "B",
            "source_field": "880",
            "subfields": {"a": "Sefer ha-ḥaruzim", "b": "perush"},
        }
    ]


def test_880_name_variant_feeds_the_romanized_name_channel() -> None:
    record = {"001": "x", "880$6": "100-01/(B", "880$a": "Maimonides, Moses"}
    prepared = prepare_record_for_pipeline(record)
    assert prepared["marc_880_name"] == "Maimonides, Moses"
    assert prepared["name_romanized"] == "Maimonides, Moses"


def test_880_does_not_overwrite_an_existing_romanization() -> None:
    record = {
        "001": "x",
        "marc_880": "already set",
        "880$6": "245-01/(B",
        "880$a": "ignored",
    }
    assert prepare_record_for_pipeline(record)["marc_880"] == "already set"


def test_unparseable_880_link_is_left_alone() -> None:
    """Fail closed: a $6 we cannot parse is not guessed onto a tag."""
    record = {"001": "x", "880$6": "garbage", "880$a": "text"}
    prepared = prepare_record_for_pipeline(record)
    assert "linked_880" not in prepared
    assert "marc_880" not in prepared


def test_record_without_880_is_untouched() -> None:
    record = {"001": "x", "245$a": "כותרת"}
    prepared = prepare_record_for_pipeline(record)
    assert "linked_880" not in prepared
