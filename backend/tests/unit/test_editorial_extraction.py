"""Editorial metadata extraction from MARC 500 notes."""
from __future__ import annotations

from app.pipeline.marc_ingest import (
    _collapse_marc_subfields,
    extract_named_entities,
)


def test_poppersh_editor_extracted() -> None:
    record: dict = {
        "500$a": (
            "בעריכת מאיר פופרש.עם הערות והוספות בשוליים ובסופו השערים."
            "במקומות אחדים הגהות \"צמח\"."
        ),
    }
    _collapse_marc_subfields(record)
    meta = record.get("editorial_metadata") or {}
    names = meta.get("editor_names") or []
    assert any("פופרש" in str(n) for n in names)
    assert "marginal_notes" in (meta.get("edition_features") or [])
    assert meta.get("has_imprint") is True


def test_editor_entity_emitted() -> None:
    record: dict = {
        "editorial_metadata": {
            "editor_names": ["מאיר פופרש"],
            "edition_features": [],
            "edition_statement": "",
            "has_imprint": False,
        },
    }
    entities = extract_named_entities(record)
    editors = [e for e in entities if e.get("role") == "editor"]
    assert editors
    assert "פופרש" in editors[0]["text"]
