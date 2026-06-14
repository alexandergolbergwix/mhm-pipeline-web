"""Phase 5 (web) — Ashkenazi-community fallback gazetteer (CLAUDE.md Rule 60).

Covers the loader/lookup:
  - exact + variant + acronym + containment matching (normalised)
  - gershayim / quote punctuation tolerated ("פ\"ק" == "פק")
  - coords are real, never (0,0); QID optional
  - miss returns None (no fabrication)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.pipeline.ashkenazi_gazetteer import _normalise, lookup  # noqa: E402


class TestLookup:
    def test_english_label(self) -> None:
        hit = lookup("Prague")
        assert hit is not None
        assert round(hit["lat"], 2) == 50.09
        assert hit["wikidata_id"] == "Q1085"

    def test_hebrew_variant(self) -> None:
        hit = lookup("פראג")
        assert hit is not None and round(hit["lat"], 2) == 50.09

    def test_acronym_with_gershayim(self) -> None:
        # "פ\"ק" (Prague acronym) normalises to the same key as "פק".
        assert lookup('פ"ק') is not None
        assert lookup("פק") is not None

    def test_containment_match(self) -> None:
        # Catalog form with a qualifier still resolves.
        hit = lookup("Prague (Bohemia)")
        assert hit is not None and hit["wikidata_id"] == "Q1085"

    def test_coords_never_null_island(self) -> None:
        for name in ("Prague", "Worms", "Venice", "Vilnius"):
            hit = lookup(name)
            assert hit is not None
            assert not (abs(hit["lat"]) < 1e-9 and abs(hit["lon"]) < 1e-9)

    def test_qid_optional(self) -> None:
        # Worms ships without a verified QID but still has coords.
        hit = lookup("Worms")
        assert hit is not None
        assert hit["lat"] is not None and hit["wikidata_id"] is None

    def test_miss_returns_none(self) -> None:
        assert lookup("Tokyo") is None
        assert lookup("") is None
        assert lookup("   ") is None


class TestNormalise:
    def test_strips_gershayim_and_casefolds(self) -> None:
        assert _normalise('פ"ק') == _normalise("פק")
        assert _normalise("  Prague  ") == "prague"
