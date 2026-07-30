"""Rule W-138 — MARC unwrapping, claim provenance, work/person identity."""

from __future__ import annotations

import pytest

from app.pipeline.marc_ingest import _unwrap_marc_value, prepare_record_for_pipeline
from app.pipeline.wikidata_canonical_enrichment import (
    canonical_work_titles,
    merge_legacy_into_canonical,
)
from app.pipeline.wikidata_export_quality_gate import assert_wikidata_export_quality
from app.pipeline.wikidata_local_refs import (
    dangling_local_references,
    resolve_local_references,
)
from app.pipeline.wikidata_verify_evidence import build_claim_sources
from converter.transformer.field_handlers import FieldHandlers
from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.marc_subject_resolve import is_too_generic_subject

CN = "990000592310205171"


class TestMarcValueUnwrapping:
    def test_surrounding_quotes_are_removed(self) -> None:
        assert _unwrap_marc_value('"heb"') == "heb"
        assert _unwrap_marc_value('"9.5X14.5 cm"') == "9.5X14.5 cm"

    def test_internal_gershayim_survives(self) -> None:
        assert _unwrap_marc_value('"שד"ל"') == 'שד"ל'
        assert _unwrap_marc_value("שד\"ל") == 'שד"ל'

    def test_empty_quoted_value_becomes_empty(self) -> None:
        assert _unwrap_marc_value('""') == ""

    def test_language_codes_survive_the_handler(self) -> None:
        """The 041 handler chunks codes in 3s; quotes shifted every offset."""
        record = prepare_record_for_pipeline({"_control_number": CN, "041$a": '"heb"'})
        assert record["languages"] == ["heb"]

    def test_multi_valued_language_is_split_after_unwrapping(self) -> None:
        record = prepare_record_for_pipeline({"_control_number": CN, "041$a": '"heb|lad"'})
        assert record["languages"] == ["heb", "lad"]

    def test_shelfmark_and_title_are_unwrapped(self) -> None:
        record = prepare_record_for_pipeline({
            "_control_number": CN, "852$j": '"F 7956"', "245$a": '"כתב יד"',
        })
        assert record["shelfmark"] == "F 7956"
        assert record["title"] == "כתב יד"

    def test_empty_rda_terms_are_dropped(self) -> None:
        record = prepare_record_for_pipeline({
            "_control_number": CN, "336$a": '""', "337$a": '""',
        })
        assert not record.get("content_types")
        assert not record.get("media_types")


class TestDimensionParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("9.5X14.5 cm", {"height_mm": 95, "width_mm": 145}),
            ("24 x 17 cm", {"height_mm": 240, "width_mm": 170}),
            ("280 x 200 mm", {"height_mm": 280, "width_mm": 200}),
            ("28 cm", {"height_mm": 280}),
            ("9,5X14,5 cm", {"height_mm": 95, "width_mm": 145}),
            ("39 leaves ; 9.5X14.5 cm", {"height_mm": 95, "width_mm": 145}),
        ],
    )
    def test_dimensions_are_parsed_not_guessed(
        self, text: str, expected: dict[str, int],
    ) -> None:
        assert FieldHandlers._parse_dimensions(text) == expected

    def test_decimal_centimetres_no_longer_read_as_a_single_digit(self) -> None:
        """`9.5X14.5 cm` previously yielded height_mm=50 (from "5 cm")."""
        assert FieldHandlers._parse_dimensions("9.5X14.5 cm")["height_mm"] == 95


class TestChannelAwareProvenance:
    def _person(self, **extra: object) -> dict[str, object]:
        item = {
            "entity_type": "person",
            "statements": [
                {"property_id": "P31", "value": "Q5"},
                {"property_id": "P214", "value": "12345"},
                {"property_id": "P569", "value": "+1555-00-00T00:00:00Z"},
                {"property_id": "P2888", "value": "https://mhm-hmo.wikibase.cloud/wiki/Item:Q9"},
            ],
        }
        item.update(extra)
        return item

    def test_authority_backed_claims_cite_their_channel(self) -> None:
        sources = build_claim_sources(
            self._person(authority_evidence=[{"kind": "viaf", "viaf_id": "12345", "birth_year": 1555}]),
            {},
            [],
        )
        assert sources["P214"]["supported"] is True
        assert "authority.viaf" in sources["P214"]["channels"]
        assert sources["P569"]["supported"] is True

    def test_identifier_from_the_hmo_item_is_cited_as_such(self) -> None:
        sources = build_claim_sources(
            self._person(hmo_wikibase_id="Q9", authority_evidence=[]), {}, [],
        )
        assert sources["P214"]["supported"] is True
        assert "hmo_wikibase" in sources["P214"]["channels"]

    def test_bridge_claims_are_supported_by_the_wiki_item(self) -> None:
        sources = build_claim_sources(self._person(hmo_wikibase_id="Q9"), {}, [])
        assert sources["P2888"]["supported"] is True

    def test_structural_claims_need_no_source_field(self) -> None:
        sources = build_claim_sources(self._person(), {}, [])
        assert sources["P31"]["structural"] is True
        assert sources["P31"]["supported"] is True

    def test_every_emitted_pid_gets_a_row(self) -> None:
        item = self._person()
        pids = {str(s["property_id"]) for s in item["statements"]}
        assert set(build_claim_sources(item, {}, [])) == pids


