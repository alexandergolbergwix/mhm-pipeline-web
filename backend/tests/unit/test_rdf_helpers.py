"""Unit tests for converter.rdf.rdf_helpers."""

from __future__ import annotations

from converter.rdf.rdf_helpers import (
    clean_marc_label,
    is_descriptive_content_title,
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
