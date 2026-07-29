"""Rule W-137 — evidence completeness, manuscript identity, claim hygiene."""

from __future__ import annotations

import pytest

from app.pipeline.hmo_canonical import CanonicalHmoEntity
from app.pipeline.hmo_canonical_wikidata import (
    identity_control_number,
    identity_records_for,
)
from app.pipeline.marc_verify_context import (
    index_marc_records,
    marc_context_for_item,
    primary_control_number_for,
    project_marc_slice,
    raw_tag_slice,
)
from app.pipeline.wikidata_canonical_enrichment import (
    dedupe_statements,
    merge_legacy_into_canonical,
)
from app.pipeline.wikidata_export_quality_gate import assert_wikidata_export_quality
from app.pipeline.wikidata_verify_evidence import (
    build_claim_sources,
    build_statement_value_labels,
    enrich_items_with_verify_evidence,
)
from converter.wikidata.item_models import WikidataItem, WikidataStatement

CN_A = "990000592310205171"
CN_B = "990001238980205171"


def _collapsed_record(cn: str = CN_A) -> dict[str, object]:
    """A TSV/collapsed-key record: raw MARC tags, few normalised keys."""
    return {
        "_control_number": cn,
        "title": "גלא עמיקתא",
        "authors": [{"name": "ויטל, חיים בן יוסף", "field": "100"}],
        "008": "1651",
        "300$a": "39 leaves",
        "500$a": "F. 96b: a short list of allusions",
        "540$a": "Public domain",
        "852$j": "F 7956",
        "710$a": "The British Library",
    }


class TestRawTagEvidenceSlice:
    def test_raw_tags_fill_slices_the_normalised_keys_cannot(self) -> None:
        slice_ = project_marc_slice(_collapsed_record(), ["title", "authors", "dates"])
        assert slice_["dates"] == "008: 1651"
        assert "300$a: 39 leaves" in slice_["extent"]
        assert "852$j: F 7956" in slice_["shelfmark"]
        assert "540$a: Public domain" in slice_["rights"]
        assert "710$a: The British Library" in slice_["contributors"]

    def test_normalised_values_win_over_raw_tags(self) -> None:
        record = {**_collapsed_record(), "extent": "12 folios"}
        slice_ = project_marc_slice(record, ["extent"])
        assert slice_["extent"] == "12 folios"

    def test_raw_tags_can_be_disabled(self) -> None:
        slice_ = project_marc_slice(
            _collapsed_record(), ["title"], include_raw_tags=False,
        )
        assert set(slice_) == {"title"}

    def test_empty_record_yields_no_slices(self) -> None:
        assert raw_tag_slice({"_control_number": CN_A}) == {}


class TestClaimSourceProvenance:
    def test_each_claim_gets_its_own_marc_evidence(self) -> None:
        item = {
            "_local_id": "QDraft_MS_" + CN_A,
            "entity_type": "manuscript",
            "records": [CN_A],
            "statements": [
                {"property_id": "P571", "value": "+1651-00-00T00:00:00Z"},
                {"property_id": "P1104", "value": 39},
                {"property_id": "P217", "value": "F 7956"},
                {"property_id": "P6216", "value": "Q19652"},
            ],
        }
        marc = marc_context_for_item(
            {"control_numbers": [CN_A]}, index_marc_records([_collapsed_record()]),
        )
        sources = build_claim_sources(item, marc, [CN_A])
        assert sources["P571"]["supported"] is True
        assert "1651" in sources["P571"]["evidence"]["dates"]
        assert sources["P1104"]["supported"] is True
        assert sources["P217"]["supported"] is True
        assert sources["P6216"]["supported"] is True

    def test_unsupported_claim_is_reported_as_such(self) -> None:
        item = {
            "statements": [{"property_id": "P571", "value": "+1651-00-00T00:00:00Z"}],
        }
        sources = build_claim_sources(item, {"title": "x"}, [])
        assert sources["P571"]["supported"] is False
        assert sources["P571"]["evidence"] == {}

    def test_evidence_pack_carries_claim_sources_and_value_labels(self) -> None:
        items = [{
            "local_id": "QDraft_MS_" + CN_A,
            "_local_id": "QDraft_MS_" + CN_A,
            "entity_type": "manuscript",
            "records": [CN_A],
            "statements": [
                {"property_id": "P31", "value": "Q87167"},
                {"property_id": "P571", "value": "+1651-00-00T00:00:00Z"},
            ],
        }]
        enrich_items_with_verify_evidence(items, [_collapsed_record()])
        pack = items[0]["verify_evidence"]
        assert pack["claim_sources"]["P571"]["supported"] is True
        assert pack["value_labels"]["Q87167"] == "manuscript"
        assert pack["value_labels"]["P31"] == "instance of"

    def test_local_targets_resolve_a_value_label(self) -> None:
        item = {
            "statements": [{"property_id": "P1574", "value": "__LOCAL:work:1"}],
            "local_reference_targets": {"work:1": {"labels": {"he": "מחזור"}}},
        }
        assert build_statement_value_labels(item)["__LOCAL:work:1"] == "מחזור"


