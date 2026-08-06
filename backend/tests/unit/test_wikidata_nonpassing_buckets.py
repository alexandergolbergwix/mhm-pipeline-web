"""Regression tests for run 48ba6c13 non-passing Wikidata Studio buckets."""

from __future__ import annotations

import pytest

from converter.transformer.field_handlers import FieldHandlers
from converter.wikidata.item_builder import WikidataItemBuilder, manuscript_he_designation


_MARC_SCRIBE = "גבאי, טוביה בן חיים יצחק"
_MAZAL_ROW = "יצחק בן שלמה בן חיים גבאי"


def _statements(item, pid: str):
    return [s for s in item.statements if s.property_id == pid]


class TestPersonIdentifierFailClosed:
    def test_heading_mismatch_drops_p8189(self) -> None:
        items = WikidataItemBuilder().build_all([{
            "_control_number": "990001404380205171",
            "title": "ספר",
            "shelfmark": "F 1",
            "contributors": [{"name": _MARC_SCRIBE, "role": "מעתיק", "field": "700"}],
            "marc_authority_matches": [{
                "name": _MARC_SCRIBE,
                "entity_text": _MARC_SCRIBE,
                "role": "מעתיק",
                "mazal_id": "987007299516905171",
                "preferred_name_heb": _MAZAL_ROW,
                "approved": True,
            }],
        }])
        person = next(i for i in items if i.entity_type == "person")
        assert _statements(person, "P8189") == []

    def test_crosscheck_fail_drops_p8189(self) -> None:
        items = WikidataItemBuilder().build_all([{
            "_control_number": "990001404380205171",
            "title": "ספר",
            "shelfmark": "F 1",
            "contributors": [{"name": "מולכו, שבתי", "role": "מעתיק", "field": "700"}],
            "marc_authority_matches": [{
                "name": "מולכו, שבתי",
                "entity_text": "מולכו, שבתי",
                "role": "מעתיק",
                "mazal_id": "987007299516905171",
                "preferred_name_heb": "מולכו, שבתי",
                "guard_flags": ["wikidata_crosscheck_fail"],
                "approved": True,
            }],
        }])
        person = next(i for i in items if i.entity_type == "person")
        assert _statements(person, "P8189") == []


class TestCanonicalP921Grounding:
    def test_exodus_without_subject_heading_is_not_emitted(self) -> None:
        item = WikidataItemBuilder().build_manuscript_item({
            "_control_number": "EXODUS-NO-SUBJ",
            "title": "פירוש",
            "canonical_references": [{"hierarchy": "Bible", "book": "Exodus"}],
            "subjects": [{"term": "Cabala", "type": "topic"}],
        })
        assert _statements(item, "P921") == []

    def test_exodus_with_matching_subject_is_emitted(self) -> None:
        item = WikidataItemBuilder().build_manuscript_item({
            "_control_number": "EXODUS-SUBJ",
            "title": "פירוש",
            "canonical_references": [{"hierarchy": "Bible", "book": "Exodus"}],
            "subjects": [{"term": "שמות", "type": "topic"}],
        })
        assert any(s.value == "Q9190" for s in _statements(item, "P921"))


class TestDescriptionYearPrecision:
    def test_century_only_description_omits_numeric_year(self) -> None:
        item = WikidataItemBuilder().build_manuscript_item({
            "_control_number": "CENTURY-ONLY",
            "title": "כתב יד",
            "dates": {
                "year": 1501,
                "original_string": "16th century",
            },
        })
        assert "1501" not in item.descriptions["en"]
        assert "16th century" in item.descriptions["en"]

    def test_explicit_year_is_kept(self) -> None:
        item = WikidataItemBuilder().build_manuscript_item({
            "_control_number": "YEAR-EXPLICIT",
            "title": "כתב יד",
            "dates": {
                "year": 1612,
                "date_format": "FullDate",
                "original_string": "1612",
            },
        })
        assert "1612" in item.descriptions["en"]


class TestManuscriptLanguageLabel:
    def test_latin_primary_language_not_hebrew_manuscript(self) -> None:
        item = WikidataItemBuilder().build_manuscript_item({
            "_control_number": "LATIN-MS",
            "title": "קובץ",
            "languages": ["lat"],
            "shelfmark": "F 123",
        })
        assert "לטיני" in item.labels["he"]
        assert "עברי" not in item.labels["he"]


class TestFacsimileHebrewLabel:
    def test_facsimile_hebrew_designation(self) -> None:
        record = {
            "languages": ["heb"],
            "notes": ["דפוס צלום של הוצאת ברלין"],
        }
        label = manuscript_he_designation(record, "F 1", holder_name="")
        assert "פקסימיליה" in label
        assert "כתב יד עברי" not in label

    def test_facsimile_item_he_label(self) -> None:
        item = WikidataItemBuilder().build_manuscript_item({
            "_control_number": "990019020880205171",
            "title": "פנקס",
            "notes": ["דפוס צלום של הוצאת ברלין, תרפ\"ה"],
            "shelfmark": "F 99",
        })
        assert "פקסימיליה" in item.labels["he"]


class TestMillimetreDimensions:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("145X100", {"height_mm": 145, "width_mm": 100}),
            ("145 x 100", {"height_mm": 145, "width_mm": 100}),
        ],
    )
    def test_unitless_manuscript_dimensions_are_millimetres(
        self, text: str, expected: dict[str, int],
    ) -> None:
        assert FieldHandlers._parse_dimensions(text) == expected


class TestAdoptionBlocksConflation:
    def test_heading_mismatch_blocks_adoption(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import adopt_identifier_matched_duplicates

        item = {
            "local_id": "QDraft_Person_110",
            "entity_type": "person",
            "heading_mismatch": {"reason": "different given name"},
            "_wikidata_existence": {
                "status": "candidates_found",
                "candidates": [{"qid": "Q178293", "matched_on": "P8189=1"}],
            },
        }
        assert adopt_identifier_matched_duplicates([item]) == []
        assert "existing_qid" not in item

    def test_crosscheck_fail_blocks_adoption(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import adopt_identifier_matched_duplicates

        item = {
            "local_id": "QDraft_Person_110",
            "entity_type": "person",
            "authority_evidence": [{"guard_flags": ["wikidata_crosscheck_fail"]}],
            "_wikidata_existence": {
                "status": "candidates_found",
                "candidates": [{"qid": "Q178293", "matched_on": "P8189=1"}],
            },
        }
        assert adopt_identifier_matched_duplicates([item]) == []
        assert "existing_qid" not in item
