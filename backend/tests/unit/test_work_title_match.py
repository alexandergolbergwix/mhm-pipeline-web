"""Tests for work title normalization and variant generation."""
from __future__ import annotations

from app.pipeline.work_title_match import (
    normalize_work_title_for_match,
    work_title_variants,
)


def test_normalize_strips_tafsif_prefix() -> None:
    assert normalize_work_title_for_match("תפסיל עת שערי רצון") == "עת שערי רצון"


def test_work_title_variants_includes_normalized() -> None:
    variants = work_title_variants("תפסיל עת שערי רצון")
    assert "תפסיל עת שערי רצון" in variants
    assert "עת שערי רצון" in variants


def test_work_title_variants_pulls_from_marc_record() -> None:
    record = {"work_mentions": [{"title": "שיר השירים", "source_field": "500"}]}
    variants = work_title_variants("other", record)
    assert "שיר השירים" in variants
