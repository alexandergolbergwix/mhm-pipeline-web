"""Tests — corpus movement map (Phase 2).

Covers:
  - _extract_corpus_item extracts production coords, year, genres, owners
  - build_corpus_movement filter isolation (year, place, genre, owner)
  - build_corpus_facets returns correct distinct values + year range
  - Integrity: no coords fabricated when KIMA absent
  - Cache kinds registered in inference_cache.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.pipeline.corpus_movement import (
    _extract_corpus_item,
    build_corpus_facets,
    build_corpus_movement,
)


# ── helpers ──────────────────────────────────────────────────────────────

def _prepared(
    title: str = "Test MS",
    place: str = "Fez",
    year: int | None = 1450,
    genres: list[str] | None = None,
    related: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "title": title,
        "place": place,
        "dates": {"year": year, "date_start": year, "date_end": year} if year else {},
        "genre_form": genres or [],
        "genres": [],
        "related_places": related or [],
    }


def _kima_match(place_text: str, lat: float, lon: float) -> dict[str, Any]:
    return {
        "entity_text": place_text,
        "entity_kind": "place",
        "role": "production_place",
        "matched_name": None,
        "wikidata_qid": "Q12345",
        "confidence": "high",
        "approved": True,
        "payload": {"kima_lat": lat, "kima_lon": lon},
    }


def _owner_match(name: str, confidence: str = "high") -> dict[str, Any]:
    return {
        "entity_text": name,
        "entity_kind": "person",
        "role": "owner",
        "matched_name": name,
        "wikidata_qid": None,
        "confidence": confidence,
        "approved": True,
        "payload": {},
    }


# ── _extract_corpus_item ──────────────────────────────────────────────────

class TestExtractCorpusItem:
    def test_extracts_production_coords(self) -> None:
        item = _extract_corpus_item(
            _prepared(place="Fez"), [_kima_match("Fez", 34.03, -5.0)], "CN001",
        )
        assert item["production_lat"] == 34.03
        assert item["production_lon"] == -5.0
        assert item["has_production_point"] is True

    def test_no_coords_when_kima_absent(self) -> None:
        item = _extract_corpus_item(_prepared(place="Unknown"), [], "CN002")
        assert item["production_lat"] is None
        assert item["has_production_point"] is False

    def test_extracts_production_year(self) -> None:
        item = _extract_corpus_item(_prepared(year=1500), [], "CN003")
        assert item["production_year"] == 1500

    def test_extracts_genres(self) -> None:
        prepared = _prepared(genres=["Halakha", "Kabbalah"])
        item = _extract_corpus_item(prepared, [], "CN004")
        assert "Halakha" in item["genres"]
        assert "Kabbalah" in item["genres"]

    def test_extracts_owners_high_confidence_only(self) -> None:
        matches = [
            _owner_match("Abraham", "high"),
            _owner_match("Isaac", "low"),
        ]
        item = _extract_corpus_item(_prepared(), matches, "CN005")
        assert "Abraham" in item["owners"]
        assert "Isaac" not in item["owners"]

    def test_holder_always_nli(self) -> None:
        item = _extract_corpus_item(_prepared(), [], "CN006")
        assert abs(item["holder_lat"] - 31.7942) < 0.001
        assert abs(item["holder_lon"] - 35.2007) < 0.001

    def test_production_place_in_places_list(self) -> None:
        item = _extract_corpus_item(_prepared(place="Fez"), [], "CN007")
        assert "Fez" in item["places"]

    def test_related_places_in_places_list(self) -> None:
        item = _extract_corpus_item(_prepared(related=["Rome"]), [], "CN008")
        assert "Rome" in item["places"]

    def test_partial_name_match_finds_coords(self) -> None:
        match = _kima_match("Fez", 34.03, -5.0)
        prepared = _prepared(place="Fez (Morocco)")
        item = _extract_corpus_item(prepared, [match], "CN009")
        assert item["production_lat"] == 34.03

    def test_event_places_collected_and_filterable(self) -> None:
        prepared = _prepared(place="Fez")
        prepared["provenance_events"] = [
            {"type": "acquisition", "place_text": "Zurich", "year": 1985},
        ]
        zurich = {
            "entity_text": "Zurich", "entity_kind": "place",
            "role": "acquisition_place", "wikidata_qid": "Q72",
            "confidence": "high", "approved": True,
            "payload": {"kima_lat": 47.37, "kima_lon": 8.54},
        }
        item = _extract_corpus_item(prepared, [_kima_match("Fez", 34.0, -5.0), zurich], "CN010")
        assert any(ep["place"] == "Zurich" and ep["lat"] == 47.37
                   for ep in item["event_places"])
        assert "Zurich" in item["places"]  # filterable on the corpus place facet


# ── build_corpus_movement filters ─────────────────────────────────────────

def _make_items(count: int = 5) -> list[dict[str, Any]]:
    items = []
    for i in range(count):
        year = 1400 + i * 20
        items.append({
            "control_number": f"CN{i:03}",
            "label": f"MS {i}",
            "production_lat": 34.0 + i,
            "production_lon": -5.0 + i,
            "production_year": year,
            "production_year_earliest": year,
            "production_year_latest": year,
            "production_place": f"Place{i}",
            "has_production_point": True,
            "holder_lat": 31.7942,
            "holder_lon": 35.2007,
            "holder_label": "National Library of Israel",
            "genres": [f"genre{i % 3}"],
            "owners": [f"owner{i % 2}"],
            "places": [f"Place{i}"],
        })
    return items


class TestBuildCorpusMovementFilters:
    def test_no_filter_returns_all(self) -> None:
        items = _make_items(5)
        result = build_corpus_movement(items)
        assert len(result["manuscripts"]) == 5

    def test_from_year_filter(self) -> None:
        items = _make_items(5)  # years: 1400, 1420, 1440, 1460, 1480
        result = build_corpus_movement(items, from_year=1440)
        assert all(m["production_year"] >= 1440 for m in result["manuscripts"])
        assert len(result["manuscripts"]) == 3

    def test_to_year_filter(self) -> None:
        items = _make_items(5)
        result = build_corpus_movement(items, to_year=1440)
        assert all(m["production_year"] <= 1440 for m in result["manuscripts"])
        assert len(result["manuscripts"]) == 3

    def test_year_range_filter(self) -> None:
        items = _make_items(5)
        result = build_corpus_movement(items, from_year=1420, to_year=1460)
        assert len(result["manuscripts"]) == 3

    def test_place_filter(self) -> None:
        items = _make_items(5)
        result = build_corpus_movement(items, place="Place2")
        assert len(result["manuscripts"]) == 1
        assert result["manuscripts"][0]["control_number"] == "CN002"

    def test_genre_filter(self) -> None:
        items = _make_items(6)  # genres cycle 0,1,2,0,1,2
        result = build_corpus_movement(items, genre="genre0")
        assert all("genre0" in m["genres"] for m in result["manuscripts"])

    def test_owner_filter(self) -> None:
        items = _make_items(6)  # owners cycle 0,1,0,1,0,1
        result = build_corpus_movement(items, owner="owner0")
        assert all("owner0" in m["owners"] for m in result["manuscripts"])

    def test_year_counts_aggregation(self) -> None:
        items = _make_items(3)  # years 1400, 1420, 1440
        result = build_corpus_movement(items)
        year_map = {r["year"]: r["count"] for r in result["year_counts"]}
        assert year_map[1400] == 1
        assert year_map[1420] == 1

    def test_multiple_filters_and(self) -> None:
        items = _make_items(5)
        result = build_corpus_movement(items, from_year=1440, genre="genre2")
        # genre2: indices 2 (1440), and (5 would be 1480 but only 5 items)
        cns = {m["control_number"] for m in result["manuscripts"]}
        assert "CN002" in cns  # year=1440, genre=genre2

    def test_no_match_returns_empty(self) -> None:
        items = _make_items(5)
        result = build_corpus_movement(items, place="NoSuchPlace")
        assert result["manuscripts"] == []
        assert result["year_counts"] == []


# ── build_corpus_facets ───────────────────────────────────────────────────

class TestBuildCorpusFacets:
    def test_year_range(self) -> None:
        items = _make_items(5)  # years 1400..1480
        facets = build_corpus_facets(items)
        assert facets["year_min"] == 1400
        assert facets["year_max"] == 1480

    def test_distinct_places(self) -> None:
        items = _make_items(3)
        facets = build_corpus_facets(items)
        assert len(facets["places"]) == 3
        assert "Place0" in facets["places"]

    def test_distinct_genres(self) -> None:
        items = _make_items(3)  # genres: genre0, genre1, genre2
        facets = build_corpus_facets(items)
        assert set(facets["genres"]) == {"genre0", "genre1", "genre2"}

    def test_distinct_owners(self) -> None:
        items = _make_items(4)  # owners: owner0, owner1
        facets = build_corpus_facets(items)
        assert set(facets["owners"]) == {"owner0", "owner1"}

    def test_empty_items_returns_none_year_range(self) -> None:
        facets = build_corpus_facets([])
        assert facets["year_min"] is None
        assert facets["year_max"] is None
        assert facets["places"] == []

    def test_items_without_year_excluded_from_range(self) -> None:
        items = _make_items(3)
        items[1]["production_year"] = None  # remove one year
        facets = build_corpus_facets(items)
        assert facets["year_min"] == 1400
        assert facets["year_max"] == 1440


# ── cache kind registration ───────────────────────────────────────────────

class TestCacheKindRegistration:
    def test_movement_kind_in_kind_ttl(self) -> None:
        from app.pipeline.inference_cache import KIND_TTL
        assert "research.movement" in KIND_TTL

    def test_movement_facets_kind_in_kind_ttl(self) -> None:
        from app.pipeline.inference_cache import KIND_TTL
        assert "research.movement_facets" in KIND_TTL

    def test_movement_kind_in_redis_ttl(self) -> None:
        from app.pipeline.inference_cache import _REDIS_TTL_SECONDS
        assert "research.movement" in _REDIS_TTL_SECONDS

    def test_movement_facets_kind_in_redis_ttl(self) -> None:
        from app.pipeline.inference_cache import _REDIS_TTL_SECONDS
        assert "research.movement_facets" in _REDIS_TTL_SECONDS
