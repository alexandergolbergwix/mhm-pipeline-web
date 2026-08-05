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
# The scorer had no name term at all (Rule W-166): a candidate whose given name is
# simply a different person scored identically to the right one, and `_fuzzy` was a
# -30 nudge rather than a signal about WHICH name matched. Weighted above the
# date-overlap term, because a shared date range across relatives is common and a
# different given name is decisive.
SCORE_NAME_MATCH = 60
PENALTY_NAME_MISMATCH = 60


def name_term_enabled() -> bool:
    """Is the name-similarity term active? OFF by default — read the docstring.

    This term changes *matching*, whose output feeds person-link evidence
    (Rule W-162), date suppression (Rule W-166) and the W-155 person drop. A
    corpus-wide matching change has to be measured before it is trusted, and the
    Studio export cannot measure it: it never records WHICH MARC heading each
    authority row matched, so any pairing reconstructed from it mispairs (an
    institution's 710 against a person's row, an author against a scribe) and
    reports flips that are artifacts.

    Measure it against live authority data first:

        cd backend && .venv/bin/python -m scripts.dryrun_homonym_name_term --run <id>

    then enable with ``AUTHORITY_HOMONYM_NAME_TERM=1``. Until then the scorer
    behaves exactly as before, and the wrong-Gabbai class of error is still caught
    downstream by the crosscheck gate and the heading-fidelity check (Rule W-166) —
    just not prevented at the source.
    """
    import os  # noqa: PLC0415

    return os.getenv("AUTHORITY_HOMONYM_NAME_TERM", "0").strip().lower() not in {
        "0", "false", "no", "",
    }
PENALTY_SUBJECT_TAG = 40
TIE_THRESHOLD = 15


@dataclass
class ScoredCandidate:
    candidate: dict[str, Any]
    score: int
    date_overlap: bool = False
    #: None when no MARC heading was supplied to compare against.
    name_match: bool | None = None

    def as_payload_entry(self) -> dict[str, Any]:
        c = self.candidate
        return {
            "mazal_id": c.get("mazal_id"),
            "dates": c.get("dates"),
            "main_marc_tag": c.get("main_marc_tag"),
            "preferred_name_heb": c.get("preferred_name_heb"),
            "preferred_name_lat": c.get("preferred_name_lat"),
            "score": self.score,
            "name_match": self.name_match,
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
    marc_name: str | None = None,
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

    name_match: bool | None = None
    if marc_name and name_term_enabled():
        from converter.authority.heading_fidelity import heading_matches  # noqa: PLC0415

        headings = [
            str(candidate.get("preferred_name_heb") or ""),
            str(candidate.get("preferred_name_lat") or ""),
        ]
        name_match = any(
            heading_matches(marc_name, heading) for heading in headings if heading
        )
        score += SCORE_NAME_MATCH if name_match else -PENALTY_NAME_MISMATCH

    return ScoredCandidate(
        candidate=candidate,
        score=score,
        date_overlap=overlap,
        name_match=name_match,
    )


def pick_mazal_candidate(
    candidates: list[dict[str, Any]],
    *,
    marc_name: str | None = None,
    marc_dates: str | None = None,
    ms_year: int | None = None,
    role: str = "",
    limit: int = 8,
) -> MazalMatchDecision:
    """Pick one Mazal row or abstain when homonyms tie without date overlap."""
    if not candidates:
        return MazalMatchDecision(abstain=True, reason="no_candidates")

    scored = [
        score_mazal_candidate(
            c, marc_name=marc_name, marc_dates=marc_dates, ms_year=ms_year, role=role,
        )
        for c in candidates
    ]
    scored.sort(key=lambda s: (-s.score, str(s.candidate.get("mazal_id") or "")))
    top_n = scored[:limit]

    if len(scored) == 1:
        only = scored[0]
        # A lone candidate used to be returned with NO checks whatsoever — which is
        # the branch that matched the wrong Gabbai scribe. A name that does not
        # match needs corroboration from a tag-100 personality heading with an
        # overlapping date range, or the match abstains (Rule W-166).
        if only.name_match is False and not (
            str(only.candidate.get("main_marc_tag") or "") == "100"
            and only.date_overlap
        ):
            return MazalMatchDecision(
                abstain=True,
                reason="single_candidate_name_mismatch",
                top_n=top_n,
            )
        return MazalMatchDecision(
            winner=only.candidate,
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
