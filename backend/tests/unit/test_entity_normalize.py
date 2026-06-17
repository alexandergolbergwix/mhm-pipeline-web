"""Tests for entity text and role normalisation."""
from __future__ import annotations

from app.pipeline.entity_normalize import (
    normalize_entity_key,
    normalize_entity_text,
    normalize_role,
    normalize_role_key,
)
from app.pipeline.marc_ingest import extract_named_entities


def test_normalize_entity_text_strips_wrapping_quotes() -> None:
    assert normalize_entity_text('"Allony, Nehemia"') == "Allony, Nehemia"


def test_normalize_entity_text_strips_stray_trailing_quote() -> None:
    assert normalize_entity_text('Allony, Nehemia"') == "Allony, Nehemia"


def test_normalize_role_hebrew_former_owners_plural() -> None:
    assert normalize_role("בעלים קודמים") == "former owner"


def test_normalize_role_english_former_owner() -> None:
    assert normalize_role("former owner") == "former owner"
    assert normalize_role('former owner"') == "former owner"


def test_normalize_role_key_stable() -> None:
    assert normalize_role_key("בעלים קודמים") == "former_owner"
    assert normalize_role_key("former owner") == "former_owner"


def test_hebrew_and_english_former_owner_share_role_key() -> None:
    assert normalize_role_key("בעלים קודמים") == normalize_role_key("former owner")


def test_dedup_merges_quote_variants_same_role() -> None:
    record = {
        "contributors": [
            {"name": '"Allony, Nehemia"', "role": "former owner", "field": "700"},
            {"name": 'Allony, Nehemia"', "role": 'former owner"', "field": "700"},
        ],
    }
    entities = extract_named_entities(record)
    allony = [e for e in entities if "allony" in normalize_entity_key(e["text"])]
    assert len(allony) == 1
    assert allony[0]["role"] == "former owner"


def test_dedup_merges_hebrew_and_english_former_owner_roles() -> None:
    record = {
        "contributors": [
            {"name": "זנגר, יוסף", "role": "בעלים קודמים", "field": "700"},
            {"name": '"זנגר, יוסף"', "role": "former owner", "field": "700"},
        ],
    }
    entities = extract_named_entities(record)
    zanger = [e for e in entities if "זנגר" in e["text"]]
    assert len(zanger) == 1
    assert zanger[0]["role"] == "former owner"


def test_dedup_merges_place_and_production_place() -> None:
    record = {
        "place": "Mikulov (Jihomoravský kraj, Czech Republic)",
        "related_places": ["Mikulov (Jihomoravský kraj, Czech Republic)"],
    }
    entities = extract_named_entities(record)
    mikulov = [
        e for e in entities
        if "mikulov" in normalize_entity_key(e["text"]) and e["kind"] == "place"
    ]
    assert len(mikulov) == 1
    assert mikulov[0]["role"] == "production place"
    assert "place" in (mikulov[0].get("alt_roles") or [])
