"""RDF build must tolerate run_records.marc rows from NLI-style collapse."""

from __future__ import annotations

from pathlib import Path
import tempfile

from app.pipeline.marc_ingest import _collapse_marc_subfields
from app.pipeline.rdf_build import _prepare_record_for_rdf, _run_mapper_sync


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
            triples, manuscripts, errors = _run_mapper_sync([rec], [], out)
            assert errors == []
            assert manuscripts == 1
            assert triples > 0
            assert out.stat().st_size > 0

    def test_quoted_control_number_does_not_crash_uri_build(self) -> None:
        """Control numbers stored with embedded quotes must not produce invalid URIs.

        The quoted CN is allowed inside literal values (``xsd:string``) but must
        never appear as part of a URI — i.e. not inside angle-bracket ``<...>``.
        """
        import re as _re

        rec = {
            "_control_number": '"990000403370205171"',
            "title": "ספר תורה",
            "authors": [{"name": "משה", "role": "author", "field": "100"}],
        }
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.ttl"
            triples, manuscripts, errors = _run_mapper_sync([rec], [], out)
            assert errors == [], errors
            assert manuscripts == 1
            assert triples > 0
            ttl = out.read_text(encoding="utf-8")
            # Verify no angle-bracket URI contains the quoted CN.
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
            triples, manuscripts, errors = _run_mapper_sync([rec], [], out)
        assert errors == []
        assert triples > 0
        assert manuscripts == 1
