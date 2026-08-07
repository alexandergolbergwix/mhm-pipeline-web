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
    def test_crosscheck_fail_alone_does_not_block_adoption(self) -> None:
        """After W-170 strips bad IDs, a clean heading may still adopt."""
        from app.pipeline.wikidata_duplicate_probe import adopt_identifier_matched_duplicates

        item = {
            "local_id": "QDraft_Person_105",
            "entity_type": "person",
            "labels": {"he": "ישראל בן יוסף כרמי"},
            "authority_evidence": [{"guard_flags": ["wikidata_crosscheck_fail"]}],
            "_wikidata_existence": {
                "status": "candidates_found",
                "candidates": [{
                    "qid": "Q132798378",
                    "matched_on": "P8189=987007263785105171",
                    "label": "ישראל בן יוסף כרמי",
                }],
            },
        }
        assert adopt_identifier_matched_duplicates([item])
        assert item["existing_qid"] == "Q132798378"

    def test_he_en_label_difference_does_not_block_identifier_adoption(self) -> None:
        """Trusted HE preferred + matching preferred_lat may adopt an EN WD label."""
        from app.pipeline.wikidata_duplicate_probe import adopt_identifier_matched_duplicates

        item = {
            "local_id": "QDraft_Person_90",
            "entity_type": "person",
            "labels": {"he": "יהודה שאול בן דוד איש קוסטליץ"},
            "authority_evidence": [{
                "preferred_name_heb": "יהודה שאול בן דוד איש קוסטליץ",
                "preferred_name_lat": "Ben-Shaul, Yehuda",
            }],
            "_wikidata_existence": {
                "status": "candidates_found",
                "candidates": [{
                    "qid": "Q6645488",
                    "matched_on": "P8189=987007258453305171",
                    "label": "Yehuda Ben-Shaul",
                }],
            },
        }
        assert adopt_identifier_matched_duplicates([item])
        assert item["existing_qid"] == "Q6645488"

    def test_he_only_without_trusted_latin_refuses_cross_script_adoption(self) -> None:
        """Maurizio→Kagel: HE-only label must not adopt an EN candidate by ID alone."""
        from app.pipeline.wikidata_duplicate_probe import adopt_identifier_matched_duplicates

        item = {
            "local_id": "QDraft_Person_110",
            "entity_type": "person",
            "labels": {"he": "מאוריציו"},
            "statements": [
                {"property_id": "P8189", "value": "987007263446605171"},
            ],
            "_wikidata_existence": {
                "status": "candidates_found",
                "candidates": [{
                    "qid": "Q178293",
                    "matched_on": "P8189=987007263446605171",
                    "label": "Mauricio Kagel",
                }],
            },
        }
        assert adopt_identifier_matched_duplicates([item]) == []
        assert "existing_qid" not in item
        assert item["_wikidata_existence"]["status"] == "absent"
        assert item["statements"] == []
        assert item["_wikidata_existence"]["adoption"]["stripped_identifier"] == (
            "P8189=987007263446605171"
        )

    def test_he_en_map_adopts_without_preferred_lat(self) -> None:
        """Abraham Monson / Yehuda Ben-Shaul: mapped HE↔EN tokens adopt fail-closed."""
        from app.pipeline.wikidata_duplicate_probe import adopt_identifier_matched_duplicates

        monson = {
            "local_id": "QDraft_Person_117",
            "entity_type": "person",
            "labels": {"he": "אברהם מונסון"},
            "_wikidata_existence": {
                "status": "candidates_found",
                "candidates": [{
                    "qid": "Q114038729",
                    "matched_on": "P8189=1",
                    "label": "Abraham Monson",
                }],
            },
        }
        benshaul = {
            "local_id": "QDraft_Person_90",
            "entity_type": "person",
            "labels": {"he": "יהודה שאול בן דוד איש קוסטליץ"},
            "_wikidata_existence": {
                "status": "candidates_found",
                "candidates": [{
                    "qid": "Q6645488",
                    "matched_on": "P8189=1",
                    "label": "Yehuda Ben-Shaul",
                }],
            },
        }
        assert adopt_identifier_matched_duplicates([monson, benshaul])
        assert monson["existing_qid"] == "Q114038729"
        assert benshaul["existing_qid"] == "Q6645488"

    def test_family_bynam_mismatch_does_not_strip_passing_identity(self) -> None:
        """Toponymic / family-heading disagreements must not drop full-passing P8189."""
        from app.pipeline.hmo_canonical_wikidata import _suppress_unconfirmed_person_identity
        from converter.wikidata.item_models import WikidataItem, WikidataStatement

        item = WikidataItem(
            local_id="QDraft_Person_32",
            entity_type="person",
            labels={"he": "סעדיה בן שלמה אלקיסי"},
            statements=[
                WikidataStatement(
                    property_id="P8189",
                    value="987007507328605171",
                    value_type="external-id",
                ),
            ],
            authority_evidence=[{
                "mazal_id": "987007507328605171",
                "preferred_name_heb": "טויל, סעדיה בן שלמה",
                "preferred_name_lat": "Ṭaṿil, Seʻadyah ben Shelomoh",
            }],
        )
        assert _suppress_unconfirmed_person_identity([item]) == []
        assert any(s.property_id == "P8189" for s in item.statements)
        assert item.heading_mismatch is None

    def test_manuscript_skips_label_gate_on_identifier_adoption(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import adopt_identifier_matched_duplicates

        item = {
            "local_id": "QDraft_MS_1",
            "entity_type": "manuscript",
            "labels": {"en": "KTIV F 1", "he": "כתב יד"},
            "_wikidata_existence": {
                "status": "candidates_found",
                "candidates": [{
                    "qid": "Q123",
                    "matched_on": "P3959=990001801390205171",
                    "label": "Cambridge Hebrew MS",
                }],
            },
        }
        assert adopt_identifier_matched_duplicates([item])
        assert item["existing_qid"] == "Q123"

    def test_candidate_label_conflict_blocks_adoption(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import adopt_identifier_matched_duplicates

        item = {
            "local_id": "QDraft_Person_110",
            "entity_type": "person",
            "labels": {"en": "Maurizio of Savoy"},
            "_wikidata_existence": {
                "status": "candidates_found",
                "candidates": [{
                    "qid": "Q178293",
                    "matched_on": "P8189=1",
                    "label": "Mauricio Kagel",
                }],
            },
        }
        assert adopt_identifier_matched_duplicates([item]) == []
        assert "existing_qid" not in item
        assert item["_wikidata_existence"]["adoption"]["adopted"] is False
        assert item["_wikidata_existence"]["status"] == "absent"
        assert item["statements"] == []

    def test_heading_mismatch_blocks_adoption_and_clears_qid(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import adopt_identifier_matched_duplicates

        item = {
            "local_id": "mazal:987007305443705171",
            "entity_type": "person",
            "heading_mismatch": {"reason": "different family name"},
            "existing_qid": "Q86007560",
            "_wikidata_existence": {
                "status": "candidates_found",
                "candidates": [{"qid": "Q86007560", "matched_on": "P8189=1"}],
            },
        }
        assert adopt_identifier_matched_duplicates([item]) == []
        assert "existing_qid" not in item
        assert item["_wikidata_existence"]["adoption"]["adopted"] is False

    def test_prior_bad_adoption_is_cleared_on_label_conflict(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import adopt_identifier_matched_duplicates

        item = {
            "local_id": "QDraft_Person_110",
            "entity_type": "person",
            "labels": {"en": "Maurizio of Savoy"},
            "existing_qid": "Q178293",
            "statements": [{"property_id": "P8189", "value": "1"}],
            "_wikidata_existence": {
                "status": "candidates_found",
                "candidates": [{
                    "qid": "Q178293",
                    "matched_on": "P8189=1",
                    "label": "Mauricio Kagel",
                }],
            },
        }
        adopt_identifier_matched_duplicates([item])
        assert "existing_qid" not in item
        assert item["_wikidata_existence"]["adoption"]["adopted"] is False
        assert item["_wikidata_existence"]["status"] == "absent"
        assert item["statements"] == []


class TestPreferredLabelStrip:
    def test_sara_vs_shabtai_strips_p8189(self) -> None:
        from app.pipeline.hmo_canonical_wikidata import _suppress_unconfirmed_person_identity
        from converter.wikidata.item_models import WikidataItem, WikidataStatement

        item = WikidataItem(
            local_id="QDraft_Person_112",
            entity_type="person",
            labels={"he": "שבתי מולכו"},
            statements=[
                WikidataStatement(
                    property_id="P8189",
                    value="987007265556705171",
                    value_type="external-id",
                ),
            ],
            authority_evidence=[{
                "mazal_id": "987007265556705171",
                "preferred_name_heb": "מולכו, שרה",
                "preferred_name_lat": "Molho, Sara",
            }],
        )
        assert _suppress_unconfirmed_person_identity([item])
        assert not any(s.property_id == "P8189" for s in item.statements)
        assert item.heading_mismatch is not None

    def test_al_particle_surname_strips_father_id_on_son(self) -> None:
        """``אל חמדי`` vs ``אלחמדי`` is the same family (Person_17)."""
        from app.pipeline.hmo_canonical_wikidata import _suppress_unconfirmed_person_identity
        from converter.wikidata.item_models import WikidataItem, WikidataStatement

        item = WikidataItem(
            local_id="QDraft_Person_17",
            entity_type="person",
            labels={"he": "יחיא בן דוד אלחמדי"},
            statements=[
                WikidataStatement(
                    property_id="P8189",
                    value="987007298284205171",
                    value_type="external-id",
                ),
            ],
            authority_evidence=[{
                "preferred_name_heb": "אל חמדי, דוד בן מסעוד",
                "preferred_name_lat": "Daṿid ben Masʻud",
            }],
        )
        assert _suppress_unconfirmed_person_identity([item])
        assert not any(s.property_id == "P8189" for s in item.statements)
        assert item.heading_mismatch is not None


class TestManuscriptClaimHygiene:
    def test_person_subject_does_not_ground_bible_book_p921(self) -> None:
        from converter.wikidata.marc_subject_resolve import (
            canonical_reference_grounded_in_subjects,
        )

        record = {
            "subjects": [
                {"term": "פרנקו, שמואל", "type": "person"},
                {"term": "Jewish funeral sermons", "type": "topic"},
            ],
        }
        assert not canonical_reference_grounded_in_subjects(
            record, {"hierarchy": "Bible", "book": "Samuel"},
        )

    def test_compound_temporary_catalog_note_is_placeholder(self) -> None:
        from converter.wikidata.catalog_notes import is_catalog_note_placeholder

        assert is_catalog_note_placeholder("רשומה זמנית | נושא נוסף: כתב-יד. מכירה")


class TestPatronymicFatherIdWithDates:
    def test_yehoyakhin_strips_medieval_father_mazal(self) -> None:
        """Person_91: 19th-c. label must not keep מרדכי 1290–1355 identity."""
        from app.pipeline.hmo_canonical_wikidata import _suppress_unconfirmed_person_identity
        from converter.wikidata.item_models import WikidataItem, WikidataStatement

        item = WikidataItem(
            local_id="QDraft_Person_91",
            entity_type="person",
            labels={"he": "יהויכין בן מרדכי"},
            statements=[
                WikidataStatement(
                    property_id="P8189",
                    value="987007415787905171",
                    value_type="external-id",
                ),
                WikidataStatement(
                    property_id="P569",
                    value="+1290-00-00T00:00:00Z",
                    value_type="time",
                ),
                WikidataStatement(
                    property_id="P570",
                    value="+1355-00-00T00:00:00Z",
                    value_type="time",
                ),
            ],
            authority_evidence=[{
                "preferred_name_heb": "מרדכי בן יהושע",
                "preferred_name_lat": "Mordechai ben Joshua",
            }],
        )
        assert _suppress_unconfirmed_person_identity([item])
        assert not any(
            s.property_id in {"P8189", "P569", "P570"} for s in item.statements
        )
        assert item.heading_mismatch is not None

    def test_patronymic_without_dates_does_not_strip_passer(self) -> None:
        from app.pipeline.hmo_canonical_wikidata import _suppress_unconfirmed_person_identity
        from converter.wikidata.item_models import WikidataItem, WikidataStatement

        item = WikidataItem(
            local_id="QDraft_Person_59",
            entity_type="person",
            labels={"he": "יהודה בן יצחק דדיניאה"},
            statements=[
                WikidataStatement(
                    property_id="P8189",
                    value="1",
                    value_type="external-id",
                ),
            ],
            authority_evidence=[{"preferred_name_heb": "יצחק בן יהודה"}],
        )
        assert _suppress_unconfirmed_person_identity([item]) == []
        assert any(s.property_id == "P8189" for s in item.statements)


class TestSubsetVerifyLocalCatalog:
    def test_attach_resolves_locals_from_catalog(self) -> None:
        from app.pipeline.wikidata_verdict_cache import attach_local_reference_targets

        ms = {
            "local_id": "QDraft_MS_1",
            "entity_type": "manuscript",
            "statements": [
                {"property_id": "P1574", "value": "__LOCAL:QDraft_Work_1"},
            ],
        }
        work = {
            "local_id": "QDraft_Work_1",
            "entity_type": "work",
            "labels": {"he": "נר ה"},
        }
        attach_local_reference_targets([ms], catalog=[ms, work])
        assert "QDraft_Work_1" in (ms.get("local_reference_targets") or {})
        assert ms["statements"][0]["value_label"] == "נר ה"


class TestP1559LabelEvidence:
    def test_p1559_matching_he_label_is_supported_without_authority(self) -> None:
        from app.pipeline.wikidata_verify_evidence import build_claim_sources

        sources = build_claim_sources(
            {
                "entity_type": "person",
                "labels": {"he": "מאוריציו"},
                "statements": [
                    {"property_id": "P1559", "value": "מאוריציו"},
                    {"property_id": "P31", "value": "Q5"},
                ],
                "authority_evidence": [],
            },
            {},
            [],
        )
        row = sources["P1559"]
        assert row["supported"] is True
        assert "labels.he" in row["channels"]


class TestWeakEditorDescription:
    def test_editor_without_dates_uses_generic_description(self) -> None:
        from converter.wikidata.item_builder import _build_person_description

        assert _build_person_description("editor", "", False) == (
            "person associated with Hebrew manuscripts"
        )
        assert _build_person_description("editor", "1200-1280", False) == (
            "editor (1200-1280)"
        )


class TestP1559AlignAfterMerge:
    def test_align_forces_p1559_to_he_label(self) -> None:
        from app.pipeline.hmo_canonical_wikidata import _align_person_p1559_to_hebrew_label
        from converter.wikidata.item_models import WikidataItem, WikidataStatement

        item = WikidataItem(
            local_id="QDraft_Person_66",
            entity_type="person",
            labels={"he": "סעדיה בן שלמה דמרמרי"},
            statements=[
                WikidataStatement(
                    property_id="P1559",
                    value="שלמה בן סעדיה אלפקעה",
                    value_type="monolingualtext",
                    language="he",
                ),
            ],
        )
        _align_person_p1559_to_hebrew_label([item])
        p1559 = [s for s in item.statements if s.property_id == "P1559"]
        assert len(p1559) == 1
        assert p1559[0].value == "סעדיה בן שלמה דמרמרי"


class TestP1559MatchesPublicLabel:
    def test_p1559_equals_he_label(self) -> None:
        items = WikidataItemBuilder().build_all([{
            "_control_number": "990001404380205171",
            "title": "ספר",
            "shelfmark": "F 1",
            "contributors": [{"name": "סעדיה בן שלמה אלקיסי", "role": "מעתיק", "field": "700"}],
            "marc_authority_matches": [{
                "name": "סעדיה בן שלמה אלקיסי",
                "entity_text": "סעדיה בן שלמה אלקיסי",
                "role": "מעתיק",
                "mazal_id": "987007507328605171",
                "preferred_name_heb": "טויל, סעדיה בן שלמה",
                "approved": True,
            }],
        }])
        person = next(i for i in items if i.entity_type == "person")
        p1559 = _statements(person, "P1559")
        assert p1559
        assert p1559[0].value == person.labels.get("he")


class TestCanonicalLanguageAndDate:
    def test_german_record_label_is_not_hebrew(self) -> None:
        from converter.wikidata.item_builder import manuscript_record_label

        label = manuscript_record_label(
            "997008371275105171",
            {"languages": ["ger", "heb"]},
        )
        assert label.startswith("German manuscript")
        assert "Hebrew" not in label

    def test_cambridge_holder_is_named_not_nli_record(self) -> None:
        from converter.wikidata.item_builder import manuscript_record_label

        label = manuscript_record_label(
            "990001400870205171",
            {
                "languages": ["lat", "heb"],
                "contributors": [{
                    "name": "Cambridge University Library",
                    "role": "current owner",
                    "field": "710",
                }],
            },
        )
        assert "Cambridge University Library" in label
        assert "NLI record" not in label
        assert label.startswith("Latin manuscript")

    def test_hebrew_century_becomes_english_in_en_description(self) -> None:
        item = WikidataItemBuilder().build_manuscript_item({
            "_control_number": "CENTURY-HEB",
            "title": "כתב יד",
            "dates": {
                "year": 1001,
                "original_string": 'מאה י"א',
            },
        })
        assert "1001" not in item.descriptions["en"]
        assert "מאה" not in item.descriptions["en"]
        assert "11th century" in item.descriptions["en"]

    def test_hebrew_description_omits_century_midpoint(self) -> None:
        from app.pipeline.hmo_canonical_wikidata import _hebrew_manuscript_description

        text = _hebrew_manuscript_description({
            "languages": ["heb"],
            "dates": {"year": 1501, "original_string": 'מאה ט"ז-י"ז'},
            "shelfmark": "F 1",
        })
        assert "1501" not in text


class TestExport29RolePlaceTitle:
    def test_paren_scribe_role_emits_p11603(self) -> None:
        items = WikidataItemBuilder().build_all([{
            "_control_number": "990001827870205171",
            "title": "ספר",
            "shelfmark": "F 32325",
            "marc_authority_matches": [{
                "name": "אלעדוי, יוסף בן עמרם בן עודד",
                "role": "(מעתיק)",
                "mazal_id": "987007402783005171",
                "preferred_name_heb": "אלעדוי, יוסף בן עמרם בן עודד",
                "approved": True,
            }],
        }])
        ms = next(i for i in items if i.entity_type == "manuscript")
        assert _statements(ms, "P11603")

    def test_amran_related_place_is_city_not_governorate(self) -> None:
        item = WikidataItemBuilder().build_manuscript_item({
            "_control_number": "AMRAN",
            "title": "ספר",
            "shelfmark": "F 1",
            "place": "",
            "related_places": ["ʻAmrān (Yemen)"],
            "kima_places": {
                "ʻAmrān (Yemen)": "https://www.wikidata.org/entity/Q275720",
            },
        })
        assert _statements(item, "P1071") == []
        assert any(s.value == "Q48200" for s in _statements(item, "P7153"))

    def test_subject_places_are_not_creation_sites(self) -> None:
        item = WikidataItemBuilder().build_manuscript_item({
            "_control_number": "EGYPT-751",
            "title": "ספר",
            "shelfmark": "F 1",
            "place": "",
            "kima_places": {
                "Egypt": "https://www.wikidata.org/entity/Q79",
                "Heraklion": "https://www.wikidata.org/entity/Q160544",
            },
        })
        assert _statements(item, "P1071") == []

    def test_work_title_p1476_becomes_shelfmark(self) -> None:
        items = WikidataItemBuilder().build_all([{
            "_control_number": "990001875220205171",
            "title": "שלחן ערוך (ארח חיים)",
            "shelfmark": "F 39766",
            "related_works": [{"title": "שלחן ערוך (ארח חיים)", "approved": True}],
        }])
        ms = next(i for i in items if i.entity_type == "manuscript")
        assert [s.value for s in _statements(ms, "P1476")] == ["F 39766"]

    def test_known_work_title_alone_uses_shelfmark(self) -> None:
        from converter.wikidata.property_mapping import known_work_qid_for_title

        assert known_work_qid_for_title("שלחן ערוך (ארח חיים)") is None
        from converter.wikidata.property_mapping import is_known_work_edition_title

        assert is_known_work_edition_title("שלחן ערוך (ארח חיים)")
        assert is_known_work_edition_title("משנה תורה לרמבם")
        assert not is_known_work_edition_title("תורה שבעל פה")
        item = WikidataItemBuilder().build_manuscript_item({
            "_control_number": "SHULCHAN",
            "title": "שלחן ערוך (ארח חיים)",
            "shelfmark": "F 39766",
        })
        assert [s.value for s in _statements(item, "P1476")] == ["F 39766"]


class TestOrphanSignificantPersonDrop:
    def test_bare_public_qid_without_build_person_is_dropped(self) -> None:
        from app.pipeline.wikidata_local_refs import drop_orphan_significant_person_claims
        from converter.wikidata.item_models import WikidataItem, WikidataStatement

        ms = WikidataItem(
            local_id="QDraft_MS_1",
            entity_type="manuscript",
            statements=[
                WikidataStatement(
                    property_id="P3342",
                    value="Q86007560",
                    value_type="item",
                ),
                WikidataStatement(
                    property_id="P3342",
                    value="__LOCAL:mazal:1",
                    value_type="item",
                ),
            ],
        )
        person = WikidataItem(
            local_id="QDraft_Person_1",
            entity_type="person",
            existing_qid="Q123",
        )
        assert drop_orphan_significant_person_claims([ms, person]) == 1
        values = [s.value for s in ms.statements if s.property_id == "P3342"]
        assert values == ["__LOCAL:mazal:1"]
