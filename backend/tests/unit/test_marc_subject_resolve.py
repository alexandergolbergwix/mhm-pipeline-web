"""Unit tests for MARC 650/655 → Wikidata QID resolution."""

from __future__ import annotations

import json
import pathlib

from converter.wikidata.marc_subject_resolve import (
    lookup_qid_by_label,
    resolve_genre_qid,
    resolve_subject_qid,
)


def test_static_subject_qid_english() -> None:
    qid = resolve_subject_qid(
        {"term": "Responsa", "type": "topic", "field": "650"},
        allow_network=False,
    )
    assert qid == "Q2112559"


def test_static_subject_qid_hebrew() -> None:
    qid = resolve_subject_qid(
        {"term": "מקרא", "type": "topic", "field": "650"},
        allow_network=False,
    )
    assert qid == "Q1845"


def test_stamped_wikidata_id_on_subject() -> None:
    qid = resolve_subject_qid(
        {"term": "Foo", "wikidata_id": "Q42", "type": "topic"},
        allow_network=False,
    )
    assert qid == "Q42"


def test_resolve_genre_qid_static() -> None:
    assert resolve_genre_qid("Commentaries", allow_network=False) == "Q1749541"


def test_lookup_qid_by_label_uses_cache(tmp_path: pathlib.Path) -> None:
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps({
            "version": 1,
            "entries": {
                "Kabbalah": {
                    "qid": "Q123006",
                    "fetched_at": "2099-01-01T00:00:00Z",
                    "ttl_seconds": 999999,
                }
            },
        }),
        encoding="utf-8",
    )
    assert lookup_qid_by_label("Kabbalah", allow_network=False, cache_path=cache) == "Q123006"
