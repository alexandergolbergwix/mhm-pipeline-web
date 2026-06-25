"""Unit tests for homonym_scoring (Rule W-37)."""
from __future__ import annotations

from app.pipeline.homonym_scoring import (
    parse_authority_dates,
    pick_mazal_candidate,
    score_mazal_candidate,
)
from converter.transformer.date_resolver import dates_overlap


def _cand(
    mazal_id: str,
    *,
    tag: str = "100",
    dates: str = "",
) -> dict:
    return {
        "mazal_id": mazal_id,
        "main_marc_tag": tag,
        "dates": dates,
        "entity_type": "person",
    }


def test_dates_overlap_ca_variants() -> None:
    a = parse_authority_dates("1542-1620")
    b = parse_authority_dates("ca. 1542-ca. 1620")
    assert dates_overlap(a, b)


def test_pick_winner_with_marc_dates() -> None:
    candidates = [
        _cand("subject", tag="150", dates=""),
        _cand("personality", tag="100", dates="1138-1204"),
    ]
    decision = pick_mazal_candidate(
        candidates,
        marc_dates="1138-1204",
        role="author",
    )
    assert not decision.abstain
    assert decision.winner is not None
    assert decision.winner["mazal_id"] == "personality"


def test_abstain_multiple_personalities_no_dates() -> None:
    candidates = [
        _cand("a", tag="100", dates="1000-1050"),
        _cand("b", tag="100", dates="1100-1150"),
    ]
    decision = pick_mazal_candidate(candidates, role="author")
    assert decision.abstain
    assert decision.winner is None
    assert len(decision.homonym_candidates) >= 2


def test_score_prefers_tag_100() -> None:
    s100 = score_mazal_candidate(_cand("a", tag="100"), marc_dates=None, ms_year=None, role="author")
    s150 = score_mazal_candidate(_cand("b", tag="150"), marc_dates=None, ms_year=None, role="author")
    assert s100.score > s150.score
