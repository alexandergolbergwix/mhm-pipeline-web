"""Unit tests for authority + NER merge layer before RDF mapping."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.pipeline.rdf_enrichment import merge_approved_authority, merge_approved_ner
from app.pipeline.rdf_build import _run_mapper_sync


def test_merge_ner_adds_former_owner() -> None:
    rec: dict = {"contributors": []}
    merge_approved_ner(rec, [{
        "text": "יעקב בן יוסף",
        "type": "OWNER",
        "role": "OWNER",
        "source": "provenance_ner",
    }])
    roles = [c.get("role") for c in rec.get("contributors") or []]
    assert "former_owner" in roles


def test_merge_authority_fills_viaf_and_wikidata() -> None:
    rec: dict = {
        "authors": [{"name": "משה בן מיימון", "role": "author"}],
    }
    merge_approved_authority(rec, [{
        "control_number": "990001",
        "entity_text": "משה בן מיימון",
        "entity_kind": "person",
        "role": "author",
        "viaf_id": "12345",
        "wikidata_qid": "Q127334",
        "payload": {"preferred_name_lat": "Maimonides, Moses"},
    }])
    author = rec["authors"][0]
    assert author.get("viaf_id") == "12345"
    assert author.get("wikidata_id") == "Q127334"
    assert rec.get("marc_authority_matches")


def test_authority_ids_emit_in_ttl() -> None:
    rec = {
        "_control_number": "990000827290205171",
        "title": "פירוש המשנה",
        "authors": [{"name": "משה בן מיימון", "role": "author"}],
    }
    matches = [{
        "control_number": "990000827290205171",
        "entity_text": "משה בן מיימון",
        "entity_kind": "person",
        "role": "author",
        "viaf_id": "987654321",
        "wikidata_qid": "Q127334",
        "payload": {},
    }]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out.ttl"
        triples, manuscripts, errors, _, _ = _run_mapper_sync([rec], matches, out)
        assert errors == []
        assert manuscripts == 1
        assert triples > 0
        ttl = out.read_text(encoding="utf-8")
        assert "viaf" in ttl.lower() or "wikidata" in ttl.lower()
