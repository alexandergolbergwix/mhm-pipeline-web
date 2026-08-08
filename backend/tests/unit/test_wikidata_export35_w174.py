"""Export-35 / Rule W-174 regressions — synthetic, no CN allowlists."""

from __future__ import annotations

from converter.wikidata.item_builder import WikidataItemBuilder, _normalise_label
from converter.wikidata.manuscript_projection import catalogue_url_agrees_with_shelfmark
from converter.wikidata.property_labels import qid_label


class TestCatalogueP973ShelfmarkGate:
    def test_mismatched_bl_ref_is_rejected(self) -> None:
        url = "http://www.bl.uk/manuscripts/FullDisplay.aspx?ref=Or_12354"
        assert not catalogue_url_agrees_with_shelfmark(url, "F 8298")

    def test_matching_ref_is_kept(self) -> None:
        url = "http://www.bl.uk/manuscripts/FullDisplay.aspx?ref=Or_12354"
        assert catalogue_url_agrees_with_shelfmark(url, "Or 12354")

    def test_builder_drops_mismatched_digital_url(self) -> None:
        item = WikidataItemBuilder().build_manuscript_item({
            "_control_number": "990000592310205171",
            "title": "גלא עמיקתא",
            "shelfmark": "F 8298",
            "holding_institution": "British Library",
            "digital_url": (
                "http://www.bl.uk/manuscripts/FullDisplay.aspx?ref=Or_12354"
            ),
            "extent": "245 דף",
        })
        p973 = [
            str(s.value) for s in item.statements if s.property_id == "P973"
        ]
        assert not any("Or_12354" in u for u in p973)


class TestHebrewBracketExpansion:
    def test_restored_letters_expand_not_space(self) -> None:
        assert _normalise_label("מע[וצ']ה") == "מעוצ'ה"
        assert "מע ה" not in _normalise_label("חבני, גיילה יחיא בן מע[וצ']ה")


class TestAuditedHolderLabels:
    def test_leeds_qid_glosses_via_holding_table(self) -> None:
        assert qid_label("Q24568958") == "University of Leeds Libraries"

    def test_enrich_snak_stamps_holding_label(self) -> None:
        from app.pipeline.wikidata_studio import _enrich_snak

        snak = {"property_id": "P195", "value": "Q24568958", "value_type": "item"}
        _enrich_snak(snak)
        assert snak.get("value_label") == "University of Leeds Libraries"


class TestWorkTitleAliasStrip:
    def test_elaboration_of_contained_work_stripped(self) -> None:
        item = WikidataItemBuilder().build_manuscript_item({
            "_control_number": "990001205840205171",
            "title": "תורה",
            "shelfmark": "F 12362",
            "holding_institution": "British Library",
            "variant_titles": ['פרוש התורה לרש"י'],
            "related_works": [{"title": 'פרוש רש"י', "approved": True}],
            "work_candidate_evidence": [
                {"title": 'פרוש רש"י', "accepted": True, "source_field": "500"},
            ],
            "extent": "10 דף",
        })
        he_aliases = [a.casefold() for a in (item.aliases.get("he") or [])]
        assert 'פרוש התורה לרש"י'.casefold() not in he_aliases
