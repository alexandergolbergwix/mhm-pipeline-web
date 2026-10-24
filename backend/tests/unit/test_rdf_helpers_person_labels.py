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



def test_sanitize_monolingual_map_publishes_built_labels_verbatim() -> None:
    """The write path must not re-clean build labels. ``clean_marc_label``
    strips trailing ``'`` and ``.`` — so a built ``…אלחגאג'`` reached the
    wiki without the geresh and label_drift failed on the difference
    forever (3 of the 11 stragglers on run 47e03437)."""
    from converter.wikibase.label_sanitize import sanitize_monolingual_map

    out = sanitize_monolingual_map({
        "he": "חיים בן שלום בן דוד בן יצחק אלחגאג'",
        "en": "A. M Chumaceiro Az.",
    })
    assert out["he"] == "חיים בן שלום בן דוד בן יצחק אלחגאג'"
    assert out["en"] == "A. M Chumaceiro Az."
    # whitespace is still trimmed and empty values dropped
    assert sanitize_monolingual_map({"en": "  x  ", "he": "   "}) == {"en": "x"}
