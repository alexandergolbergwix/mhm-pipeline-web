"""Tests for ``MarcStructuredIndex`` — novelty detection + classify.

Ported from the desktop pipeline's
``tests/unit/gui/dialogs/widgets/test_marc_structured_index.py`` with
imports adjusted for the new web-backend module path. Adds tests for
the new :meth:`MarcStructuredIndex.classify` method covering all four
status values (grounded, wrong_field, novel, unknown).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.pipeline.marc_structured_index import (
    MarcStructuredIndex,
    _normalise,
    _record_key,
)


class TestNormalise:
    def test_casefold_and_punctuation(self) -> None:
        assert _normalise("Maimonides,") == "maimonides"
        assert _normalise("  Yosef ben Efrayim  ") == "yosef ben efrayim"

    def test_hebrew_preserved(self) -> None:
        assert _normalise("בירב, יעקב") == "בירב יעקב"

    def test_empty_inputs(self) -> None:
        assert _normalise("") == ""
        assert _normalise("   ") == ""


class TestRecordKey:
    def test_uri_last_segment(self) -> None:
        assert _record_key("https://example/manuscript/990001") == "990001"
        assert _record_key("990001") == "990001"
        assert _record_key("") == ""


class TestIndexLoad:
    def _write(self, path: Path, records: list[dict]) -> Path:
        out = path / "marc_extracted.json"
        out.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        return out

    def test_missing_file_yields_empty_index(self, tmp_path: Path) -> None:
        idx = MarcStructuredIndex.load(tmp_path / "nope.json")
        assert len(idx) == 0
        assert idx.is_novel("anything", "anything") is False

    def test_indexes_structured_fields(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            [
                {
                    "_control_number": "990001",
                    "contributors": [{"name": "Maimonides", "role": "author"}],
                    "subjects": [{"term": "Responsa", "type": "topic"}],
                    "title": "Mishneh Torah",
                    "genre_form": [],
                },
            ],
        )
        idx = MarcStructuredIndex.load(path)
        assert len(idx) == 1
        assert idx.has("990001") is True
        assert idx.has("https://x/manuscript/990001") is True

    def test_malformed_json_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "marc_extracted.json"
        path.write_text("{not json", encoding="utf-8")
        idx = MarcStructuredIndex.load(path)
        assert len(idx) == 0


class TestFromRecords:
    def test_from_records_round_trips(self) -> None:
        idx = MarcStructuredIndex.from_records([
            {
                "_control_number": "990001",
                "contributors": [{"name": "Maimonides"}],
                "title": "Mishneh Torah",
            },
        ])
        assert idx.has("990001") is True
        assert idx.is_novel("990001", "Maimonides") is False
        assert idx.is_novel("990001", "Some Other Scribe") is True


class TestIsNovel:
    @pytest.fixture
    def index(self, tmp_path: Path) -> MarcStructuredIndex:
        path = tmp_path / "marc_extracted.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "_control_number": "990001",
                        "contributors": [
                            {"name": "Maimonides, Moses", "role": "author"},
                            {"name": "Karo, Yosef ben Efrayim", "role": "author"},
                        ],
                        "subjects": [{"term": "Responsa", "type": "topic"}],
                        "title": "Mishneh Torah",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return MarcStructuredIndex.load(path)

    def test_known_name_not_novel(self, index: MarcStructuredIndex) -> None:
        assert index.is_novel("990001", "Maimonides, Moses") is False

    def test_substring_of_known_value_not_novel(self, index: MarcStructuredIndex) -> None:
        assert index.is_novel("990001", "Maimonides") is False

    def test_known_value_substring_of_candidate_not_novel(
        self, index: MarcStructuredIndex,
    ) -> None:
        assert index.is_novel("990001", "Moses Maimonides ben Maimon") is False

    def test_truly_novel_name(self, index: MarcStructuredIndex) -> None:
        assert index.is_novel("990001", "Some Other Scribe") is True

    def test_unknown_record_safe_default_false(self, index: MarcStructuredIndex) -> None:
        assert index.is_novel("999999", "anything") is False

    def test_empty_candidate_not_novel(self, index: MarcStructuredIndex) -> None:
        assert index.is_novel("990001", "") is False

    def test_uri_record_id_resolves(self, index: MarcStructuredIndex) -> None:
        assert (
            index.is_novel("https://nli/manuscript/990001", "Some Other Scribe")
            is True
        )


class TestClassify:
    """Cover the four status values of :meth:`classify` end-to-end."""

    @pytest.fixture
    def index(self, tmp_path: Path) -> MarcStructuredIndex:
        path = tmp_path / "marc_extracted.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "_control_number": "990001",
                        "contributors": [
                            {"name": "Maimonides, Moses", "role": "author"},
                        ],
                        "former_owners": [{"name": "Bodleian Library"}],
                        "title": "Mishneh Torah",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return MarcStructuredIndex.load(path)

    def test_unknown_record(self, index: MarcStructuredIndex) -> None:
        out = index.classify("999999", "anything", candidate_type="person")
        assert out["status"] == "unknown"
        assert out["fields"] == []

    def test_grounded_when_candidate_matches_expected_field(
        self, index: MarcStructuredIndex,
    ) -> None:
        out = index.classify("990001", "Maimonides", candidate_type="person")
        assert out["status"] == "grounded"
        assert "contributors" in out["fields"]

    def test_wrong_field_when_match_is_in_unexpected_field(
        self, index: MarcStructuredIndex,
    ) -> None:
        out = index.classify(
            "990001", "Maimonides", candidate_type="provenance_ner",
        )
        assert out["status"] == "wrong_field"
        assert "contributors" in out["fields"]

    def test_novel_when_not_in_any_field(self, index: MarcStructuredIndex) -> None:
        out = index.classify(
            "990001", "Some Other Scribe", candidate_type="person",
        )
        assert out["status"] == "novel"
        assert out["fields"] == []

    def test_unknown_type_defaults_to_grounded_on_any_match(
        self, index: MarcStructuredIndex,
    ) -> None:
        out = index.classify("990001", "Maimonides", candidate_type=None)
        assert out["status"] == "grounded"
