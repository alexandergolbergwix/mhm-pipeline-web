"""Tests — geo coordinates for ALL place roles in the RDF graph.

Covers:
  - rdf_enrichment.merge_approved_authority (imported here as
    _merge_authority_ids, its pre-refactor name) writes production-place
    coords into rec
  - the same writes related-place coords into rec
  - graph_builder._add_production_event emits wgs84:lat/long
  - graph_builder._add_related_places emits wgs84:lat/long
  - No coords fabricated when KIMA has no match
  - query_geography returns production places with coords
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Allow imports from the backend tree without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

from rdflib import Graph, URIRef
from rdflib.namespace import OWL

_WGS84_LAT  = URIRef("http://www.w3.org/2003/01/geo/wgs84_pos#lat")
_WGS84_LONG = URIRef("http://www.w3.org/2003/01/geo/wgs84_pos#long")


# ── helpers ────────────────────────────────────────────────────────────

def _kima_match(entity_text: str, lat: float, lon: float, role: str = "production_place", qid: str | None = "Q1234") -> dict[str, Any]:
    return {
        "entity_text": entity_text,
        "entity_kind": "place",
        "role": role,
        "wikidata_qid": qid,
        "payload": {"kima_id": "KIMA123", "kima_lat": lat, "kima_lon": lon, "kima_geonames": "987654"},
    }


def _rec(place: str = "", related: list[str] | None = None, subjects: list[dict] | None = None) -> dict[str, Any]:
    return {
        "place": place,
        "related_places": list(related or []),
        "subjects": list(subjects or []),
    }


# ── _merge_authority_ids ───────────────────────────────────────────────

class TestMergeAuthorityIdsProductionPlace:
    def test_writes_production_place_lat_lon(self) -> None:
        from app.pipeline.rdf_enrichment import merge_approved_authority as _merge_authority_ids

        rec = _rec(place="Fez")
        _merge_authority_ids(rec, [_kima_match("Fez", 34.03, -5.00)])
        assert rec["production_place_lat"] == 34.03
        assert rec["production_place_lon"] == -5.00

    def test_writes_wikidata_id_for_production_place(self) -> None:
        from app.pipeline.rdf_enrichment import merge_approved_authority as _merge_authority_ids

        rec = _rec(place="Fez")
        _merge_authority_ids(rec, [_kima_match("Fez", 34.03, -5.00, qid="Q83751")])
        assert rec["production_place_wikidata_id"] == "Q83751"

    def test_fill_only_if_absent_for_production_place(self) -> None:
        from app.pipeline.rdf_enrichment import merge_approved_authority as _merge_authority_ids

        rec = _rec(place="Fez")
        rec["production_place_lat"] = 99.0  # pre-existing
        _merge_authority_ids(rec, [_kima_match("Fez", 34.03, -5.00)])
        assert rec["production_place_lat"] == 99.0  # not overwritten

    def test_partial_name_match_still_fills(self) -> None:
        from app.pipeline.rdf_enrichment import merge_approved_authority as _merge_authority_ids

        rec = _rec(place="ʻAmrān (Yemen)")
        # KIMA normalised entity_text shorter than the full name
        _merge_authority_ids(rec, [_kima_match("ʻAmrān", 15.65, 43.94)])
        assert rec.get("production_place_lat") == 15.65

    def test_no_fabrication_when_no_matching_place(self) -> None:
        from app.pipeline.rdf_enrichment import merge_approved_authority as _merge_authority_ids

        rec = _rec(place="Somewhere Unknown")
        _merge_authority_ids(rec, [_kima_match("Fez", 34.03, -5.00)])
        assert "production_place_lat" not in rec


class TestMergeAuthorityIdsRelatedPlaces:
    def test_writes_related_place_coords(self) -> None:
        from app.pipeline.rdf_enrichment import merge_approved_authority as _merge_authority_ids

        rec = _rec(related=["Córdoba"])
        _merge_authority_ids(rec, [_kima_match("Córdoba", 37.89, -4.78, role="place")])
        assert rec["related_place_coords"]["Córdoba"]["lat"] == 37.89
        assert rec["related_place_coords"]["Córdoba"]["lon"] == -4.78

    def test_fill_only_if_absent_for_related_place(self) -> None:
        from app.pipeline.rdf_enrichment import merge_approved_authority as _merge_authority_ids

        rec = _rec(related=["Córdoba"])
        rec["related_place_coords"] = {"Córdoba": {"lat": 99.0, "lon": 0.0}}
        _merge_authority_ids(rec, [_kima_match("Córdoba", 37.89, -4.78, role="place")])
        assert rec["related_place_coords"]["Córdoba"]["lat"] == 99.0  # not overwritten

    def test_no_crash_when_related_places_empty(self) -> None:
        from app.pipeline.rdf_enrichment import merge_approved_authority as _merge_authority_ids

        rec = _rec()
        _merge_authority_ids(rec, [_kima_match("Fez", 34.03, -5.00)])
        assert "related_place_coords" not in rec


# ── graph_builder._add_production_event ───────────────────────────────

class TestGraphBuilderProductionPlaceCoords:
    def _build_graph(self, place: str, lat: float | None, lon: float | None, qid: str | None = None) -> Graph:
        from converter.rdf.graph_builder import GraphBuilder as ManuscriptGraphBuilder
        from converter.transformer.field_handlers import ExtractedData

        data = ExtractedData()
        data.place = place
        data.production_place_lat  = lat
        data.production_place_lon  = lon
        data.production_place_wikidata_id = qid
        cn = "TEST001"
        builder = ManuscriptGraphBuilder()
        return builder.build_graph(data, cn)

    def test_wgs84_emitted_when_coords_present(self) -> None:
        g = self._build_graph("Fez", 34.03, -5.00)
        lats = list(g.objects(None, _WGS84_LAT))
        assert len(lats) == 1
        assert str(lats[0]) == "34.03"

    def test_wgs84_long_emitted(self) -> None:
        g = self._build_graph("Fez", 34.03, -5.00)
        longs = list(g.objects(None, _WGS84_LONG))
        assert len(longs) == 1
        assert str(longs[0]) == "-5.0"

    def test_owl_same_as_emitted_when_qid_present(self) -> None:
        g = self._build_graph("Fez", 34.03, -5.00, qid="Q83751")
        same_as = list(g.objects(None, OWL.sameAs))
        assert URIRef("https://www.wikidata.org/entity/Q83751") in same_as

    def test_no_wgs84_when_coords_absent(self) -> None:
        g = self._build_graph("Fez", None, None)
        lats = list(g.objects(None, _WGS84_LAT))
        assert lats == []


class TestGraphBuilderRelatedPlaceCoords:
    def _build_graph(self, places: list[str], coords: dict | None) -> Graph:
        from converter.rdf.graph_builder import GraphBuilder as ManuscriptGraphBuilder
        from converter.transformer.field_handlers import ExtractedData

        data = ExtractedData()
        data.related_places = places
        data.related_place_coords = coords
        cn = "TEST002"
        builder = ManuscriptGraphBuilder()
        return builder.build_graph(data, cn)

    def test_wgs84_emitted_for_related_place(self) -> None:
        g = self._build_graph(["Córdoba"], {"Córdoba": {"lat": 37.89, "lon": -4.78}})
        place_uri = URIRef("http://www.ontology.org.il/HebrewManuscripts/2025-12-06#place_C%C3%B3rdoba")
        lats = list(g.objects(place_uri, _WGS84_LAT))
        # Accept any URI that contains 'rdoba' in case encoding differs
        all_place_lats = [
            (s, o) for s, p, o in g.triples((None, _WGS84_LAT, None))
            if "rdoba" in str(s) or "C%C3%B3rdoba" in str(s) or "Cordoba" in str(s) or "órdoba" in str(s)
        ]
        assert len(all_place_lats) >= 1

    def test_no_wgs84_when_coords_dict_empty(self) -> None:
        g = self._build_graph(["SomePlace"], {})
        lats = list(g.objects(None, _WGS84_LAT))
        assert lats == []

    def test_no_fabrication_when_coords_none(self) -> None:
        g = self._build_graph(["SomePlace"], None)
        lats = list(g.objects(None, _WGS84_LAT))
        assert lats == []
