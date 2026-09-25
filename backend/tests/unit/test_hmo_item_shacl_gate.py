"""Unit tests for SHACL upload gate helpers."""

from __future__ import annotations

from app.pipeline.hmo_item_shacl_gate import (
    blocking_shacl_issues,
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
    msg = format_shacl_block_message([
        {"message": "first"},
        {"message": "second"},
    ])
    assert "first" in msg
    assert "second" in msg


def test_sanitize_wikibase_labels_drops_und() -> None:
    labels = sanitize_wikibase_labels({"und": "1001", "en": "1001"})
    assert "und" not in labels
    assert labels["en"] == "1001"


def test_drop_descriptions_equal_to_labels() -> None:
    labels = {"he": "תכלאל", "en": "Tiklal"}
    descriptions = {
        "he": "תכלאל",       # identical to the he label — Wikibase rejects it
        "en": "A Tiklal",     # distinct — stays
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
