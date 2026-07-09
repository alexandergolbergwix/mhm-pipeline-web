"""Person label compatibility helpers."""

from __future__ import annotations

from converter.rdf.rdf_helpers import preferred_lat_label_compatible


def test_preferred_lat_compatible_when_names_overlap() -> None:
    assert preferred_lat_label_compatible("Salomon ben Isaac", "Salomon ben Isaac")


def test_preferred_lat_incompatible_for_catalog_heading_mismatch() -> None:
    assert not preferred_lat_label_compatible("יהודה בן יוסף", "Kahana, Yehuda")
    assert not preferred_lat_label_compatible("יצחק", "לוריא, יצחק")


def test_clean_marc_label_strips_trailing_comma() -> None:
    from converter.rdf.rdf_helpers import clean_marc_label

    assert clean_marc_label("אהרן בן אליהו,") == "אהרן בן אליהו"
    assert clean_marc_label("יוסף יוזלן,") == "יוסף יוזלן"

