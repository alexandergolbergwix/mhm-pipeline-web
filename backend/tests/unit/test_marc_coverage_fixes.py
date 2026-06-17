"""Tests for MARC coverage fixes (place, genre RDF, provenance mining)."""
from __future__ import annotations

from typing import Any


def test_merge_ml_genres_tags_attribution() -> None:
    from app.pipeline.rdf_enrichment import merge_ml_genres

    rec: dict[str, Any] = {"genres": []}
    merge_ml_genres(rec, [{"label": "Kabbalah", "confidence": 0.9}])
    assert "Kabbalah" in rec["genres"]
    assert rec["attribution_sources"]["genre_Kabbalah"] == "AIAttribution"
    assert rec["certainty_levels"]["genre_Kabbalah"] == "Probable"


def test_merge_ml_genres_skips_when_marc_present() -> None:
    from app.pipeline.rdf_enrichment import merge_ml_genres

    rec: dict[str, Any] = {"genres": ["Halakhah"]}
    merge_ml_genres(rec, [{"label": "Kabbalah", "confidence": 0.9}])
    assert rec["genres"] == ["Halakhah"]


def test_provenance_segments_from_notes_when_561_absent() -> None:
    from app.pipeline.extraction import _provenance_segments_for_record

    record = {
        "provenance": "",
        "notes": ["המסמך נרכש על ידי הספרייה בשנת 1950"],
    }
    segments = _provenance_segments_for_record(record)
    assert len(segments) == 1
    assert "נרכש" in segments[0]


def test_provenance_segments_prefers_561() -> None:
    from app.pipeline.extraction import _provenance_segments_for_record

    record = {
        "provenance": "561 provenance text",
        "notes": ["נרכש על ידי X"],
    }
    segments = _provenance_segments_for_record(record)
    assert segments == ["561 provenance text"]


def test_looks_like_place_includes_production_place() -> None:
    from app.pipeline.authority import _looks_like_place

    record = {"place": "Mikulov (Jihomoravský kraj, Czech Republic)"}
    assert _looks_like_place("Mikulov", record)
