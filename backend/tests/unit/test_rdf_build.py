"""Regression tests: RDF build + Wikidata Studio normalisation of run_records.marc rows."""

from __future__ import annotations

import asyncio
import re as _re
import tempfile
from pathlib import Path

from app.pipeline.marc_ingest import _collapse_marc_subfields, prepare_record_for_pipeline
from app.pipeline.rdf_build import _prepare_record_for_rdf, _run_mapper_sync
from app.pipeline.wikidata_studio import build_items_for_run


def _nli_style_record() -> dict:
    return {
        "_control_number": "990000827290205171",
        "245$a": "פירוש המשנה",
        "245$b": "להרמב\"ם",
        "100$a": "משה בן מיימון",
        "655$a": "manuscript",
    }


class TestRdfBuildCollapsedMarc:
    def test_collapse_genres_are_strings(self) -> None:
        rec = _nli_style_record()
        _collapse_marc_subfields(rec)
        genres = rec.get("genres") or []
        assert genres
        assert all(isinstance(g, str) for g in genres)

    def test_build_produces_triples_after_collapse(self) -> None:
        rec = _nli_style_record()
        _collapse_marc_subfields(rec)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "manuscripts.ttl"
            triples, manuscripts, errors, _cov, _unk = _run_mapper_sync([rec], [], out)
            assert errors == []
            assert manuscripts == 1
            assert triples > 0
            assert out.stat().st_size > 0

    def test_quoted_control_number_does_not_crash_uri_build(self) -> None:
        """Control numbers stored with embedded quotes must not produce invalid URIs."""
        rec = {
            "_control_number": '"990000403370205171"',
            "title": "ספר תורה",
            "authors": [{"name": "משה", "role": "author", "field": "100"}],
        }
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.ttl"
            triples, manuscripts, errors, _cov, _unk = _run_mapper_sync([rec], [], out)
            assert errors == [], errors
            assert manuscripts == 1
            assert triples > 0
            ttl = out.read_text(encoding="utf-8")
            uri_fragments = _re.findall(r"<[^>]+>", ttl)
            leaked = [u for u in uri_fragments if '"990000403370205171"' in u]
            assert not leaked, f"raw quoted CN leaked into URI(s): {leaked}"

    def test_prepare_coerces_legacy_dict_genres(self) -> None:
        rec = {
            "_control_number": "X",
            "title": "ספר",
            "genres": [{"name": "manuscript", "field": "655"}],
        }
        prepared = _prepare_record_for_rdf(rec)
        assert prepared["genres"] == ["manuscript"]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.ttl"
            triples, manuscripts, errors, _cov, _unk = _run_mapper_sync([rec], [], out)
        assert errors == []
        assert triples > 0
        assert manuscripts == 1


class TestWikidataStudioWorks:
    def test_works_created_from_505a_subfield_key(self) -> None:
        """Records with raw 505$a (old DB rows) must produce work items."""
        rec = {
            "_control_number": "990001",
            "245$a": "כתב יד",
            "505$a": "פירוש א -- פירוש ב -- פירוש ג",
            "100$a": "ראובן בן יעקב",
        }
        result = asyncio.run(build_items_for_run(marc_records=[rec], approved_matches=[]))
        summary = result["summary"]
        assert summary["works"] == 3, f"expected 3 work items, got {summary['works']}"
        assert summary["manuscripts"] == 1

    def test_works_created_from_flat_contents(self) -> None:
        """Records already normalised (flat contents list) still produce work items."""
        rec = {
            "_control_number": "990002",
            "title": "כתב יד",
            "contents": [{"title": "פירוש א"}, {"title": "פירוש ב"}],
        }
        result = asyncio.run(build_items_for_run(marc_records=[rec], approved_matches=[]))
        assert result["summary"]["works"] == 2

    def test_prepare_record_for_pipeline_derives_contents(self) -> None:
        rec = {"_control_number": "X", "505$a": "א -- ב -- ג"}
        out = prepare_record_for_pipeline(rec)
        titles = [c["title"] for c in out.get("contents") or []]
        assert "א" in titles
        assert "ב" in titles
        assert "ג" in titles
