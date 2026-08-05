"""Rule W-166 — homonym scoring gains a name term, behind a flag.

`score_mazal_candidate` had no name-similarity term at all, so a candidate whose
given name belongs to a different person scored identically to the right one, and
`pick_mazal_candidate` returned a LONE candidate with no checks whatsoever. That
is the branch that matched Mazal 987007299516905171 (יצחק בן שלמה בן חיים גבאי) to
a manuscript whose scribe MARC names as גבאי, טוביה בן חיים יצחק.
"""

from __future__ import annotations

import pytest

from app.pipeline.homonym_scoring import (
    name_term_enabled,
    pick_mazal_candidate,
)
from app.pipeline.homonym_scoring import (
    score_mazal_candidate as _score,
)


def score_mazal_candidate(candidate, **kwargs):
    kwargs.setdefault("marc_dates", None)
    kwargs.setdefault("ms_year", None)
    kwargs.setdefault("role", "")
    return _score(candidate, **kwargs)

_MARC_SCRIBE = "גבאי, טוביה בן חיים יצחק"


@pytest.fixture
def _name_term_on(monkeypatch):
    monkeypatch.setenv("AUTHORITY_HOMONYM_NAME_TERM", "1")


def _candidate(pref_heb: str, **extra) -> dict:
    row = {
        "mazal_id": "987007299516905171",
        "preferred_name_heb": pref_heb,
        "main_marc_tag": "100",
        "dates": "",
    }
    row.update(extra)
    return row


class TestTheFlagDefaultsOff:
    def test_the_term_is_off_unless_asked_for(self, monkeypatch) -> None:
        """A corpus-wide matching change must be measured before it is trusted."""
        monkeypatch.delenv("AUTHORITY_HOMONYM_NAME_TERM", raising=False)
        assert name_term_enabled() is False

    def test_with_the_flag_off_scoring_is_unchanged(self, monkeypatch) -> None:
        monkeypatch.delenv("AUTHORITY_HOMONYM_NAME_TERM", raising=False)
        scored = score_mazal_candidate(
            _candidate("יצחק בן שלמה בן חיים גבאי"), marc_name=_MARC_SCRIBE,
        )
        assert scored.name_match is None
        decision = pick_mazal_candidate(
            [_candidate("יצחק בן שלמה בן חיים גבאי")], marc_name=_MARC_SCRIBE,
        )
        assert decision.winner is not None

    def test_the_flag_turns_it_on(self, _name_term_on) -> None:
        assert name_term_enabled() is True


class TestTheNameTerm:
    def test_a_mismatched_name_is_penalised(self, _name_term_on) -> None:
        wrong = score_mazal_candidate(
            _candidate("יצחק בן שלמה בן חיים גבאי"), marc_name=_MARC_SCRIBE,
        )
        right = score_mazal_candidate(
            _candidate("גבאי, טוביה בן חיים יצחק"), marc_name=_MARC_SCRIBE,
        )
        assert wrong.name_match is False
        assert right.name_match is True
        assert right.score > wrong.score

    def test_the_decision_is_visible_in_the_payload(self, _name_term_on) -> None:
        scored = score_mazal_candidate(
            _candidate("גבאי, טוביה בן חיים יצחק"), marc_name=_MARC_SCRIBE,
        )
        assert scored.as_payload_entry()["name_match"] is True

    def test_a_name_match_breaks_a_tie(self, _name_term_on) -> None:
        decision = pick_mazal_candidate(
            [
                _candidate("יצחק בן שלמה בן חיים גבאי", mazal_id="1"),
                _candidate("גבאי, טוביה בן חיים יצחק", mazal_id="2"),
            ],
            marc_name=_MARC_SCRIBE,
        )
        assert decision.winner is not None
        assert decision.winner["mazal_id"] == "2"


class TestTheSingleCandidateCheck:
    def test_a_lone_mismatched_candidate_abstains(self, _name_term_on) -> None:
        """The exact branch that shipped the wrong Gabbai."""
        decision = pick_mazal_candidate(
            [_candidate("יצחק בן שלמה בן חיים גבאי")], marc_name=_MARC_SCRIBE,
        )
        assert decision.abstain is True
        assert decision.reason == "single_candidate_name_mismatch"

    def test_a_lone_matching_candidate_still_wins(self, _name_term_on) -> None:
        decision = pick_mazal_candidate(
            [_candidate("גבאי, טוביה בן חיים יצחק")], marc_name=_MARC_SCRIBE,
        )
        assert decision.winner is not None
        assert decision.reason == "single_candidate"

    def test_a_lone_candidate_with_no_marc_name_still_wins(self, _name_term_on) -> None:
        """No heading to compare is not evidence of a mismatch."""
        decision = pick_mazal_candidate([_candidate("כל שם")])
        assert decision.winner is not None

    def test_a_tag_100_date_overlap_corroborates_a_name_mismatch(
        self, _name_term_on,
    ) -> None:
        """A transliteration we cannot compare should not lose a dated match."""
        decision = pick_mazal_candidate(
            [_candidate("Gabbai, Toviah", dates="1600-1660")],
            marc_name=_MARC_SCRIBE,
            marc_dates="1610-1655",
        )
        assert decision.winner is not None
