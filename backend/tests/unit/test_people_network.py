"""People network must follow the RDF graph's real person→manuscript edges."""

from __future__ import annotations

import tempfile
from pathlib import Path

import rdflib
from rdflib import Literal
from rdflib.namespace import RDF, RDFS

from app.pipeline.rdf_build import _run_mapper_sync
from app.pipeline.research_queries import HM, CIDOC, query_people_network


def _build_real_graph(records: list[dict]) -> rdflib.Graph:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "manuscripts.ttl"
        result = _run_mapper_sync(records, [], out)
        _triples, manuscripts, errors = result[0], result[1], result[2]
        assert errors == [], errors
        assert manuscripts == len(records)
        g = rdflib.Graph()
        g.parse(str(out), format="turtle")
        return g


def test_people_network_links_author_and_mentions_scribe_on_same_ms() -> None:
    """Synthetic scribes attach via hm:mentions_scribe, not hm:has_scribe on the MS."""
    records = [
        {
            "_control_number": "990000000000000001",
            "title": "ספר תורה",
            "authors": [{"name": "משה בן מימון", "role": "author"}],
        },
    ]
    graph = _build_real_graph(records)
    result = query_people_network(graph, max_nodes=50)

    assert len(result["nodes"]) >= 2
    assert len(result["links"]) >= 1


def test_people_network_finds_production_event_scribe_path() -> None:
    ms = HM["MS_test"]
    prod = HM["Prod_test"]
    scribe = HM["Person_scribe"]
    graph = rdflib.Graph()
    graph.add((ms, RDF.type, HM.Bibliographic_Unit))
    graph.add((scribe, RDF.type, CIDOC.E21_Person))
    graph.add((scribe, RDFS.label, Literal("Scribe A", lang="en")))
    graph.add((ms, HM.has_production_event, prod))
    graph.add((prod, HM.has_scribe, scribe))

    author = HM["Person_author"]
    work = HM["Work_test"]
    graph.add((author, RDF.type, CIDOC.E21_Person))
    graph.add((author, RDFS.label, Literal("Author B", lang="he")))
    graph.add((ms, HM.has_work, work))
    graph.add((work, HM.has_author, author))

    result = query_people_network(graph, max_nodes=50)
    assert len(result["nodes"]) == 2
    assert len(result["links"]) == 1


def test_people_network_includes_former_owner() -> None:
    ms = HM["MS_test"]
    owner = HM["Person_owner"]
    graph = rdflib.Graph()
    graph.add((ms, RDF.type, HM.Bibliographic_Unit))
    graph.add((owner, RDF.type, CIDOC.E21_Person))
    graph.add((ms, HM.former_owner, owner))

    author = HM["Person_author"]
    work = HM["Work_test"]
    graph.add((author, RDF.type, CIDOC.E21_Person))
    graph.add((ms, HM.has_work, work))
    graph.add((work, HM.has_author, author))

    result = query_people_network(graph, max_nodes=50)
    assert len(result["nodes"]) == 2
    assert len(result["links"]) == 1
