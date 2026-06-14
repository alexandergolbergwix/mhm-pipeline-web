"""Phase 3 (web) — provenance events in the RDF graph (CLAUDE.md Rule 60).

Covers:
  - _merge_authority_ids writes KIMA coords back onto provenance_events
  - GraphBuilder emits a CIDOC event + wgs84-bearing E53_Place per geo event
  - query_geography surfaces the new places (via hm:mentions_place)
  - integrity: no wgs84 / no event emitted when coords are absent
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.pipeline.rdf_build import _merge_authority_ids  # noqa: E402
from app.pipeline.research_queries import query_geography  # noqa: E402


_WGS84_LAT = "http://www.w3.org/2003/01/geo/wgs84_pos#lat"
_WGS84_LONG = "http://www.w3.org/2003/01/geo/wgs84_pos#long"


def _kima_match(place: str, lat: float, lon: float, qid: str = "Q72") -> dict:
    return {
        "control_number": "CN1",
        "entity_text": place,
        "entity_kind": "place",
        "role": "acquisition_place",
        "wikidata_qid": qid,
        "payload": {"kima_id": 7, "kima_lat": lat, "kima_lon": lon, "kima_geonames": "2657896"},
    }


# ── coord propagation ────────────────────────────────────────────────────────

class TestMergeCoordsOntoEvents:
    def test_coords_written_onto_matching_event(self) -> None:
        rec = {
            "place": "Fez",
            "provenance_events": [
                {"type": "acquisition", "place_text": "Zurich", "lat": None,
                 "lon": None, "wikidata_id": None, "source_field": "541"},
            ],
        }
        _merge_authority_ids(rec, [_kima_match("Zurich", 47.37, 8.54, "Q72")])
        ev = rec["provenance_events"][0]
        assert ev["lat"] == 47.37 and ev["lon"] == 8.54
        assert ev["wikidata_id"] == "Q72"
        assert ev["geonames_id"] == "2657896"

    def test_no_match_leaves_event_uncoorded(self) -> None:
        rec = {
            "provenance_events": [
                {"type": "acquisition", "place_text": "Zurich", "lat": None, "lon": None},
            ],
        }
        _merge_authority_ids(rec, [_kima_match("Lisbon", 38.7, -9.1)])
        ev = rec["provenance_events"][0]
        assert ev["lat"] is None and ev["lon"] is None


# ── RDF emission ─────────────────────────────────────────────────────────────

def _build_graph(provenance_events: list[dict]):
    from converter.transformer.field_handlers import ExtractedData
    from converter.transformer.mapper import MarcToRdfMapper

    extracted = ExtractedData()
    extracted.control_number = "CN1"
    extracted.title = "Test MS"
    extracted.provenance_events = provenance_events
    mapper = MarcToRdfMapper()
    return mapper.graph_builder.build_graph(extracted, "CN1")


class TestRdfEmission:
    def test_geo_event_emits_wgs84_place(self) -> None:
        g = _build_graph([
            {"type": "acquisition", "place_text": "Zurich", "lat": 47.37,
             "lon": 8.54, "wikidata_id": "Q72", "year": 1985, "source_field": "541"},
        ])
        lat_triples = [(s, o) for s, p, o in g if str(p) == _WGS84_LAT]
        lon_triples = [(s, o) for s, p, o in g if str(p) == _WGS84_LONG]
        assert any(str(o) == "47.37" for _, o in lat_triples)
        assert any(str(o) == "8.54" for _, o in lon_triples)
        # CIDOC event node present + linked to ms.
        ttl = g.serialize(format="turtle")
        assert "has_provenance_event" in ttl
        assert "E8_Acquisition" in ttl
        assert "P7_took_place_at" in ttl

    def test_geography_query_surfaces_event_place(self) -> None:
        g = _build_graph([
            {"type": "conservation", "place_text": "Jerusalem", "lat": 31.79,
             "lon": 35.21, "wikidata_id": "Q1218", "year": 2010, "source_field": "583"},
        ])
        rows = query_geography(g)
        labels = {r["place_label"] for r in rows}
        assert any("Jerusalem" in lbl for lbl in labels)
        jeru = next(r for r in rows if "Jerusalem" in r["place_label"])
        assert jeru["lat"] == 31.79 and jeru["lon"] == 35.21

    def test_no_coords_no_wgs84_no_event(self) -> None:
        # Integrity: an event without KIMA coords must NOT reach the graph.
        g = _build_graph([
            {"type": "acquisition", "place_text": "Nowhere", "lat": None,
             "lon": None, "source_field": "541"},
        ])
        ttl = g.serialize(format="turtle")
        assert "has_provenance_event" not in ttl
        assert "Nowhere" not in ttl
