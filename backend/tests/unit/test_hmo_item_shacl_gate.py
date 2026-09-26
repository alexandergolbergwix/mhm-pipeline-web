"""Unit tests for SHACL upload gate helpers."""

from __future__ import annotations

from app.pipeline.hmo_item_shacl_gate import (
    blocking_shacl_issues,
    compute_disambiguated_labels,
    compute_payload_label_keys,
    drop_descriptions_equal_to_labels,
    format_shacl_block_message,
    sanitize_wikibase_labels,
)


def test_blocking_shacl_issues_filters_warnings() -> None:
    issues = [
        {"severity": "Warning", "message": "soft"},
        {"severity": "Violation", "message": "hard"},
    ]
    blocked = blocking_shacl_issues(issues)
    assert len(blocked) == 1
    assert blocked[0]["message"] == "hard"


def test_format_shacl_block_message_joins_messages() -> None:
    msg = format_shacl_block_message(
        [
            {"message": "first"},
            {"message": "second"},
        ]
    )
    assert "first" in msg
    assert "second" in msg


def test_sanitize_wikibase_labels_drops_und() -> None:
    labels = sanitize_wikibase_labels({"und": "1001", "en": "1001"})
    assert "und" not in labels
    assert labels["en"] == "1001"


def test_drop_descriptions_equal_to_labels() -> None:
    labels = {"he": "תכלאל", "en": "Tiklal"}
    descriptions = {
        "he": "תכלאל",  # identical to the he label — Wikibase rejects it
        "en": "A Tiklal",  # distinct — stays
    }
    out, dropped = drop_descriptions_equal_to_labels(labels, descriptions)
    assert dropped == 1
    assert out == {"en": "A Tiklal"}


def test_drop_descriptions_equal_to_labels_keeps_distinct() -> None:
    labels = {"he": "מחזור"}
    descriptions = {"he": "מחזור  לפי מנהג"}  # different after strip
    out, dropped = drop_descriptions_equal_to_labels(labels, descriptions)
    assert dropped == 0
    assert out == descriptions


class _Entity:
    """Minimal resolved-entity stand-in for the disambiguation pass."""

    def __init__(self, local_id, labels, descriptions=None, control_numbers=None):
        self.local_id = local_id
        self.labels = labels
        self.descriptions = descriptions or {}
        self.control_numbers = control_numbers or []


def test_disambiguation_suffixes_only_the_second_claimant() -> None:
    first = _Entity("QDraft_B", {"en": "Mishnah"}, {"en": "a tractate"}, ["CN_B"])
    second = _Entity("QDraft_C", {"en": "Mishnah"}, {"en": "a tractate"}, ["CN_C"])
    overrides = compute_disambiguated_labels([second, first])  # unsorted input

    assert overrides == {"QDraft_C": {"en": "Mishnah — CN_C"}}


def test_disambiguation_keeps_distinct_descriptions() -> None:
    a = _Entity("QDraft_A", {"en": "Mishnah"}, {"en": "tractate beraḵot"}, ["CN_A"])
    b = _Entity("QDraft_B", {"en": "Mishnah"}, {"en": "tractate shabbat"}, ["CN_B"])
    assert compute_disambiguated_labels([a, b]) == {}


def test_disambiguation_falls_back_to_local_id_without_cn() -> None:
    a = _Entity("QDraft_A", {"en": "Mishnah"}, {"en": "x"})
    b = _Entity("QDraft_B", {"en": "Mishnah"}, {"en": "x"})
    overrides = compute_disambiguated_labels([a, b])
    assert overrides == {"QDraft_B": {"en": "Mishnah — QDraft_B"}}


def test_disambiguation_guards_against_cn_collisions() -> None:
    a = _Entity("QDraft_A", {"en": "Mishnah"}, {"en": "x"}, ["SAME_CN"])
    b = _Entity("QDraft_B", {"en": "Mishnah"}, {"en": "x"}, ["SAME_CN"])
    c = _Entity("QDraft_C", {"en": "Mishnah"}, {"en": "x"}, ["SAME_CN"])
    overrides = compute_disambiguated_labels([a, b, c])
    assert overrides["QDraft_B"] == {"en": "Mishnah — SAME_CN"}
    assert overrides["QDraft_C"] == {"en": "Mishnah — SAME_CN (2)"}


def test_disambiguation_pre_claimed_keys_suffix_even_the_first() -> None:
    """The wiki label space is global across runs: a key held by another
    run's mapped item blocks every newcomer, even the sorted-first."""
    foreign = _Entity("QDraft_Good", {"en": "Good condition"}, {"en": "state"}, ["CN_F"])
    newcomer = _Entity("QDraft_Good_2", {"en": "Good condition"}, {"en": "state"}, ["CN_N"])
    pre_claimed = compute_payload_label_keys(foreign)

    overrides = compute_disambiguated_labels([newcomer], pre_claimed=pre_claimed)

    assert overrides == {"QDraft_Good_2": {"en": "Good condition — CN_N"}}
