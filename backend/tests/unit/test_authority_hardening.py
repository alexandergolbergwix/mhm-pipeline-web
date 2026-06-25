"""Unit tests for the seven authority-hardening guards.

Each guard has at least one positive (fires) and one negative (stays
silent) test. The orchestrator round-trip is exercised separately so
the contract between the guards and the candidate payload is pinned.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.pipeline import authority_hardening as ah


# ── Guard 1 — short-name homonym ────────────────────────────────────────


class TestShortNameHomonym:
    def test_fires_for_single_token_marc_against_rich_cluster(self) -> None:
        v = ah.guard_short_name_homonym(
            marc_name="יעקב",
            preferred_name_lat="Jacob ben Asher of Toledo",
            mazal_matched=False,
            biographical_dates_present=False,
        )
        assert v.fired is True
        assert v.new_confidence == "low"
        assert v.flag == "short_name_homonym"

    def test_silent_when_mazal_personality_anchors_the_match(self) -> None:
        v = ah.guard_short_name_homonym(
            marc_name="יעקב",
            preferred_name_lat="Jacob ben Asher of Toledo",
            mazal_matched=True,
            biographical_dates_present=False,
            payload={"main_marc_tag": "100", "personality_count": 1},
        )
        assert v.fired is False

    def test_fires_when_mazal_hit_is_subject_not_personality(self) -> None:
        v = ah.guard_short_name_homonym(
            marc_name="יעקב",
            preferred_name_lat="Jacob ben Asher of Toledo",
            mazal_matched=True,
            biographical_dates_present=False,
            payload={"main_marc_tag": "150", "personality_count": 3},
        )
        assert v.fired is True

    def test_silent_when_marc_already_multi_token(self) -> None:
        v = ah.guard_short_name_homonym(
            marc_name="יעקב בן אשר",
            preferred_name_lat="Jacob ben Asher of Toledo",
            mazal_matched=False,
            biographical_dates_present=False,
        )
        assert v.fired is False


# ── Guard 2 — placeholder name ──────────────────────────────────────────


class TestPlaceholderName:
    @pytest.mark.parametrize(
        "name",
        ["א\"א", "N.N.", "Anonymous", "מחבר אלמוני", "M. J.", "A. B."],
    )
    def test_fires_on_placeholders(self, name: str) -> None:
        v = ah.guard_placeholder_name(name=name)
        assert v.fired is True
        assert v.flag == "placeholder_name"

    def test_silent_on_real_name(self) -> None:
        v = ah.guard_placeholder_name(name="משה בן מימון")
        assert v.fired is False


# ── Guard 3 — cluster collapse ──────────────────────────────────────────


class TestClusterCollapse:
    def test_fires_when_two_distinct_names_share_a_cluster(self) -> None:
        me = {"matched_name": "יעקב", "viaf_id": "12345"}
        siblings = [
            {"matched_name": "אברהם", "viaf_id": "12345"},
        ]
        v = ah.guard_cluster_collapse(candidate=me, siblings=siblings)
        assert v.fired is True
        assert v.new_confidence == "low"
        assert v.flag == "cluster_collapse"

    def test_silent_when_only_one_name_with_the_cluster(self) -> None:
        me = {"matched_name": "יעקב", "viaf_id": "12345"}
        siblings = [
            {"matched_name": "אברהם", "viaf_id": "99999"},
        ]
        v = ah.guard_cluster_collapse(candidate=me, siblings=siblings)
        assert v.fired is False

    def test_silent_when_same_name_appears_twice(self) -> None:
        me = {"matched_name": "יעקב", "viaf_id": "12345"}
        siblings = [
            {"matched_name": "יעקב", "viaf_id": "12345"},
        ]
        v = ah.guard_cluster_collapse(candidate=me, siblings=siblings)
        assert v.fired is False

    def test_silent_when_candidate_has_no_viaf(self) -> None:
        me = {"matched_name": "יעקב", "viaf_id": ""}
        siblings = [{"matched_name": "אברהם", "viaf_id": "12345"}]
        v = ah.guard_cluster_collapse(candidate=me, siblings=siblings)
        assert v.fired is False


# ── Guard 4 — NLI strict / VIAF skip ────────────────────────────────────


class TestNliStrictSkipViaf:
    def test_fires_on_pipeline_range_qid_with_mazal_and_viaf(self) -> None:
        me: dict[str, Any] = {
            "mazal_id": "987007414776605171",
            "viaf_id": "12345",
            "wikidata_qid": "Q139094451",
        }
        v = ah.guard_nli_strict_skip_viaf(candidate=me)
        assert v.fired is True
        assert v.new_confidence == "medium"
        assert v.flag == "nli_strict_skip_viaf"

    def test_silent_on_canonical_low_qid(self) -> None:
        me: dict[str, Any] = {
            "mazal_id": "987007414776605171",
            "viaf_id": "12345",
            "wikidata_qid": "Q189564",
        }
        v = ah.guard_nli_strict_skip_viaf(candidate=me)
        assert v.fired is False

    def test_silent_when_mazal_or_viaf_missing(self) -> None:
        me: dict[str, Any] = {
            "mazal_id": "",
            "viaf_id": "12345",
            "wikidata_qid": "Q139094451",
        }
        v = ah.guard_nli_strict_skip_viaf(candidate=me)
        assert v.fired is False


# ── Guard 5 — Wikidata cross-check ──────────────────────────────────────


class _FakeWdResult:
    def __init__(
        self, qids: list[str], hebrew_labels: list[str],
        birth_years: list[int], death_years: list[int],
        occupations: list[str], error: str | None = None,
    ) -> None:
        self.qids = qids
        self.hebrew_labels = hebrew_labels
        self.birth_years = birth_years
        self.death_years = death_years
        self.occupations = occupations
        self.error = error


class _FakeOverMergeTable:
    def __init__(self, result: _FakeWdResult) -> None:
        self._result = result

    def get(self, viaf_id: str) -> _FakeWdResult:  # noqa: ARG002
        return self._result


class TestWikidataCrosscheck:
    def test_fires_on_overmerge_signal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Two QIDs, two different birth years → over-merged.
        result = _FakeWdResult(
            qids=["Q1", "Q2"],
            hebrew_labels=["יעקב", "יצחק"],
            birth_years=[1100, 1300],
            death_years=[],
            occupations=[],
        )
        # Force the cross-check module to report enabled.
        from converter.authority import wikidata_crosscheck as wcc

        monkeypatch.setattr(wcc, "is_enabled", lambda: True)
        v = ah.guard_wikidata_crosscheck(
            marc_name="יעקב",
            candidate={"viaf_id": "12345"},
            over_merge_table=_FakeOverMergeTable(result),
        )
        assert v.fired is True
        assert v.flag == "wikidata_crosscheck_fail"
        assert v.new_confidence == "low"

    def test_silent_when_no_viaf(self) -> None:
        v = ah.guard_wikidata_crosscheck(
            marc_name="יעקב",
            candidate={"viaf_id": ""},
        )
        assert v.fired is False

    def test_silent_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from converter.authority import wikidata_crosscheck as wcc

        monkeypatch.setattr(wcc, "is_enabled", lambda: False)
        v = ah.guard_wikidata_crosscheck(
            marc_name="יעקב",
            candidate={"viaf_id": "12345"},
        )
        assert v.fired is False


# ── Guard 6 — Mazal-pair collision ──────────────────────────────────────


class TestMazalPairCollision:
    def test_fires_on_two_distinct_pairs_sharing_viaf(self) -> None:
        me = {
            "matched_name": "יעקב",
            "viaf_id": "12345",
            "mazal_id": "MAZAL_A",
        }
        siblings = [
            {
                "matched_name": "אברהם",
                "viaf_id": "12345",
                "mazal_id": "MAZAL_B",
            },
        ]
        v = ah.guard_mazal_pair_collision(candidate=me, siblings=siblings)
        assert v.fired is True
        assert v.flag == "mazal_pair_collision"
        assert v.new_confidence == "low"

    def test_silent_when_same_mazal_id_on_same_cluster(self) -> None:
        me = {
            "matched_name": "יעקב",
            "viaf_id": "12345",
            "mazal_id": "MAZAL_A",
        }
        siblings = [
            {
                "matched_name": "יעקב",
                "viaf_id": "12345",
                "mazal_id": "MAZAL_A",
            },
        ]
        v = ah.guard_mazal_pair_collision(candidate=me, siblings=siblings)
        assert v.fired is False

    def test_silent_when_no_mazal_id_present(self) -> None:
        me = {"matched_name": "יעקב", "viaf_id": "12345", "mazal_id": ""}
        siblings = [
            {"matched_name": "אברהם", "viaf_id": "12345", "mazal_id": "MAZAL_B"},
        ]
        v = ah.guard_mazal_pair_collision(candidate=me, siblings=siblings)
        assert v.fired is False


# ── Guard 7 — corporate / meeting ───────────────────────────────────────


class TestCorporateMeeting:
    def test_fires_when_org_carries_person_viaf(self) -> None:
        me = {
            "matched_name": "Bodleian Library",
            "viaf_id": "12345",
            "entity_kind": "organization",
            "payload": {"viaf_name_type": "Personal"},
        }
        v = ah.guard_corporate_meeting(candidate=me, entity_kind="organization")
        assert v.fired is True
        assert v.flag == "corporate_viaf_drop"
        assert v.new_confidence == "low"

    def test_silent_when_org_carries_corporate_viaf(self) -> None:
        me = {
            "matched_name": "Bodleian Library",
            "viaf_id": "12345",
            "entity_kind": "organization",
            "payload": {"viaf_name_type": "Corporate", "viaf_resolve_op": "sru_corporate"},
        }
        v = ah.guard_corporate_meeting(candidate=me, entity_kind="organization")
        assert v.fired is False

    def test_silent_for_person_with_viaf(self) -> None:
        me = {
            "matched_name": "משה בן מימון",
            "viaf_id": "12345",
            "entity_kind": "person",
        }
        v = ah.guard_corporate_meeting(candidate=me, entity_kind="person")
        assert v.fired is False

    def test_silent_for_meeting_without_viaf(self) -> None:
        me = {
            "matched_name": "World Hebrew Congress",
            "viaf_id": "",
            "entity_kind": "meeting",
        }
        v = ah.guard_corporate_meeting(candidate=me, entity_kind="meeting")
        assert v.fired is False


class TestViafNameTypeMismatch:
    def test_personal_viaf_on_place_stripped(self) -> None:
        me = {
            "matched_name": "ירושלים",
            "viaf_id": "999",
            "entity_kind": "place",
            "payload": {"viaf_name_type": "Personal"},
        }
        v = ah.guard_viaf_name_type_mismatch(candidate=me, entity_kind="place")
        assert v.fired is True
        assert v.flag == "viaf_person_on_non_person"


class TestWikidataHumanOnNonPerson:
    def test_fires_on_q5_for_work(self) -> None:
        me = {
            "matched_name": "תלמוד בבלי",
            "wikidata_qid": "Q5",
            "entity_kind": "work",
            "payload": {},
        }
        v = ah.guard_wikidata_human_on_non_person(candidate=me, entity_kind="work")
        assert v.fired is True
        assert v.flag == "wikidata_human_on_non_person"


class TestWikidataOrphanLabel:
    def test_label_without_anchor_fires(self) -> None:
        me = {
            "matched_name": "תלמוד בבלי",
            "wikidata_qid": "Q192043",
            "entity_kind": "work",
            "payload": {"wikidata_resolve_op": "label"},
        }
        v = ah.guard_wikidata_orphan_label(candidate=me, entity_kind="work")
        assert v.fired is True
        assert v.flag == "wikidata_orphan_label"

    def test_label_with_mazal_anchor_silent(self) -> None:
        me = {
            "matched_name": "תלמוד בבלי",
            "mazal_id": "MAZAL_1",
            "wikidata_qid": "Q192043",
            "entity_kind": "work",
            "payload": {"wikidata_resolve_op": "label"},
        }
        v = ah.guard_wikidata_orphan_label(candidate=me, entity_kind="work")
        assert v.fired is False


# ── Orchestrator round-trip ─────────────────────────────────────────────


class TestApplyHardeningGuards:
    def test_idempotent_no_op_for_clean_candidate(self) -> None:
        me: dict[str, Any] = {
            "matched_name": "משה בן מימון הספרדי",
            "entity_kind": "person",
            "confidence": "high",
            "mazal_id": "987007414776605171",
            "viaf_id": "",
            "wikidata_qid": "Q189564",
            "payload": {},
        }
        out = ah.apply_hardening_guards(me)
        assert out["confidence"] == "high"
        assert out["payload"]["guard_flags"] == []
        # Idempotent
        out2 = ah.apply_hardening_guards(out)
        assert out2["confidence"] == "high"
        assert out2["payload"]["guard_flags"] == []

    def test_placeholder_clears_ids(self) -> None:
        me: dict[str, Any] = {
            "matched_name": "N.N.",
            "entity_kind": "person",
            "confidence": "medium",
            "mazal_id": "MAZAL_X",
            "viaf_id": "12345",
            "wikidata_qid": "Q1",
            "payload": {"gnd_id": "GND_X"},
        }
        out = ah.apply_hardening_guards(me)
        assert out["confidence"] == "low"
        assert "placeholder_name" in out["payload"]["guard_flags"]
        assert out["mazal_id"] == ""
        assert out["viaf_id"] == ""
        assert out["wikidata_qid"] == ""
        assert "gnd_id" not in out["payload"]

    def test_corporate_drops_person_viaf(self) -> None:
        me: dict[str, Any] = {
            "matched_name": "Bodleian Library",
            "entity_kind": "organization",
            "confidence": "high",
            "mazal_id": "",
            "viaf_id": "12345",
            "wikidata_qid": "",
            "payload": {
                "viaf_uri": "https://viaf.org/viaf/12345",
                "viaf_name_type": "Personal",
                "isni": "0001",
            },
        }
        out = ah.apply_hardening_guards(
            me, context=ah.HardeningContext(entity_kind="organization"),
        )
        assert out["viaf_id"] == ""
        assert "corporate_viaf_drop" in out["payload"]["guard_flags"]
        assert out["confidence"] == "low"

    def test_corporate_keeps_corporate_viaf(self) -> None:
        me: dict[str, Any] = {
            "matched_name": "Bodleian Library",
            "entity_kind": "organization",
            "confidence": "high",
            "mazal_id": "MAZAL_X",
            "viaf_id": "12345",
            "wikidata_qid": "",
            "payload": {
                "viaf_name_type": "Corporate",
                "viaf_resolve_op": "sru_corporate",
            },
        }
        out = ah.apply_hardening_guards(
            me, context=ah.HardeningContext(entity_kind="organization"),
        )
        assert out["viaf_id"] == "12345"
        assert "corporate_viaf_drop" not in out["payload"]["guard_flags"]

    def test_cluster_collapse_via_siblings(self) -> None:
        me: dict[str, Any] = {
            "matched_name": "יעקב",
            "entity_kind": "person",
            "confidence": "high",
            "mazal_id": "",
            "viaf_id": "12345",
            "wikidata_qid": "",
            "payload": {},
        }
        siblings = [
            {"matched_name": "אברהם", "viaf_id": "12345", "mazal_id": ""},
        ]
        out = ah.apply_hardening_guards(
            me, context=ah.HardeningContext(siblings=siblings),
        )
        assert out["confidence"] == "low"
        assert "cluster_collapse" in out["payload"]["guard_flags"]


# ── Helper coverage ─────────────────────────────────────────────────────


class TestHelpers:
    @pytest.mark.parametrize(
        "candidate,expected",
        [
            ({"viaf_id": "12345"}, "12345"),
            ({"viaf_id": "https://viaf.org/viaf/12345"}, "12345"),
            ({"payload": {"viaf_uri": "https://viaf.org/viaf/77"}}, "77"),
            ({}, ""),
        ],
    )
    def test_viaf_id_from(
        self, candidate: dict[str, Any], expected: str,
    ) -> None:
        assert ah._viaf_id_from(candidate) == expected

    def test_lower_confidence(self) -> None:
        assert ah._lower_confidence("high", "medium") == "medium"
        assert ah._lower_confidence("medium", "high") == "medium"
        assert ah._lower_confidence("low", "high") == "low"
        assert ah._lower_confidence("high", None) == "high"
