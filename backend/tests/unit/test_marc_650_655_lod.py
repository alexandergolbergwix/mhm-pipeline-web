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

    def test_resolvable_secondary_subject_is_promoted_to_p921(self) -> None:
        """A verified QID is the evidence gate, not an explicit primary marker.

        Rule W-281. ``resolve_subject_qid`` is fail-closed — it answers only
        from the static crosswalk or a pre-stamped authority ``wikidata_id`` —
        so a hit is the MARC/authority evidence Rule R26 requires. Requiring
        a ``primary`` marker as well dropped 463 resolvable term-occurrences
        in the 17th-century corpus and held P921 at 2.3%.

        (This test previously asserted the opposite: a secondary 650 was
        withheld from P921. The broad-term guard below keeps the part of that
        rule that still holds.)
        """
        rec = prepare_record_for_pipeline(_sample_record())
        item = WikidataItemBuilder(reconciler=None).build_manuscript_item(rec)
        p136 = [s for s in item.statements if s.property_id == "P136"]
        assert any(
            s.property_id == "P921" and s.value == "Q1845"
            for s in item.statements
        )
        assert any(s.value == "Q1749541" for s in p136)

    def test_broad_subject_is_still_withheld_from_p921(self) -> None:
        """Generic headings are never the main subject, however often they appear.

        Rule R26 / W-71: ``Jews`` and ``Judaism`` are population/topic
        containers, not the subject of one manuscript. This is the guard that
        survives the W-281 widening.
        """
        for broad in ("Jews", "Judaism"):
            rec = prepare_record_for_pipeline({
                "_control_number": f"BROAD-{broad}",
                "title": "כתב יד",
                "subjects": [{"term": broad, "type": "topic", "field": "650"}],
            })
            item = WikidataItemBuilder(reconciler=None).build_manuscript_item(rec)
            assert not any(s.property_id == "P921" for s in item.statements), broad

    def test_unresolvable_subject_is_not_promoted_to_p921(self) -> None:
        """No QID, no claim — fail closed (Rule R26 / W-71)."""
        rec = prepare_record_for_pipeline({
            "_control_number": "NOQID",
            "title": "כתב יד",
            "subjects": [{"term": "זבלוסט ויטש", "type": "topic", "field": "650"}],
        })
        item = WikidataItemBuilder(reconciler=None).build_manuscript_item(rec)
        assert not any(s.property_id == "P921" for s in item.statements)

    def test_explicit_primary_subject_is_projected_to_p921(self) -> None:
        rec = prepare_record_for_pipeline({
            "_control_number": "PRIMARY650",
            "title": "פירוש",
            "subjects": [{"term": "מקרא", "type": "topic", "field": "650", "primary": True}],
        })
        item = WikidataItemBuilder(reconciler=None).build_manuscript_item(rec)
        assert any(
            s.property_id == "P921" and s.value == "Q1845"
            for s in item.statements
        )

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
