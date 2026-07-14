"""Integration tests: MARC 650/655 flow to HMO RDF and Wikidata items."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.pipeline.marc_ingest import _collapse_marc_subfields, prepare_record_for_pipeline
from app.pipeline.rdf_build import _run_mapper_sync
from converter.wikidata.item_builder import WikidataItemBuilder


def _sample_record() -> dict:
    return {
        "_control_number": "LOD650655",
        "245$a": "פירוש",
        "650$a": "מקרא",
        "655$a": "Commentaries",
    }


class TestMarc650655Rdf:
    def test_collapse_normalizes_subjects_and_genres(self) -> None:
        rec = _sample_record()
        _collapse_marc_subfields(rec)
        prepared = prepare_record_for_pipeline(rec)
        subjects = prepared.get("subjects") or []
        assert any(s.get("term") == "מקרא" and s.get("type") == "topic" for s in subjects)
        assert prepared.get("genres") == ["Commentaries"]
        assert not any(not s.get("term") for s in subjects)

    def test_rdf_emits_subject_and_genre_triples(self) -> None:
        rec = prepare_record_for_pipeline(_sample_record())
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.ttl"
            _triples, manuscripts, errors, *_ = _run_mapper_sync([rec], [], out)
            assert errors == []
            assert manuscripts == 1
            ttl = out.read_text(encoding="utf-8")
        assert "מקרא" in ttl
        assert "Commentaries" in ttl
        assert "has_genre" in ttl
        assert "P129_is_about" in ttl or "is_about" in ttl


class TestMarc650655Wikidata:
    def test_prepare_strips_empty_genre_dict_shells(self) -> None:
        rec = {
            "_control_number": "EMPTY655",
            "title": "כתב יד",
            "genres": [{"name": "", "field": "655"}],
        }
        prepared = prepare_record_for_pipeline(rec)
        assert prepared.get("genres") == []

    def test_p921_and_p136_from_marc(self) -> None:
        rec = prepare_record_for_pipeline(_sample_record())
        item = WikidataItemBuilder(reconciler=None).build_manuscript_item(rec)
        p921 = [s for s in item.statements if s.property_id == "P921"]
        p136 = [s for s in item.statements if s.property_id == "P136"]
        assert any(s.value == "Q1845" for s in p921)
        assert any(s.value == "Q1749541" for s in p136)

    def test_generic_jews_heading_is_not_promoted_to_main_subject(self) -> None:
        rec = prepare_record_for_pipeline({
            "_control_number": "BROAD",
            "245$a": "כתב יד",
            "650$a": "Jews",
        })
        item = WikidataItemBuilder(reconciler=None).build_manuscript_item(rec)
        assert all(
            s.value != "Q7325"
            for s in item.statements
            if s.property_id == "P921"
        )
