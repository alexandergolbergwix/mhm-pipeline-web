"""Unit tests for MARC 650/655 → Wikidata QID resolution."""

from __future__ import annotations

import json
import pathlib

from converter.wikidata.marc_subject_resolve import (
    lookup_qid_by_label,
    resolve_genre_qid,
    resolve_subject_qid,
)
from converter.wikidata.property_labels import QID_LABELS
from converter.wikidata.property_mapping import GENRE_TO_QID, SUBJECT_TO_QID


def test_static_subject_qid_english() -> None:
    qid = resolve_subject_qid(
        {"term": "Responsa", "type": "topic", "field": "650"},
        allow_network=False,
    )
    assert qid == "Q3427762"


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


def test_all_static_genre_and_subject_qids_have_verified_labels() -> None:
    emitted_qids = set(GENRE_TO_QID.values()) | set(SUBJECT_TO_QID.values())
    assert emitted_qids <= QID_LABELS.keys()


def test_known_corrupt_crosswalk_qids_are_not_emitted() -> None:
    emitted_qids = set(GENRE_TO_QID.values()) | set(SUBJECT_TO_QID.values())
    corrupt = {
        "Q1377011", "Q3089066", "Q177038", "Q752001", "Q7197095",
        "Q189539", "Q12378", "Q207128", "Q3412432", "Q2112559",
        "Q1207", "Q173579", "Q131748", "Q328079", "Q217535",
        "Q575696", "Q204819", "Q168529", "Q132834", "Q179723",
        "Q37602", "Q6867684",
    }
    assert emitted_qids.isdisjoint(corrupt)


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
