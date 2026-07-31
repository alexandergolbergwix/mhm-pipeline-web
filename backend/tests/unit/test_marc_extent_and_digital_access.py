"""Rule W-140 — extent, digital access, material and Hebrew descriptions."""

from __future__ import annotations

from app.pipeline.hmo_canonical_wikidata import _hebrew_manuscript_description
from app.pipeline.marc_ingest import _merge_desktop_extracted_fields
from converter.transformer.extent import UNIT_LEAF, UNIT_PAGE, parse_extent
from converter.wikidata.property_mapping import materials_in_text


class TestParseExtent:
    def test_single_sequence(self) -> None:
        parsed = parse_extent("245 דף")
        assert parsed is not None
        assert (parsed.count, parsed.unit) == (245, UNIT_LEAF)

    def test_multiple_sequences_are_summed(self) -> None:
        """'111, [2] דף' is 113 leaves — the old parser reported 2."""
        parsed = parse_extent("111, [2] דף.")
        assert parsed is not None
        assert parsed.count == 113
        assert parsed.parts == (111, 2)

    def test_volume_collation_sums_per_volume_sequences(self) -> None:
        """'3 כרכים (300, 207, 110 דף)' is 617 leaves in 3 volumes, not 110."""
        parsed = parse_extent("3 כרכים (300, 207, 110 דף).")
        assert parsed is not None
        assert (parsed.count, parsed.volumes) == (617, 3)

    def test_page_unit_is_recognised(self) -> None:
        for text, count in (("623 עמודים ;", 623), ("232 עמ'.", 232), ("22 pages.", 22)):
            parsed = parse_extent(text)
            assert parsed is not None, text
            assert (parsed.count, parsed.unit) == (count, UNIT_PAGE)

    def test_hebrew_numeral_extents(self) -> None:
        for text, count in (
            ("ריג דף.", 213), ("שנב דף.", 352), ("טו דף.", 15),
            ("קצג דף.", 193), ("רצח דף.", 298), ("רפ דף.", 280),
        ):
            parsed = parse_extent(text)
            assert parsed is not None, text
            assert parsed.count == count, text

    def test_hebrew_numeral_sequences_are_summed(self) -> None:
        parsed = parse_extent("מו, עה דף.")
        assert parsed is not None
        assert parsed.count == 121

    def test_roman_prelims_are_counted(self) -> None:
        parsed = parse_extent("XXXI, [1], 358 עמודים.")
        assert parsed is not None
        assert parsed.count == 390

    def test_parenthetical_notes_are_ignored(self) -> None:
        for text, count in (
            ("11 דף (חסר הסוף) ;", 11),
            ('14 דף (ספירה מקורית: קנו ע""א-קסט ע""א).', 14),
            ("36, 40 דף (ספירת דפים מקורית: עב דף).", 76),
        ):
            parsed = parse_extent(text)
            assert parsed is not None, text
            assert parsed.count == count, text

    def test_unnameable_unit_fails_closed(self) -> None:
        """Columns are a real extent but neither leaves nor pages."""
        assert parse_extent("31 עמודות ;") is None

    def test_folio_reference_is_not_a_count(self) -> None:
        assert parse_extent("דף 2א-2ב.") is None

    def test_bare_number_fails_closed(self) -> None:
        assert parse_extent("150") is None

    def test_unit_word_is_never_read_as_a_numeral(self) -> None:
        """'דף' is gematria 84 — it must stay a unit, not become a count."""
        assert parse_extent("דף") is None

    def test_empty_input(self) -> None:
        assert parse_extent("") is None
        assert parse_extent(None) is None


class TestIngestStampsExtentAndDigitalAccess:
    def test_extent_unit_and_volumes_are_stamped(self) -> None:
        record = {"300$a": "3 כרכים (300, 207, 110 דף)."}
        _merge_desktop_extracted_fields(record)
        assert record["extent"] == 617
        assert record["extent_unit"] == UNIT_LEAF
        assert record["volume_count"] == 3

    def test_page_extent_unit_survives_to_the_projection(self) -> None:
        record = {"300$a": "623 עמודים ;"}
        _merge_desktop_extracted_fields(record)
        assert (record["extent"], record["extent_unit"]) == (623, UNIT_PAGE)

    def test_digital_url_is_derived_from_856u(self) -> None:
        record = {"856$u": '"http://www.bl.uk/manuscripts/FullDisplay.aspx?ref=Or_12354"'}
        _merge_desktop_extracted_fields(record)
        assert record["digital_url"] == "http://www.bl.uk/manuscripts/FullDisplay.aspx?ref=Or_12354"

    def test_iiif_manifest_is_recognised(self) -> None:
        record = {"856$u": "https://iiif.nli.org.il/IIIFv21/x/manifest"}
        _merge_desktop_extracted_fields(record)
        assert record["iiif_manifest_url"] == record["digital_url"]


class TestMaterialsInText:
    def test_material_named_in_prose_is_recovered(self) -> None:
        assert materials_in_text("כתב יד בדיו חומה, טמפרה, עלי זהב וכסף על קלף") == ["Q226697"]

    def test_hebrew_prefixed_terms_match(self) -> None:
        assert materials_in_text("נייר ופפירוס") == ["Q11472", "Q125576"]

    def test_hand_and_binding_notes_are_not_materials(self) -> None:
        for note in ("בכתיבות אחדות.", "אוטוגרף.", "דפים אחדים מכורכים שלא כסדרם."):
            assert materials_in_text(note) == [], note

    def test_negated_material_is_not_claimed(self) -> None:
        assert materials_in_text("לא על נייר") == []

    def test_empty_input(self) -> None:
        assert materials_in_text("") == []


class TestHebrewManuscriptDescription:
    def test_language_follows_the_record(self) -> None:
        """10 manuscripts said 'כתב יד עברי' while `en` said Arabic/Italian."""
        text = _hebrew_manuscript_description(
            {"languages": ["ara"], "dates": {"year": "1661"}},
        )
        assert text.startswith("כתב יד ערבי")

    def test_hebrew_default_when_no_language(self) -> None:
        assert _hebrew_manuscript_description(
            {"dates": {"year": "1600"}},
        ).startswith("כתב יד עברי")

    def test_israeli_holder_reads_in_hebrew(self) -> None:
        text = _hebrew_manuscript_description(
            {
                "languages": ["heb"],
                "dates": {"year": "1697"},
                "holding_institution": "The National Library of Israel",
            },
        )
        assert "הספרייה הלאומית" in text
        assert "National Library" not in text

    def test_foreign_holder_keeps_its_own_name(self) -> None:
        """We do not invent a Hebrew name for an institution."""
        text = _hebrew_manuscript_description(
            {"languages": ["ita"], "holding_institution": "The Russian State Library"},
        )
        assert "The Russian State Library" in text

    def test_no_evidence_yields_no_description(self) -> None:
        assert _hebrew_manuscript_description({"languages": ["heb"]}) == ""
