"""Regression: research analytics counts must match the REAL converter vocab.

The Linked Data Explorer's Overview tab showed ``0 manuscripts`` on a graph
with thousands of triples because ``_MS_COUNT_Q`` counted ``hm:Manuscript_Object``
— a class the ``MarcToRdfMapper`` never emits. The mapper types a manuscript as
``lrmoo:F4_Manifestation_Singleton`` + ``hm:Bibliographic_Unit`` on the same URI.

Earlier tests hand-wrote TTL using ``hm:Manuscript_Object`` to match the queries,
so they passed against fake data while the real converter output never matched.
This test closes that gap: it builds a graph through the real converter
(``_run_mapper_sync``) and asserts ``query_summary`` counts what was actually
emitted.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import rdflib

from app.pipeline.rdf_build import _run_mapper_sync
from app.pipeline.research_queries import query_summary


def _build_real_graph(records: list[dict]) -> rdflib.Graph:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "manuscripts.ttl"
        _triples, manuscripts, errors, _, _, _, _, _, _ = _run_mapper_sync(records, [], out)
        assert errors == [], errors
        assert manuscripts == len(records)
        g = rdflib.Graph()
        g.parse(str(out), format="turtle")
        return g


def _records() -> list[dict]:
    return [
        {
            "_control_number": "990000827290205171",
            "title": "פירוש המשנה",
            "authors": [{"name": "משה בן מיימון", "role": "author", "field": "100"}],
            "place": "קהיר",
        },
        {
            "_control_number": "990000403370205171",
            "title": "ספר תורה",
            "authors": [{"name": "שלמה בן יצחק", "role": "author", "field": "100"}],
        },
    ]


class TestSummaryCountsRealConverterVocab:
    def test_manuscripts_counted_from_real_output(self) -> None:
        """The regression: a graph from the real converter counts > 0 manuscripts."""
        g = _build_real_graph(_records())
        summary = query_summary(g)
        assert summary["total_manuscripts"] == 2, summary

    def test_persons_and_works_counted_from_real_output(self) -> None:
        g = _build_real_graph(_records())
        summary = query_summary(g)
        assert summary["total_persons"] > 0, summary
        assert summary["total_works"] > 0, summary

    def test_triples_present_but_no_legacy_manuscript_class(self) -> None:
        """The graph has triples and NONE of them use the legacy hm:Manuscript_Object."""
        g = _build_real_graph(_records())
        assert len(g) > 0
        hm = rdflib.Namespace("https://w3id.org/mhm/ontology#")
        legacy = list(g.triples((None, rdflib.RDF.type, hm.Manuscript_Object)))
        assert legacy == [], "converter unexpectedly emits the legacy class"
