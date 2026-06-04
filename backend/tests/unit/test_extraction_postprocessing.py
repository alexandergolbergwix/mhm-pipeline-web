"""Unit tests for extraction entity post-processing in _group_entity_rows.

Covers:
  Fix 3 — Intra-record entity deduplication
  Fix 4 — Noise / non-person entity filtering
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest


# ── Minimal stub to stand in for an ExtractionApproval ORM row ───────────────

@dataclass
class _Row:
    control_number: str
    text: str
    type: str
    role: str | None = None
    source: str = "person_ner"
    start: int = 0
    end: int = 0
    confidence: float = 0.9
    model_confidence: float = 0.9
    override_text: str | None = None
    override_type: str | None = None
    override_role: str | None = None
    approved: bool = True


def _group(rows: list[_Row], approved_only: bool = False) -> dict[str, list[dict[str, Any]]]:
    from app.routers.wikidata_studio import _group_entity_rows
    return _group_entity_rows(rows, approved_only)


# ── Fix 3: Deduplication ──────────────────────────────────────────────────────

class TestDeduplication:
    def test_exact_duplicate_same_role_removed(self) -> None:
        rows = [
            _Row("cn1", "אלעזר אזכרי", "PERSON", "AUTHOR"),
            _Row("cn1", "אלעזר אזכרי", "PERSON", "AUTHOR"),
            _Row("cn1", "אלעזר אזכרי", "PERSON", "AUTHOR"),
        ]
        result = _group(rows)
        assert len(result["cn1"]) == 1

    def test_same_name_different_roles_kept(self) -> None:
        rows = [
            _Row("cn1", "אלעזר אזכרי", "PERSON", "AUTHOR"),
            _Row("cn1", "אלעזר אזכרי", "PERSON", "TRANSCRIBER"),
        ]
        result = _group(rows)
        assert len(result["cn1"]) == 2

    def test_different_names_kept(self) -> None:
        rows = [
            _Row("cn1", "משה", "PERSON", "AUTHOR"),
            _Row("cn1", "אהרן", "PERSON", "AUTHOR"),
        ]
        result = _group(rows)
        assert len(result["cn1"]) == 2

    def test_dedup_across_different_records_independent(self) -> None:
        rows = [
            _Row("cn1", "משה", "PERSON", "AUTHOR"),
            _Row("cn1", "משה", "PERSON", "AUTHOR"),
            _Row("cn2", "משה", "PERSON", "AUTHOR"),
        ]
        result = _group(rows)
        assert len(result["cn1"]) == 1
        assert len(result["cn2"]) == 1

    def test_case_insensitive_dedup(self) -> None:
        rows = [
            _Row("cn1", "Moses Gaster", "PERSON", "AUTHOR"),
            _Row("cn1", "moses gaster", "PERSON", "AUTHOR"),
        ]
        result = _group(rows)
        assert len(result["cn1"]) == 1


# ── Fix 4: Noise filter ───────────────────────────────────────────────────────

class TestNoiseFilter:
    def test_known_non_person_dropped(self) -> None:
        rows = [_Row("cn1", "הסוחר", "PERSON", "OWNER", confidence=0.39)]
        result = _group(rows)
        assert "cn1" not in result or len(result.get("cn1", [])) == 0

    def test_nefesh_dropped(self) -> None:
        rows = [_Row("cn1", "נפש", "PERSON", "OWNER", confidence=0.50)]
        result = _group(rows)
        assert "cn1" not in result or len(result.get("cn1", [])) == 0

    def test_single_token_low_conf_dropped(self) -> None:
        rows = [_Row("cn1", "שלמה", "PERSON", "AUTHOR", confidence=0.30)]
        result = _group(rows)
        assert "cn1" not in result or len(result.get("cn1", [])) == 0

    def test_single_token_above_threshold_kept(self) -> None:
        rows = [_Row("cn1", "שלמה", "PERSON", "AUTHOR", confidence=0.85)]
        result = _group(rows)
        assert len(result["cn1"]) == 1

    def test_multi_token_low_conf_kept(self) -> None:
        # Multi-token names are kept even at low confidence (name is specific enough)
        rows = [_Row("cn1", "משה בן מימון", "PERSON", "AUTHOR", confidence=0.25)]
        result = _group(rows)
        assert len(result["cn1"]) == 1

    def test_non_person_type_not_filtered(self) -> None:
        # Noise filter only applies to PERSON type; GENRE/WORK/FOLIO pass through
        rows = [_Row("cn1", "הסוחר", "GENRE", None, confidence=0.39)]
        result = _group(rows)
        assert len(result["cn1"]) == 1

    def test_approved_only_flag_respected(self) -> None:
        rows = [
            _Row("cn1", "משה גסטר", "PERSON", "AUTHOR", approved=True),
            _Row("cn1", "אהרן", "PERSON", "AUTHOR", approved=False),
        ]
        result = _group(rows, approved_only=True)
        assert len(result["cn1"]) == 1
        assert result["cn1"][0]["text"] == "משה גסטר"
