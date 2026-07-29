"""Rule W-137 — manuscript descriptions are generated, never catalog notes."""

from __future__ import annotations

from app.pipeline.hmo_canonical import CanonicalHmoEntity
from app.pipeline.hmo_canonical_wikidata import (
    _is_catalog_note_description,
    _upload_descriptions,
    canonical_studio_context,
)
from converter.wikidata.item_builder import _build_manuscript_description

_HEBREW_RE = __import__("re").compile(r"[\u0590-\u05ff]")

CN = "990000633490205171"


def _entity(descriptions: dict[str, str]) -> CanonicalHmoEntity:
    return CanonicalHmoEntity(
        local_id=f"QDraft_MS_{CN}",
        source_uri=f"https://w3id.org/mhm/ontology#MS_{CN}",
        wikibase_id="Q1",
        revision_id=1,
        labels={"he": "מחזור"},
        descriptions=descriptions,
        aliases={},
        claims=[],
        authority_evidence=[],
        source_fingerprint="fp",
        entity_type="F4_Manifestation",
        control_numbers=[CN],
    )


def _context() -> object:
    return canonical_studio_context(marc_records=[{
        "_control_number": CN,
        "title": "מחזור",
        "languages": ["heb"],
        "dates": {"original_string": "1662", "year": "1662"},
        "852$j": "F 46266",
        "710$a": "The Bodleian Libraries, University of Oxford",
    }])


class TestCatalogNoteDetection:
    def test_rda_carrier_placeholder_is_a_note(self) -> None:
        assert _is_catalog_note_description(
            "Hebrew manuscript. · MARC 336 content type: · MARC 337 media type:",
        )

    def test_folio_prefixed_note_is_a_note(self) -> None:
        assert _is_catalog_note_description("F. 96b: A short list of allusions")

    def test_english_catalog_prose_is_a_note(self) -> None:
        assert _is_catalog_note_description("According to Louis Levin, the author is …")
        assert _is_catalog_note_description("Related material: AHW-14")

    def test_hebrew_note_prose_is_a_note(self) -> None:
        assert _is_catalog_note_description("בסוף הכרך השני הועתק חבור בערבית")

    def test_long_prose_is_a_note(self) -> None:
        assert _is_catalog_note_description("x " * 200)

    def test_generated_description_is_not_a_note(self) -> None:
        assert not _is_catalog_note_description(
            "Hebrew manuscript, 1662, The Bodleian Libraries, University of Oxford",
        )


class TestManuscriptDescriptions:
    def test_generated_form_wins_over_a_stored_note(self) -> None:
        out = _upload_descriptions(
            _entity({
                "en": "Hebrew manuscript. · MARC 336 content type: text",
                "he": "F. 96b: A short list of allusions",
            }),
            "manuscript",
            context=_context(),
        )
        assert "MARC 336" not in out["en"]
        assert out["en"].startswith("Hebrew manuscript, 1662")
        assert out["he"].startswith("כתב יד עברי")

    def test_hebrew_slot_is_generated_not_a_note(self) -> None:
        out = _upload_descriptions(_entity({}), "manuscript", context=_context())
        assert "F 46266" in out["he"]
        assert "96b" not in out["he"]

    def test_no_marc_context_keeps_a_usable_fallback(self) -> None:
        out = _upload_descriptions(_entity({}), "manuscript", context=None)
        assert out["en"] == "Hebrew manuscript, National Library of Israel"

    def test_subjects_are_not_appended_to_the_description(self) -> None:
        desc = _build_manuscript_description({
            "_control_number": CN,
            "languages": ["heb"],
            "dates": {"original_string": "1662", "year": "1662"},
            "subjects": ["Kabbalah", "Liturgy", "Piyyutim", "Midrash"],
        })
        assert "Subjects include" not in desc
        assert desc.startswith("Hebrew manuscript, 1662")


class TestDescriptionLanguageRouting:
    def test_hebrew_text_never_stays_in_the_english_slot(self) -> None:
        from app.pipeline.hmo_canonical_wikidata import _description_language_slot

        assert _description_language_slot("en", "מחזור מנהג אשכנז") == "he"

    def test_latin_text_moves_out_of_the_hebrew_slot(self) -> None:
        from app.pipeline.hmo_canonical_wikidata import _description_language_slot

        assert _description_language_slot("he", "Related material: AHW-14") == "en"

    def test_mixed_script_english_description_is_dropped(self) -> None:
        from app.pipeline.hmo_canonical_wikidata import _description_language_slot

        assert _description_language_slot("en", 'Work by שד"ל, 19th century') == ""

    def test_work_hebrew_description_routes_and_keeps_english_generated(self) -> None:
        out = _upload_descriptions(
            _entity({"en": "מחזור מנהג אשכנז המערבי"}), "work", context=None,
        )
        assert "en" not in out or not _HEBREW_RE.search(out["en"])
        assert out.get("he") == "מחזור מנהג אשכנז המערבי"
