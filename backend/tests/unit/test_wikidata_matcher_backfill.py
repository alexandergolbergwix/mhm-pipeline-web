"""Wikidata P214 VIAF backfill and Mazal P8189 triangulation."""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest


def test_find_viaf_by_qid_picks_valid_among_junk_p214() -> None:
    """Allony Q59530677: one real cluster + one SRU artefact → keep cluster."""
    from converter.authority import wikidata_matcher as wd_mod
    from converter.authority.wikidata_matcher import WikidataMatcher

    payload = {
        "head": {"vars": ["viaf"]},
        "results": {
            "bindings": [
                {"viaf": {"type": "literal", "value": "49353935"}},
                {"viaf": {"type": "literal", "value": "280159474312627662451"}},
            ],
        },
    }
    with patch.object(wd_mod, "_http_sparql", return_value=payload):
        result = WikidataMatcher().find_viaf_by_qid("Q59530677")

    assert result == "49353935"


def test_find_viaf_by_qid_abstains_on_two_valid_clusters() -> None:
    from converter.authority import wikidata_matcher as wd_mod
    from converter.authority.wikidata_matcher import WikidataMatcher

    payload = {
        "head": {"vars": ["viaf"]},
        "results": {
            "bindings": [
                {"viaf": {"type": "literal", "value": "12345678"}},
                {"viaf": {"type": "literal", "value": "87654321"}},
            ],
        },
    }
    with patch.object(wd_mod, "_http_sparql", return_value=payload):
        result = WikidataMatcher().find_viaf_by_qid("Q42")

    assert result is None


@pytest.mark.asyncio
async def test_mazal_hit_backfills_wikidata_and_viaf_before_label_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mazal P8189 triangulation must run before unreliable label search."""
    from app.pipeline.authority import DesktopMatcher

    # guard_wikidata_crosscheck (Rule W-37) makes a real Wikidata SPARQL
    # lookup by default — this test is about Mazal/Wikidata *ordering*, not
    # that guard, so keep it deterministic and offline.
    monkeypatch.setenv("MHM_DISABLE_WIKIDATA_CROSSCHECK", "1")

    matcher = DesktopMatcher()
    matcher._mazal = object()
    matcher._viaf = None
    matcher._wikidata = object()
    matcher._authority_backend = None

    async def _mazal_person(*_a: Any, **_k: Any) -> dict[str, str]:
        return {"mazal_id": "987007257676505171", "main_marc_tag": "100"}

    async def _mazal_details(*_a: Any, **_k: Any) -> dict[str, str]:
        return {"preferred_name_lat": "Allony, Nehemya", "dates": "1906-1983"}

    async def _wd_by_mazal(_mid: str, **_k: Any) -> str:
        assert _mid == "987007257676505171"
        return "Q59530677"

    async def _wd_person(_text: str, **_k: Any) -> str:
        pytest.fail("label search must not run when Mazal P8189 resolves")

    async def _wd_dates(_qid: str, **_k: Any) -> dict[str, int]:
        return {"birth_year": 1906, "death_year": 1983}

    async def _wd_enrich(_qid: str, **_k: Any) -> dict[str, str]:
        return {
            "viaf_id": "49353935",
            "he_label": "נחמיה אלוני",
            "en_description": "Israeli scholar",
        }

    matcher._mazal_match_person = _mazal_person  # type: ignore[method-assign]
    matcher._mazal_get_details = _mazal_details  # type: ignore[method-assign]
    matcher._wikidata_match_by_mazal = _wd_by_mazal  # type: ignore[method-assign]
    matcher._wikidata_match_person = _wd_person  # type: ignore[method-assign]
    matcher._wikidata_dates = _wd_dates  # type: ignore[method-assign]
    matcher._wikidata_enrich_qid = _wd_enrich  # type: ignore[method-assign]

    entity = {
        "text": "Allony, Nehemia",
        "kind": "person",
        "role": "former owner",
        "field": "710",
    }
    # guard_modern_person (converter/authority/stage3_guards.py) rejects a
    # birth year >= 1900 when the manuscript looks premodern/undated — an
    # empty marc_record leaves ms_year=None, which reads as "undated" and
    # wrongly flags this 1906-born *former owner* as cataloguer noise (the
    # guard has no role awareness, so it can't tell an owner from an
    # anachronistic author match). Giving it a compatible production year
    # keeps this test's actual point — Mazal P8189 triangulation ordering —
    # isolated from that unrelated guard.
    marc: dict[str, Any] = {"dates": {"year": 1950}}

    candidates = await matcher.match(
        entity, marc, db_session=None, user_id=None, skip_cache=True,
    )
    assert len(candidates) == 1
    c = candidates[0]
    assert c.mazal_id == "987007257676505171"
    assert c.wikidata_qid == "Q59530677"
    assert c.viaf_id == "49353935"
    assert c.source == "cross_source"
    assert "mazal" in (c.payload or {}).get("sources", [])
    assert "wikidata" in (c.payload or {}).get("sources", [])
    assert "viaf" in (c.payload or {}).get("sources", [])
