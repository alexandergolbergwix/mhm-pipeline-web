"""Guards for WikiProject Manuscripts–aligned Wikidata projection."""

from __future__ import annotations

from converter.wikidata.item_builder import WikidataItemBuilder
from converter.wikidata.property_mapping import (
    DISCOURAGED_MANUSCRIPT_P31,
    P_ANNOTATOR,
    P_COMMISSIONED_BY,
    Q_COMPOSITE_MANUSCRIPT,
    Q_MANUSCRIPT,
    Q_MANUSCRIPT_FRAGMENT,
    Q_UNKNOWN_TEXT,
    ROLE_TO_PID,
)


def test_role_map_has_annotator_and_commissioner() -> None:
    assert ROLE_TO_PID["annotator"] == P_ANNOTATOR
    assert ROLE_TO_PID["commissioner"] == P_COMMISSIONED_BY
    assert ROLE_TO_PID["patron"] == P_COMMISSIONED_BY
    assert "editor" not in ROLE_TO_PID
    assert "compiler" not in ROLE_TO_PID


def test_anthology_uses_composite_not_codex() -> None:
    builder = WikidataItemBuilder()
    qids = builder._determine_instance_type({
        "is_anthology": True,
        "genres": [],
    })
    assert Q_COMPOSITE_MANUSCRIPT in qids
    assert Q_MANUSCRIPT in qids
    assert not DISCOURAGED_MANUSCRIPT_P31.intersection(qids)
    assert "Q213924" not in qids


def test_fragment_condition_note_sets_manuscript_fragment_p31() -> None:
    builder = WikidataItemBuilder()
    qids = builder._determine_instance_type({
        "condition_notes": ["קטע פגום"],
        "genres": [],
    })
    assert Q_MANUSCRIPT_FRAGMENT in qids


def test_anonymous_author_never_emits_manuscript_p50() -> None:
    builder = WikidataItemBuilder()
    item = builder.build_manuscript_item({
        "_control_number": "990000403370205171",
        "title": "פירוש",
        "shelfmark": "Ms. Heb. 8°1",
        "authors": [],
        "marc_authority_matches": [
            {"name": "Anonymous", "role": "author"},
        ],
        "entities": [],
        "contents": [],
        "languages": ["heb"],
    })
    assert all(s.property_id != "P50" for s in item.statements)


def test_p1574_carries_object_named_as() -> None:
    builder = WikidataItemBuilder()
    item = builder.build_manuscript_item({
        "_control_number": "990000403370205171",
        "title": "קובץ",
        "shelfmark": "Ms. Heb. 8°2",
        "languages": ["heb"],
        "contents": [
            {
                "title": "משנה תורה",
                "source_field": "505",
                "folio_range": "1r-10v",
                "sequence": 1,
            }
        ],
        "marc_authority_matches": [],
        "entities": [],
        "authors": [],
    })
    exemplars = [s for s in item.statements if s.property_id == "P1574"]
    assert exemplars
    for stmt in exemplars:
        props = {q.get("property") for q in (stmt.qualifiers or []) if isinstance(q, dict)}
        assert "P1932" in props
        assert "P958" in props or stmt.value == Q_UNKNOWN_TEXT or str(stmt.value).startswith("__LOCAL:")