class TestManuscriptIdentityScoping:
    def test_primary_control_number_comes_from_the_entity_identity(self) -> None:
        assert primary_control_number_for(
            [CN_B, CN_A], f"https://w3id.org/mhm/ontology#MS_{CN_A}",
        ) == CN_A

    def test_manuscript_records_are_scoped_to_its_own_record(self) -> None:
        entity = CanonicalHmoEntity(
            local_id=f"QDraft_MS_{CN_A}",
            source_uri=f"https://w3id.org/mhm/ontology#MS_{CN_A}",
            wikibase_id="Q893",
            revision_id=1,
            labels={"he": "גלא עמיקתא"},
            descriptions={},
            aliases={},
            claims=[],
            authority_evidence=[],
            source_fingerprint="fp",
            entity_type="F4_Manifestation",
            control_numbers=[CN_A, CN_B],
        )
        assert identity_control_number(entity) == CN_A
        assert identity_records_for(entity, "manuscript") == [CN_A]
        assert identity_records_for(entity, "person") == [CN_A, CN_B]

    def test_legacy_join_never_borrows_a_linked_manuscript(self) -> None:
        canonical = WikidataItem(
            labels={"he": "גלא עמיקתא"},
            entity_type="manuscript",
            local_id=f"QDraft_MS_{CN_A}",
            records=[CN_A, CN_B],
            statements=[WikidataStatement(
                property_id="P3959", value=CN_A, value_type="external-id",
            )],
        )
        legacy_other = WikidataItem(
            labels={"en": "The British Library, F 8298"},
            entity_type="manuscript",
            local_id=f"marc:{CN_B}",
            records=[CN_B],
            statements=[WikidataStatement(
                property_id="P217", value="F 8298", value_type="string",
            )],
        )
        merged = merge_legacy_into_canonical([canonical], [legacy_other])
        assert len(merged) == 1
        assert merged[0].records == [CN_A]
        assert not [s for s in merged[0].statements if s.property_id == "P217"]
        assert merged[0].labels.get("en") != "The British Library, F 8298"

    def test_legacy_join_still_enriches_its_own_manuscript(self) -> None:
        canonical = WikidataItem(
            labels={"he": "גלא עמיקתא"},
            entity_type="manuscript",
            local_id=f"QDraft_MS_{CN_A}",
            records=[CN_A],
        )
        legacy = WikidataItem(
            entity_type="manuscript",
            local_id=f"marc:{CN_A}",
            records=[CN_A],
            statements=[WikidataStatement(
                property_id="P217", value="F 7956", value_type="string",
            )],
        )
        merged = merge_legacy_into_canonical([canonical], [legacy])
        assert [s.value for s in merged[0].statements if s.property_id == "P217"] == [
            "F 7956",
        ]


class TestClaimDedup:
    def test_same_fact_survives_once_and_keeps_its_references(self) -> None:
        bare = WikidataStatement(property_id="P3959", value=CN_A, value_type="string")
        sourced = WikidataStatement(
            property_id="P3959",
            value=CN_A,
            value_type="external-id",
            references=[{"property": "P248", "value": "Q118384267"}],
        )
        out = dedupe_statements([bare, sourced])
        assert len(out) == 1
        assert out[0].references

    def test_distinct_values_are_kept(self) -> None:
        out = dedupe_statements([
            WikidataStatement(property_id="P217", value="F 1", value_type="string"),
            WikidataStatement(property_id="P217", value="F 2", value_type="string"),
        ])
        assert len(out) == 2


class TestExportQualityIdentityGates:
    def _ms(self, local_id: str, label: str, shelfmark: str, cns: list[str]) -> WikidataItem:
        return WikidataItem(
            labels={"en": label},
            entity_type="manuscript",
            local_id=local_id,
            records=cns,
            statements=[
                WikidataStatement(
                    property_id="P217", value=shelfmark, value_type="string",
                ),
                *[
                    WikidataStatement(
                        property_id="P3959", value=cn, value_type="external-id",
                    )
                    for cn in cns
                ],
            ],
        )

    def test_shared_identity_blocks_the_build(self) -> None:
        items = [
            self._ms("A", "The British Library, F 8298", "F 7956", [CN_A]),
            self._ms("B", "The British Library, F 8298", "F 7956", [CN_B]),
        ]
        with pytest.raises(ValueError, match="MANUSCRIPT_SHARED_IDENTITY"):
            assert_wikidata_export_quality(items)

    def test_label_shelfmark_mismatch_blocks_the_build(self) -> None:
        with pytest.raises(ValueError, match="LABEL_SHELFMARK_MISMATCH"):
            assert_wikidata_export_quality([
                self._ms("A", "The British Library, F 8298", "F 7956", [CN_A]),
            ])

    def test_multiple_catalog_ids_block_the_build(self) -> None:
        with pytest.raises(ValueError, match="MANUSCRIPT_MULTIPLE_CATALOG_IDS"):
            assert_wikidata_export_quality([
                self._ms("A", "Jerusalem, NLI, F 7956", "F 7956", [CN_A, CN_B]),
            ])

    def test_clean_manuscript_passes(self) -> None:
        assert_wikidata_export_quality([
            self._ms("A", "Jerusalem, NLI, F 7956", "F 7956", [CN_A]),
        ])


class TestExportCarriesPropertyIds:
    def test_serialised_statements_expose_the_pid(self) -> None:
        """Rule W-62 / W-137 — exports had ``property: null`` on every row."""
        from app.pipeline.wikidata_studio import _serialise_item

        item = WikidataItem(
            labels={"en": "Jerusalem, NLI, F 1"},
            entity_type="manuscript",
            local_id="A",
            records=[CN_A],
            statements=[
                WikidataStatement(
                    property_id="P31",
                    value="Q87167",
                    value_type="wikibase-item",
                    references=[{"property": "P248", "value": "Q118384267"}],
                ),
            ],
        )
        data = _serialise_item(item)
        stmt = data["statements"][0]
        assert stmt["property"] == "P31"
        assert stmt["property_id"] == "P31"
        assert stmt["property_label"] == "instance of"
        assert data["statements"][0]["references"][0]["property"] == "P248"