class TestWorkTitleIdentity:
    def _work(self, titles: list[str], label: str) -> WikidataItem:
        return WikidataItem(
            labels={"he": label},
            entity_type="work",
            local_id="work:1",
            statements=[
                WikidataStatement(property_id="P1476", value=t, value_type="string")
                for t in titles
            ],
        )

    def test_quote_wrapped_duplicate_is_dropped(self) -> None:
        work = self._work(
            ['"סדור מנהג קרפנטרץ לראש השנה."', "סדור מנהג קרפנטרץ לראש השנה"],
            "סדור מנהג קרפנטרץ לראש השנה",
        )
        assert len(canonical_work_titles(work)) == 1

    def test_foreign_title_is_dropped(self) -> None:
        work = self._work(
            ["סדור מנהג קרפנטרץ לראש השנה", "סדר אליהו זוטא"],
            "סדור מנהג קרפנטרץ לראש השנה",
        )
        kept = canonical_work_titles(work)
        assert [s.value for s in kept] == ["סדור מנהג קרפנטרץ לראש השנה"]

    def test_works_are_not_merged_on_a_title_key(self) -> None:
        canonical = WikidataItem(
            labels={"he": "גורלות החול"}, entity_type="work", local_id="work:a",
        )
        other = WikidataItem(
            labels={"he": "סדר אליהו זוטא"},
            entity_type="work",
            local_id="work:b",
            statements=[
                WikidataStatement(
                    property_id="P1476", value="גורלות החול", value_type="string",
                ),
            ],
        )
        merged = merge_legacy_into_canonical([canonical], [other])
        by_id = {item.local_id: item for item in merged}
        assert [s.value for s in by_id["work:a"].statements] == []

    def test_multiple_titles_block_the_build(self) -> None:
        work = self._work(["title one", "title two"], "unrelated label")
        work.statements.append(
            WikidataStatement(property_id="P1476", value="title three", value_type="string"),
        )
        with pytest.raises(ValueError, match="WORK_MULTIPLE_TITLES"):
            assert_wikidata_export_quality([work])


class TestLocalReferenceResolution:
    def _corpus(self) -> tuple[WikidataItem, WikidataItem]:
        manuscript = WikidataItem(
            labels={"en": "Jerusalem, NLI, F 1"},
            entity_type="manuscript",
            local_id="ms1",
            statements=[
                WikidataStatement(
                    property_id="P1574", value="__LOCAL:work:מבוא_שערים", value_type="item",
                ),
                WikidataStatement(
                    property_id="P1574", value="__LOCAL:work:kept", value_type="item",
                ),
                WikidataStatement(
                    property_id="P50", value="__LOCAL:person:gone", value_type="item",
                ),
            ],
        )
        work = WikidataItem(labels={"he": "מחזור"}, entity_type="work", local_id="work:kept")
        return manuscript, work

    def test_missing_work_target_degrades_to_unknown_text(self) -> None:
        manuscript, work = self._corpus()
        stats = resolve_local_references([manuscript, work])
        exemplars = [s for s in manuscript.statements if s.property_id == "P1574"]
        assert stats["degraded"] == 1
        assert "Q234460" in {str(s.value) for s in exemplars}
        degraded = next(s for s in exemplars if s.value == "Q234460")
        assert any(
            q.get("property") == "P1932" and "מבוא" in str(q.get("value"))
            for q in degraded.qualifiers
        )

    def test_non_degradable_claim_is_dropped(self) -> None:
        manuscript, work = self._corpus()
        stats = resolve_local_references([manuscript, work])
        assert stats["dropped"] == 1
        assert "P50" not in {s.property_id for s in manuscript.statements}

    def test_resolved_corpus_has_no_dangling_references(self) -> None:
        manuscript, work = self._corpus()
        resolve_local_references([manuscript, work])
        assert dangling_local_references([manuscript, work]) == []

    def test_dangling_reference_blocks_the_build(self) -> None:
        manuscript, work = self._corpus()
        with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
            assert_wikidata_export_quality([manuscript, work])


