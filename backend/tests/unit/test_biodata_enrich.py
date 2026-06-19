"""Unit tests for authority biodata merge + payload serialization."""

from __future__ import annotations

from converter.authority.biodata import BioData, extract_viaf_biodata
from converter.authority.biodata_enrich import (
    build_biodata_payload_slice,
    merge_authority_biodata,
)


VIAF_RASHI_CLUSTER = {
    "viafID": "27066507",
    "nameType": "Personal",
    "birthDate": "1040",
    "deathDate": "1105",
    "mainHeadings": {
        "data": [
            {
                "text": "Rashi, 1040-1105",
                "sources": {"s": ["DNB"]},
            },
            {
                "text": "רש״י",
                "sources": {"s": ["NLI"]},
            },
        ],
    },
    "x400s": {
        "x400": [
            {
                "datafield": {
                    "subfield": "Salomon ben Isaac",
                },
            },
        ],
    },
    "occupation": {
        "data": [
            {"text": "rabbi"},
            {"text": "Bible commentator"},
        ],
    },
    "nationalityOfEntity": {
        "data": [{"text": "France"}],
    },
}


class TestExtractViafBiodataFixture:
    def test_fixture_has_occupations_and_names(self) -> None:
        bio = extract_viaf_biodata(VIAF_RASHI_CLUSTER)
        assert "rabbi" in bio.occupations
        assert "Bible commentator" in bio.occupations
        assert any("רש" in n for n in bio.names.get("he", []))
        assert bio.dates.get("birth") == "1040"
        assert bio.dates.get("death") == "1105"


class TestMergeAuthorityBiodata:
    def test_viaf_only_merge(self) -> None:
        viaf_bio = extract_viaf_biodata(VIAF_RASHI_CLUSTER)
        merged, sources = merge_authority_biodata(viaf_bio=viaf_bio)
        assert sources == ["viaf"]
        assert "rabbi" in merged.occupations
        assert merged.dates.get("birth") == "1040"

    def test_mazal_hebrew_names_preferred_over_viaf(self) -> None:
        mazal_bio = BioData(
            names={"he": ["שלמה בן יצחק"], "lat": ["Salomon ben Isaac"]},
            dates={"birth": "1040", "death": "1105"},
        )
        viaf_bio = extract_viaf_biodata(VIAF_RASHI_CLUSTER)
        merged, sources = merge_authority_biodata(mazal_bio=mazal_bio, viaf_bio=viaf_bio)
        assert sources == ["mazal", "viaf"]
        assert merged.names["he"][0] == "שלמה בן יצחק"
        assert "rabbi" in merged.occupations

    def test_empty_inputs_return_empty(self) -> None:
        merged, sources = merge_authority_biodata()
        assert sources == []
        assert merged == BioData()


class TestBuildBiodataPayloadSlice:
    def test_person_slice_from_viaf_cluster(self) -> None:
        payload = build_biodata_payload_slice(viaf_cluster_raw=VIAF_RASHI_CLUSTER)
        assert payload["biodata_version"] == 1
        assert payload["biodata_sources"] == ["viaf"]
        assert "Bible commentator" in payload["occupations"]
        assert payload["biodata_authority"]["names"]["he"]

    def test_mazal_and_viaf_combined(self) -> None:
        mazal_entry = {
            "preferred_name_heb": "שלמה בן יצחק",
            "preferred_name_lat": "Salomon ben Isaac",
            "dates": "1040-1105",
            "nli_id": "987007259505005171",
        }
        payload = build_biodata_payload_slice(
            mazal_entry=mazal_entry,
            viaf_cluster_raw=VIAF_RASHI_CLUSTER,
        )
        assert set(payload["biodata_sources"]) == {"mazal", "viaf"}
        assert "שלמה בן יצחק" in payload["name_variants_he"]
        assert "France" in (payload["biodata_authority"]["places"].get("country") or [])

    def test_no_sources_returns_empty_dict(self) -> None:
        assert build_biodata_payload_slice() == {}
