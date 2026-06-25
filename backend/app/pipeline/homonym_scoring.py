"""Score and pick among Mazal person homonyms (Rule W-37).

Uses fuzzy date overlap via :func:`dates_overlap` — exact string equality on
MARC $d vs Mazal ``dates`` is insufficient for catalog variants.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from converter.authority.stage3_guards import evaluate_date_conflict
from converter.transformer.date_resolver import (
    DateRange,
    dates_overlap,
    resolve_person_dates,
)

SCORE_TAG_100 = 100
SCORE_DATE_OVERLAP = 50
SCORE_MS_PLAUSIBLE = 20
PENALTY_FUZZY = 30
PENALTY_SUBJECT_TAG = 40
TIE_THRESHOLD = 15


@dataclass
class ScoredCandidate:
    candidate: dict[str, Any]
    score: int
    date_overlap: bool = False

    def as_payload_entry(self) -> dict[str, Any]:
        c = self.candidate
        return {
            "mazal_id": c.get("mazal_id"),
            "dates": c.get("dates"),
            "main_marc_tag": c.get("main_marc_tag"),
            "preferred_name_heb": c.get("preferred_name_heb"),
            "preferred_name_lat": c.get("preferred_name_lat"),
            "score": self.score,
            "date_overlap": self.date_overlap,
        }


@dataclass
class MazalMatchDecision:
    winner: dict[str, Any] | None = None
    abstain: bool = False
    reason: str = ""
    top_n: list[ScoredCandidate] = field(default_factory=list)

    @property
    def homonym_candidates(self) -> list[dict[str, Any]]:
        return [s.as_payload_entry() for s in self.top_n]

    @property
    def personality_count(self) -> int:
        return sum(
            1 for s in self.top_n
            if str(s.candidate.get("main_marc_tag") or "") == "100"
        )


def parse_authority_dates(date_str: str | None) -> DateRange:
    """Build a :class:`DateRange` from a Mazal or MARC $d date string."""
    if not date_str or not str(date_str).strip():
        return DateRange(None, None, False, "empty")
    parsed = resolve_person_dates(str(date_str).strip())
    birth = parsed.get("birth_year")
    death = parsed.get("death_year")
    active = parsed.get("active_year")
    if birth is not None or death is not None:
        return DateRange(birth, death, False, "person_range")
    if active is not None:
        return DateRange(active, active, False, "active_year")
    return DateRange(None, None, False, "unresolved")


def _parsed_years(date_str: str | None) -> tuple[int | None, int | None]:
    if not date_str:
        return None, None
    parsed = resolve_person_dates(str(date_str).strip())
    birth = parsed.get("birth_year")
    death = parsed.get("death_year")
    active = parsed.get("active_year")
    if birth is None and death is None and active is not None:
        return active, active
    return birth, death


def score_mazal_candidate(
    candidate: dict[str, Any],
    *,
    marc_dates: str | None,
    ms_year: int | None,
    role: str,
) -> ScoredCandidate:
    score = 0
    tag = str(candidate.get("main_marc_tag") or "")
    if tag == "100":
        score += SCORE_TAG_100
    elif tag in ("150", "450"):
        score -= PENALTY_SUBJECT_TAG

    marc_range = parse_authority_dates(marc_dates)
    auth_range = parse_authority_dates(candidate.get("dates"))
    overlap = False
    if marc_range.year_start is not None or marc_range.year_end is not None:
        if dates_overlap(marc_range, auth_range):
            score += SCORE_DATE_OVERLAP
            overlap = True

    birth, death = _parsed_years(candidate.get("dates"))
    if ms_year is not None and evaluate_date_conflict(role, ms_year, birth, death) is None:
        score += SCORE_MS_PLAUSIBLE

    if candidate.get("_fuzzy"):
        score -= PENALTY_FUZZY

    return ScoredCandidate(candidate=candidate, score=score, date_overlap=overlap)


def pick_mazal_candidate(
    candidates: list[dict[str, Any]],
    *,
    marc_dates: str | None = None,
    ms_year: int | None = None,
    role: str = "",
    limit: int = 8,
) -> MazalMatchDecision:
    """Pick one Mazal row or abstain when homonyms tie without date overlap."""
    if not candidates:
        return MazalMatchDecision(abstain=True, reason="no_candidates")

    scored = [
        score_mazal_candidate(c, marc_dates=marc_dates, ms_year=ms_year, role=role)
        for c in candidates
    ]
    scored.sort(key=lambda s: (-s.score, str(s.candidate.get("mazal_id") or "")))
    top_n = scored[:limit]

    if len(scored) == 1:
        return MazalMatchDecision(
            winner=scored[0].candidate,
            reason="single_candidate",
            top_n=top_n,
        )

    best = scored[0]
    second = scored[1]
    gap = best.score - second.score

    personalities = [s for s in scored if str(s.candidate.get("main_marc_tag") or "") == "100"]
    if len(personalities) >= 2 and not marc_dates:
        if gap <= TIE_THRESHOLD and not best.date_overlap:
            return MazalMatchDecision(
                abstain=True,
                reason="homonym_tie_no_dates",
                top_n=top_n,
            )

    if gap <= TIE_THRESHOLD and not best.date_overlap and not second.date_overlap:
        return MazalMatchDecision(
            abstain=True,
            reason="homonym_tie_no_overlap",
            top_n=top_n,
        )

    if best.score <= 0 and not best.date_overlap:
        return MazalMatchDecision(
            abstain=True,
            reason="low_confidence_homonym",
            top_n=top_n,
        )

    return MazalMatchDecision(
        winner=best.candidate,
        reason="scored_winner",
        top_n=top_n,
    )
