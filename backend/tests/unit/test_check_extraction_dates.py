"""Tests for scripts.check_extraction_dates."""
from __future__ import annotations

from scripts.check_extraction_dates import (
    audit_extraction_dates,
    parse_date_entity_text,
)


def test_parse_hebrew_provenance_date() -> None:
    parsed = parse_date_entity_text('משנת הת"ר')
    assert parsed.gregorian_year == 1845
    assert parsed.parse_method == "normalized_yyyy"


def test_parse_gregorian_span() -> None:
    parsed = parse_date_entity_text("בשנת 1648")
    assert parsed.gregorian_year == 1648


def test_parse_short_numeric_flags_warning() -> None:
    parsed = parse_date_entity_text("15")
    assert parsed.gregorian_year is None
    assert "unparsed" in parsed.warnings


def test_audit_dedupes_duplicate_rows() -> None:
    entities = [
        {
            "control_number": "990000864590205171",
            "type": "DATE",
            "text": "בשנת 1648",
            "source": "provenance_ner",
            "confidence": 0.99,
            "approved": False,
            "ai_verdict_overall": None,
        },
        {
            "control_number": "990000864590205171",
            "type": "DATE",
            "text": "בשנת 1648",
            "source": "provenance_ner",
            "confidence": 0.99,
            "approved": False,
            "ai_verdict_overall": None,
        },
    ]
    rows = audit_extraction_dates(entities)
    assert len(rows) == 1
    assert rows[0].entity_count == 2
    assert "duplicate_entity_rows" in rows[0].flags
