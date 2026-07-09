"""Unit tests for converter.rdf.rdf_helpers."""

from __future__ import annotations

from converter.rdf.rdf_helpers import (
    clean_marc_label,
    is_descriptive_content_title,
    parse_contents_entry,
)


def test_clean_marc_label_strips_outer_double_quotes() -> None:
    assert clean_marc_label('""אב הרחמים""') == "אב הרחמים"


def test_clean_marc_label_normalizes_isbd_colon_quotes() -> None:
    assert clean_marc_label('"פסק דין :" "פסק דין מרבני פראג"') == "פסק דין : פסק דין מרבני פראג"


def test_clean_marc_label_preserves_hebrew_abbreviation_gershayim() -> None:
    text = 'ציון ""אברהם היכיני"" המזכירה שנת ""שלש מאות""'
    cleaned = clean_marc_label(text)
    assert '""' not in cleaned
    assert "אברהם היכיני" in cleaned


def test_is_descriptive_content_title_rejects_gam_prefix() -> None:
    assert is_descriptive_content_title("גם תרגום לטיני באותיות לטיניות")
    assert not is_descriptive_content_title("שיר השירים")


def test_is_descriptive_content_title_rejects_kolel_gam_nusach() -> None:
    assert is_descriptive_content_title("כולל גם נוסח ביוונית")


def test_parse_contents_entry_splits_folio_and_title() -> None:
    parsed = parse_contents_entry("14) דף 298ב-371א : משל הקדמוני")
    assert parsed["sequence"] == 14
    assert parsed["folio_range"] == "298ב-371א"
    assert parsed["title"] == "משל הקדמוני"


def test_clean_marc_label_strips_in_ms_suffix() -> None:
    assert clean_marc_label("משל הקדמוני (in MS 990000403370205171)") == "משל הקדמוני"


def test_clean_marc_label_strips_enum_prefix_when_requested() -> None:
    assert clean_marc_label("14) משל הקדמוני", strip_enum_prefix=True) == "משל הקדמוני"