class TestGenericSubjects:
    @pytest.mark.parametrize("term", ["Jews", "jews", "Judaism", "יהודים", "Manuscripts"])
    def test_corpus_wide_headings_are_not_a_main_subject(self, term: str) -> None:
        assert is_too_generic_subject(term)

    @pytest.mark.parametrize("term", ["Jewish law", "Kabbalah", "Astronomy", "קבלה"])
    def test_specific_headings_still_resolve(self, term: str) -> None:
        assert not is_too_generic_subject(term)


class TestVerifiedHolders:
    def test_only_unambiguous_holders_resolve(self) -> None:
        import converter.wikidata.item_builder  # noqa: F401  (import order)
        from converter.wikidata.manuscript_projection import (
            _current_holder_names,
            _current_holder_qid,
        )

        def qid_for(name: str) -> str | None:
            record = {"contributors": [{"name": name, "role": "current owner"}]}
            return _current_holder_qid(record, _current_holder_names(record))

        assert qid_for("The British Library") == "Q23308"
        assert qid_for("The Bodleian Libraries, University of Oxford") == "Q82133"
        assert qid_for("The National Library of Israel") == "Q188915"
        # Two plausible Wikidata entities — abstain rather than guess.
        assert qid_for("The Ben Zvi Institute") is None


class TestPersonDateGating:
    def _entity(self, label: str) -> object:
        from app.pipeline.hmo_canonical import CanonicalHmoEntity

        return CanonicalHmoEntity(
            local_id="QDraft_Person_1",
            source_uri="https://w3id.org/mhm/ontology#Person_1",
            wikibase_id="Q9",
            revision_id=1,
            labels={"he": label},
            descriptions={},
            aliases={},
            claims=[],
            authority_evidence=[],
            source_fingerprint="fp",
            entity_type="E21_Person",
            control_numbers=[CN],
        )

    def _context(self, match: dict[str, object]) -> object:
        from app.pipeline.hmo_canonical_wikidata import canonical_studio_context

        return canonical_studio_context(approved_matches=[match])

    def test_name_only_match_never_supplies_dates(self) -> None:
        """A homonym gave a 1642 scribe the dates (1786-1874)."""
        from app.pipeline.hmo_canonical_wikidata import _upload_descriptions

        context = self._context({
            "entity_text": "שמואל בן אברהם מונדולפו",
            "role": "scribe",
            "payload": {"birth_year": 1786, "death_year": 1874},
        })
        out = _upload_descriptions(
            self._entity("שמואל בן אברהם מונדולפו"), "person", context=context,
        )
        assert "1786" not in out["en"]
        assert out["en"] == "Hebrew manuscript scribe"

    def test_identifier_backed_match_keeps_its_dates(self) -> None:
        from app.pipeline.hmo_canonical import CanonicalHmoEntity
        from app.pipeline.hmo_canonical_wikidata import _upload_descriptions

        entity = CanonicalHmoEntity(
            local_id="QDraft_Person_2",
            source_uri="https://w3id.org/mhm/ontology#Person_2",
            wikibase_id="Q9",
            revision_id=1,
            labels={"he": "אברהם מונסון"},
            descriptions={},
            aliases={},
            claims=[],
            authority_evidence=[
                {"kind": "viaf", "accepted": True, "identifier": "12345"},
            ],
            source_fingerprint="fp",
            entity_type="E21_Person",
            control_numbers=[CN],
        )
        context = self._context({
            "entity_text": "אברהם מונסון",
            "viaf_id": "12345",
            "role": "author",
            "payload": {"birth_year": 1555, "death_year": 1619},
        })
        out = _upload_descriptions(entity, "person", context=context)
        assert out["en"] == "author (1555-1619)"


