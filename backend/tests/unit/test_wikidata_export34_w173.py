"""Export-34 / Rule W-173 regressions — synthetic, no CN allowlists."""

from __future__ import annotations

from converter.wikidata.catalog_notes import is_incipit_text
from converter.wikidata.item_builder import WikidataItemBuilder
from converter.wikidata.property_labels import QID_LABELS
from converter.wikidata.work_link_specificity import Q_BIBLE, refine_exemplar_work_qid


class TestRelatedWorksBibleLadder:
    def test_piyyut_related_bible_is_dropped(self) -> None:
        record = {
            "_control_number": "990000827290205171",
            "control_number": "990000827290205171",
            "title": "פיוטים ושירים",
            "shelfmark": "F 47961",
            "holding_institution": "Russian State Library",
            "genres": ["Piyyutim", "Poetry"],
            "related_works": [{"title": "Bible", "source_field": "RELATED_WORKS"}],
            "extent": "210 דף",
        }
        assert refine_exemplar_work_qid(
            Q_BIBLE, title="Bible", record=record,
        ) is None
        items = WikidataItemBuilder().build_all([record])
        manuscripts = [i for i in items if i.entity_type == "manuscript"]
        assert manuscripts
        exemplars = [
            s.value for s in manuscripts[0].statements if s.property_id == "P1574"
        ]
        assert Q_BIBLE not in exemplars


class TestIncipitChronologyGate:
    def test_year_folio_note_is_not_incipit(self) -> None:
        assert not is_incipit_text(
            'בשנת תכ"א. בעמוד 542 דף השלמה ובו לוח מתחיל בשנת תרמ"ד.'
        )

    def test_literary_first_line_still_ok(self) -> None:
        assert is_incipit_text("בראשית ברא אלהים את השמים")


class TestCambridgeHolderLabel:
    def test_cambridge_qid_has_static_label(self) -> None:
        assert QID_LABELS.get("Q1028334") == "Cambridge University Library"


class TestHebrewPreferredP8189Strip:
    def test_mismatched_hebrew_preferred_strips_p8189(self) -> None:
        from app.pipeline.hmo_canonical_wikidata import (
            _hebrew_preferred_heading_mismatch,
            _suppress_unconfirmed_person_identity,
        )
        from converter.wikidata.item_models import WikidataItem, WikidataStatement

        item = WikidataItem(
            local_id="QDraft_Person_147",
            entity_type="person",
            labels={"he": "סלימן בן סאלם"},
            statements=[
                WikidataStatement(property_id="P31", value="Q5", value_type="item"),
                WikidataStatement(
                    property_id="P8189",
                    value="987007451406105171",
                    value_type="external-id",
                ),
            ],
            authority_evidence=[{
                "mazal_id": "987007451406105171",
                "preferred_name_heb": "עלי בן סולימאן",
                "preferred_name_lat": "Ali ben Suleiman",
            }],
        )
        assert _hebrew_preferred_heading_mismatch(
            item, ["עלי בן סולימאן", "Ali ben Suleiman"],
        )
        suppressed = _suppress_unconfirmed_person_identity([item])
        assert item.local_id in suppressed
        assert not any(s.property_id == "P8189" for s in item.statements)
