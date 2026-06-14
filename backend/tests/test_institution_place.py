"""Phase 4 (web) — institution/collection → place two-hop (CLAUDE.md Rule 60).

Covers the pure binding parser for institution_place:
  - P159 (headquarters) resolves; precedence over P276/P131
  - abstain when the entity is a human (Q5 → owner_place territory)
  - abstain on conflicting coords for a property (Rule 40 / guard A8)
  - coord sanity (guard A6): (0,0) and out-of-range rejected
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.pipeline.research_geo_enrich import (  # noqa: E402
    _parse_institution_binding,
    institution_place,
)


def _b(type_qid: str | None = None, **points: str) -> dict:
    """Build one WDQS binding row with a P31 type + named point properties."""
    row: dict = {}
    if type_qid:
        row["type"] = {"value": f"http://www.wikidata.org/entity/{type_qid}"}
    for pid, wkt in points.items():
        row[pid] = {"value": wkt}
    return row


class TestParseInstitutionBinding:
    def test_p159_headquarters_resolves(self) -> None:
        out = _parse_institution_binding([_b("Q...col", P159="Point(8.54 47.37)")])
        assert out is not None
        assert round(out["lat"], 2) == 47.37 and round(out["lon"], 2) == 8.54
        assert out["geo_source"] == "P159"

    def test_precedence_p159_over_p276(self) -> None:
        out = _parse_institution_binding([
            _b("Q42", P159="Point(8.54 47.37)", P276="Point(2.35 48.85)"),
        ])
        assert out["geo_source"] == "P159"

    def test_falls_through_to_p131(self) -> None:
        out = _parse_institution_binding([_b("Q42", P131="Point(2.35 48.85)")])
        assert out["geo_source"] == "P131"

    def test_human_abstains(self) -> None:
        # A Q5 entity is owner_place's job, not institution_place's.
        out = _parse_institution_binding([_b("Q5", P159="Point(8.54 47.37)")])
        assert out is None

    def test_conflicting_coords_abstain(self) -> None:
        out = _parse_institution_binding([
            _b("Q42", P159="Point(8.54 47.37)"),
            _b("Q42", P159="Point(2.35 48.85)"),
        ])
        assert out is None  # two distinct P159 coords → abstain

    def test_null_island_rejected(self) -> None:
        out = _parse_institution_binding([_b("Q42", P159="Point(0 0)")])
        assert out is None

    def test_empty_bindings(self) -> None:
        assert _parse_institution_binding([]) is None


class TestInstitutionPlaceGuards:
    async def test_malformed_qid_returns_none(self) -> None:
        assert await institution_place("not-a-qid") is None

    async def test_no_network_env_returns_none(self, monkeypatch) -> None:
        monkeypatch.setenv("MHM_NO_NETWORK", "true")
        assert await institution_place("Q42") is None
