"""Unit tests for graph catalog + viewport selection."""

from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path

import rdflib
from rdflib import RDF, RDFS, Graph, Literal, Namespace

from app.pipeline.graph_index import (
    ViewportParams,
    _select_viewport_nodes,
    build_and_persist_index,
    build_catalog,
    build_viewport_payload,
    ensure_index,
    load_catalog,
    scan_graph,
)

HMO = Namespace("http://hebrew-manuscripts.org/ontology/")


def _sample_nodes_edges() -> tuple[list[dict], list[dict]]:
    nodes = [
        {"id": "urn:ms:1", "type": "Manuscript", "degree": 10, "is_manuscript": True,
         "haystack": "ms one manuscript", "label": "MS 1", "color": "#000"},
        {"id": "urn:ms:2", "type": "Manuscript", "degree": 8, "is_manuscript": True,
         "haystack": "ms two manuscript", "label": "MS 2", "color": "#000"},
        {"id": "urn:p:1", "type": "Person", "degree": 20, "is_manuscript": False,
         "haystack": "maimonides person", "label": "Maimonides", "color": "#111"},
        {"id": "urn:p:2", "type": "Person", "degree": 15, "is_manuscript": False,
         "haystack": "rashi person", "label": "Rashi", "color": "#111"},
        {"id": "urn:w:1", "type": "Work", "degree": 5, "is_manuscript": False,
         "haystack": "mishneh torah work", "label": "Mishneh Torah", "color": "#222"},
    ]
    edges = [
        {"id": "e1", "source": "urn:ms:1", "target": "urn:p:1",
         "predicate": str(HMO.mentions_scribe), "predicate_label": "mentions scribe"},
        {"id": "e2", "source": "urn:ms:2", "target": "urn:p:2",
         "predicate": str(HMO.mentions_scribe), "predicate_label": "mentions scribe"},
        {"id": "e3", "source": "urn:ms:1", "target": "urn:w:1",
         "predicate": str(HMO.has_work), "predicate_label": "has work"},
        {"id": "e4", "source": "urn:p:1", "target": "urn:w:1",
         "predicate": str(RDFS.seeAlso), "predicate_label": "seeAlso"},
    ]
    return nodes, edges


class TestBuildCatalog:
    def test_counts_node_types_and_manuscripts(self) -> None:
        nodes, edges = _sample_nodes_edges()
        catalog = build_catalog(nodes, edges)
        assert catalog.total_nodes == 5
        assert catalog.total_edges == 4
        assert catalog.node_types["Manuscript"] == 2
        assert catalog.node_types["Person"] == 2
        assert catalog.manuscript_count == 2
        assert len(catalog.manuscript_ids) == 2


class TestSelectViewportNodes:
    def test_default_view_keeps_all_manuscripts_then_fills_by_degree(self) -> None:
        nodes, edges = _sample_nodes_edges()
        kept = _select_viewport_nodes(nodes, edges, ViewportParams(max_nodes=3))
        assert "urn:ms:1" in kept
        assert "urn:ms:2" in kept
        # Budget is clamped to min 50 — all five nodes fit.
        assert len(kept) == 5

    def test_type_filter_manuscript_returns_only_manuscripts(self) -> None:
        nodes, edges = _sample_nodes_edges()
        kept = _select_viewport_nodes(
            nodes, edges,
            ViewportParams(types=["Manuscript"], max_nodes=500),
        )
        assert kept == {"urn:ms:1", "urn:ms:2"}

    def test_search_expands_one_hop_within_candidate(self) -> None:
        nodes, edges = _sample_nodes_edges()
        kept = _select_viewport_nodes(
            nodes, edges,
            ViewportParams(q="maimonides", max_nodes=500),
        )
        assert "urn:p:1" in kept
        assert "urn:ms:1" in kept

    def test_predicate_filter_keeps_endpoints(self) -> None:
        nodes, edges = _sample_nodes_edges()
        kept = _select_viewport_nodes(
            nodes, edges,
            ViewportParams(predicates=["has work"], max_nodes=500),
        )
        assert kept == {"urn:ms:1", "urn:w:1"}


class TestPersistAndViewport:
    def _tiny_graph(self) -> Graph:
        g = Graph()
        g.bind("hmo", HMO)
        ms = HMO["MS_test_1"]
        person = HMO["person_1"]
        g.add((ms, RDF.type, HMO.F4_Manifestation_Singleton))
        g.add((ms, RDFS.label, Literal("Test MS")))
        g.add((person, RDF.type, HMO.E21_Person))
        g.add((person, RDFS.label, Literal("Test Author")))
        g.add((ms, HMO.mentions_scribe, person))
        return g

    def test_build_and_persist_writes_catalog_and_sqlite(self) -> None:
        graph = self._tiny_graph()
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            catalog = build_and_persist_index(graph, run_dir)
            assert (run_dir / "graph_catalog.json").exists()
            assert (run_dir / "graph_index.sqlite").exists()
            loaded = load_catalog(run_dir)
            assert loaded is not None
            assert loaded.manuscript_count == catalog.manuscript_count
            assert loaded.total_nodes >= 2

    def test_viewport_payload_includes_manuscript_metadata(self) -> None:
        graph = self._tiny_graph()
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            build_and_persist_index(graph, run_dir)
            payload = build_viewport_payload(run_dir, ViewportParams(max_nodes=500, layout="preset"))
            assert payload["manuscript_count"] >= 1
            assert payload["manuscripts_in_view"] >= 1
            assert payload["total_nodes"] >= len(payload["nodes"])
            assert "nodes" in payload and "edges" in payload

    def test_scan_graph_detects_manuscript_from_ms_uri(self) -> None:
        g = Graph()
        g.bind("hmo", HMO)
        ms = HMO["MS_abc"]
        g.add((ms, RDF.type, HMO.F4_Manifestation_Singleton))
        g.add((ms, RDFS.label, Literal("Shelf")))
        nodes, _edges = scan_graph(g)
        ms_nodes = [n for n in nodes if n.get("is_manuscript") and "MS_abc" in n["id"]]
        assert len(ms_nodes) == 1


class TestEnsureIndexConcurrency:
    def test_parallel_ensure_index_does_not_corrupt_sqlite(self) -> None:
        g = Graph()
        g.bind("hmo", HMO)
        ms = HMO["MS_parallel"]
        person = HMO["person_parallel"]
        g.add((ms, RDF.type, HMO.F4_Manifestation_Singleton))
        g.add((ms, RDFS.label, Literal("Parallel MS")))
        g.add((person, RDF.type, HMO.E21_Person))
        g.add((person, RDFS.label, Literal("Author")))
        g.add((ms, HMO.mentions_scribe, person))

        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            ttl = run_dir / "output.ttl"
            g.serialize(destination=str(ttl), format="turtle")
            errors: list[Exception] = []

            def worker() -> None:
                try:
                    ensure_index(ttl, run_dir)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert errors == [], errors
            assert (run_dir / "graph_index.sqlite").exists()
            payload = build_viewport_payload(
                run_dir, ViewportParams(max_nodes=500, layout="preset"),
            )
            assert len(payload["nodes"]) >= 2