class TestResidualDefects:
    """Rule W-138 follow-up — export (15) residuals."""

    def test_every_projected_pid_has_a_provenance_mapping(self) -> None:
        """P1922 / P655 / P9046 / P5816 reached the judge unmapped."""
        item = {
            "entity_type": "manuscript",
            "statements": [
                {"property_id": pid, "value": "x"}
                for pid in ("P1922", "P655", "P9046", "P5816")
            ],
        }
        sources = build_claim_sources(
            item, {"notes": "F. 1a: incipit", "authors": "ויטל", "material": "parchment"}, [],
        )
        assert all(row["supported"] for row in sources.values())

    def test_unmapped_pid_is_labelled_unmapped_not_structural(self) -> None:
        sources = build_claim_sources(
            {"statements": [{"property_id": "P9999", "value": "x"}]}, {}, [],
        )
        assert sources["P9999"]["channels"] == ["unmapped"]
        assert sources["P9999"]["supported"] is False

    def test_legacy_work_description_names_the_attesting_manuscript(self) -> None:
        import asyncio

        from app.pipeline.wikidata_studio import build_items_for_run

        result = asyncio.run(build_items_for_run(
            marc_records=[{
                "_control_number": '"990000464110205171"',
                "245$a": '"כתב יד"',
                "852$j": '"F 12345"',
                "500$a": '"כולל: ענף שני מפרי עץ החיים"',
            }],
            approved_matches=[],
        ))
        works = [i for i in result["items"] if i["entity_type"] == "work"]
        assert works
        for work in works:
            description = work["descriptions"]["en"]
            assert description != "Work preserved in a Hebrew manuscript"
            # The control number is verifiable from the item's own record_ids /
            # P3959; a shelfmark is not part of a work's evidence pack.
            assert "990000464110205171" in description


class TestExport16Residuals:
    """Rule W-138 follow-up — regressions my own W-138 changes introduced."""

    def test_shelfmark_slice_holds_the_shelfmark_not_rights_text(self) -> None:
        """952$a-d are rights/audit fields in this corpus; 090$a is the shelfmark."""
        from app.pipeline.marc_verify_context import (
            index_marc_records,
            marc_context_for_item,
        )

        record = {
            "_control_number": CN,
            "090$a": '"F 3238"',
            "952$a": '"Public domain; Contract"',
            "952$c": '"Noam Solan by Ruben Wengiel 20220613"',
        }
        slice_ = marc_context_for_item(
            {"control_numbers": [CN]}, index_marc_records([record]),
        )
        assert "F 3238" in slice_["shelfmark"]
        assert "Public domain" not in slice_["shelfmark"]
        assert "Public domain" in slice_["rights"]

    def test_work_title_provenance_is_entity_aware(self) -> None:
        """A 505-derived work is not evidenced by the manuscript's 245."""
        marc = {"title": "גלא עמיקתא", "contents": "505$a: מחזור"}
        main = {
            "entity_type": "work",
            "statements": [{"property_id": "P1476", "value": "גלא עמיקתא"}],
            "work_candidate_evidence": [{"source_field": "100/245", "accepted": True}],
        }
        derived = {
            "entity_type": "work",
            "statements": [{"property_id": "P1476", "value": "מחזור"}],
            "work_candidate_evidence": [{"source_field": "505", "accepted": True}],
        }
        assert "marc.title" in build_claim_sources(main, marc, [])["P1476"]["channels"]
        derived_row = build_claim_sources(derived, marc, [])["P1476"]
        assert "marc.title" not in derived_row["channels"]
        assert "work_candidate_evidence" in derived_row["channels"]

    def test_work_attestation_cites_a_verifiable_control_number(self) -> None:
        from converter.wikidata.work_projection import _work_description_with_attestation

        description = _work_description_with_attestation(
            author_name=None, century=None,
            source_record={"_control_number": CN, "shelfmark": "Ms. Heb. 197/11=4"},
        )
        assert f"attested in NLI record {CN}" in description
        assert "MS Ms." not in description

    def test_known_work_qid_is_carried_from_the_accepting_evidence(self) -> None:
        """Accepted via `known_wikidata_work` must not mint a local CREATE."""
        from app.pipeline.hmo_canonical_wikidata import _known_qid_from_work_evidence

        assert _known_qid_from_work_evidence([
            {"accepted": True, "title": "הגדה של פסח", "raw_title": "הגדה של פסח."},
        ]) == "Q623354"
        assert _known_qid_from_work_evidence([{"accepted": False, "title": "הגדה של פסח"}]) == ""

    def test_title_claims_are_sanitised_like_labels(self) -> None:
        from app.pipeline.hmo_canonical_wikidata import _claim_value_for

        assert _claim_value_for(
            "P1476", '"הגדה של פסח :" "מנהג אשכנז."',
        ) == "הגדה של פסח : מנהג אשכנז"
        assert _claim_value_for("P31", "Q47461344") == "Q47461344"
