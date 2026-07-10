"""Unit tests for converter.rdf.rdf_helpers."""

from __future__ import annotations

from converter.rdf.rdf_helpers import (
    clean_marc_label,
    clean_person_display_name,
    disambiguate_person_label,
    disambiguate_work_label,
    is_descriptive_content_title,
    label_language_for_text,
    parse_contents_entry,
    sanitize_work_title,
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


def test_sanitize_work_title_drops_unbalanced_quotes() -> None:
    assert sanitize_work_title('ספר הקבלה לאברהם בן דוד (הראב"ד') == (
        "ספר הקבלה לאברהם בן דוד"
    )


def test_sanitize_work_title_drops_unbalanced_open_paren() -> None:
    assert sanitize_work_title("קטע מפרוש התורה (מסוף שמות") == "קטע מפרוש התורה"


def test_is_descriptive_content_title_rejects_in_latin_fragment() -> None:
    assert is_descriptive_content_title("Meir Netiv in Latin")


def test_label_language_for_text_detects_latin() -> None:
    assert label_language_for_text("Diodati Segre") == "en"
    assert label_language_for_text("ספר תהילים") == "he"


def test_disambiguate_person_label_adds_ms_scope() -> None:
    assert disambiguate_person_label("יעקב", control_number="990001") == "יעקב (MS 990001)"


def test_disambiguate_work_label_adds_ms_scope() -> None:
    assert disambiguate_work_label("תורה", "990001") == "תורה (MS 990001)"


def test_clean_person_display_name_strips_dangling_ben() -> None:
    assert clean_person_display_name("לוב, יצחק בן") == "לוב, יצחק"
    assert disambiguate_person_label("יצחק לוריא") == "יצחק לוריא"


def test_clean_person_display_name_strips_dangling_ibn() -> None:
    assert clean_person_display_name("חביב, שמעון אבן") == "חביב, שמעון"


def test_sanitize_work_title_preserves_gershayim() -> None:
    assert sanitize_work_title('שד"ל') == 'שד"ל'
    assert sanitize_work_title('ה"ה') == 'ה"ה'


def test_sanitize_work_title_drops_dangling_close_paren() -> None:
    assert sanitize_work_title("משל הקדמוני)") == "משל הקדמוני"
