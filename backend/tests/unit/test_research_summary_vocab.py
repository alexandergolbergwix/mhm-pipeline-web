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

    def test_persons_works_and_places_counted_from_real_output(self) -> None:
        g = _build_real_graph(_records())
        summary = query_summary(g)
        assert summary["total_persons"] > 0, summary
        assert summary["total_works"] > 0, summary
        assert summary["total_places"] > 0, summary

    def test_triples_present_but_no_legacy_manuscript_class(self) -> None:
        """The graph has triples and NONE of them use the legacy hm:Manuscript_Object."""
        g = _build_real_graph(_records())
        assert len(g) > 0
        hm = rdflib.Namespace("https://w3id.org/mhm/ontology#")
        legacy = list(g.triples((None, rdflib.RDF.type, hm.Manuscript_Object)))
        assert legacy == [], "converter unexpectedly emits the legacy class"


def _parse_ttl(ttl: str) -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")
    return g


class TestSummaryCountsSurviveVocabDrift:
    """A production graph can lack ``hm:has_work`` / slash-CIDOC types and
    still carry F1 works, role links, and places. The header must count them.
    """

    def test_f1_work_without_has_work(self) -> None:
        g = _parse_ttl("""
        @prefix lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/> .
        @prefix hm: <https://w3id.org/mhm/ontology#> .
        <https://w3id.org/mhm/ontology#MS_1> a lrmoo:F4_Manifestation_Singleton .
        <https://w3id.org/mhm/ontology#W1> a lrmoo:F1_Work .
        """)
        summary = query_summary(g)
        assert summary["total_manuscripts"] == 1, summary
        assert summary["total_works"] == 1, summary

    def test_person_via_hmo_class_and_role_link(self) -> None:
        g = _parse_ttl("""
        @prefix hm: <https://w3id.org/mhm/ontology#> .
        @prefix lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/> .
        <https://w3id.org/mhm/ontology#MS_1> a lrmoo:F4_Manifestation_Singleton ;
            hm:has_scribe <https://w3id.org/mhm/ontology#P1> .
        <https://w3id.org/mhm/ontology#P1> a hm:E21_Person .
        """)
        summary = query_summary(g)
        assert summary["total_persons"] == 1, summary

    def test_place_via_production_link_without_e53(self) -> None:
        g = _parse_ttl("""
        @prefix hm: <https://w3id.org/mhm/ontology#> .
        @prefix lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/> .
        <https://w3id.org/mhm/ontology#MS_1> a lrmoo:F4_Manifestation_Singleton ;
            hm:has_production_place <https://w3id.org/mhm/ontology#Place_cairo> .
        """)
        summary = query_summary(g)
        assert summary["total_places"] == 1, summary

    def test_legacy_ontology_namespace_and_cidoc_hash(self) -> None:
        g = _parse_ttl("""
        @prefix lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/> .
        @prefix old: <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#> .
        @prefix crm: <http://www.cidoc-crm.org/cidoc-crm#> .
        <https://w3id.org/mhm/ontology#MS_1> a lrmoo:F4_Manifestation_Singleton ;
            old:has_work <https://w3id.org/mhm/ontology#W1> ;
            old:has_scribe <https://w3id.org/mhm/ontology#P1> ;
            old:has_production_place <https://w3id.org/mhm/ontology#Pl1> .
        <https://w3id.org/mhm/ontology#W1> a lrmoo:F1_Work .
        <https://w3id.org/mhm/ontology#P1> a crm:E21_Person .
        <https://w3id.org/mhm/ontology#Pl1> a crm:E53_Place .
        """)
        summary = query_summary(g)
        assert summary["total_works"] == 1, summary
        assert summary["total_persons"] == 1, summary
        assert summary["total_places"] == 1, summary

    def test_aggregate_matches_query_summary_on_drift_graph(self) -> None:
        from app.pipeline.research_aggregate import compute_aggregated_summary

        g = _parse_ttl("""
        @prefix lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/> .
        @prefix hm: <https://w3id.org/mhm/ontology#> .
        <https://w3id.org/mhm/ontology#MS_1> a lrmoo:F4_Manifestation_Singleton ;
            hm:has_scribe <https://w3id.org/mhm/ontology#P1> ;
            hm:has_production_place <https://w3id.org/mhm/ontology#Pl1> .
        <https://w3id.org/mhm/ontology#W1> a lrmoo:F1_Work .
        <https://w3id.org/mhm/ontology#P1> a hm:E21_Person .
        """)
        summary = query_summary(g)
        agg = compute_aggregated_summary(g, [], [], wikibase_configured=False)
        assert agg["total_manuscripts"] == summary["total_manuscripts"] == 1
        assert agg["total_works"] == summary["total_works"] == 1
        assert agg["total_persons"] == summary["total_persons"] == 1
        assert agg["total_places"] == summary["total_places"] == 1
