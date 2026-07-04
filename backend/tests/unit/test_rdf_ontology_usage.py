"""Unit tests for `rdf_build.ontology_usage` — real usage of one HMO
ontology class/property inside a run's RDF graph (backs the schema
bootstrap detail drawer's "based on the RDF graph" section)."""

from __future__ import annotations

from rdflib import RDF, RDFS, Graph, Literal, Namespace

from app.pipeline.rdf_build import ontology_usage

LRMOO = Namespace("http://iflastandards.info/ns/lrm/lrmoo/")
HM = Namespace("http://hebrew-manuscripts.org/data/")


def _sample_graph() -> Graph:
    g = Graph()
    g.add((HM.Work_A, RDF.type, LRMOO.F1_Work))
    g.add((HM.Work_A, RDFS.label, Literal("Mishneh Torah")))
    g.add((HM.Work_B, RDF.type, LRMOO.F1_Work))
    g.add((HM.Work_B, RDFS.label, Literal("Guide for the Perplexed")))
    g.add((HM.Expression_A, RDF.type, LRMOO.F2_Expression))
    g.add((HM.Expression_A, LRMOO.R3_is_realised_in, HM.Work_A))
    g.add((HM.MS_1, LRMOO.R4_embodies, HM.Expression_A))
    g.add((HM.MS_1, RDFS.label, Literal("MS Vatican Ebr. 44")))
    return g


class TestClassUsage:
    def test_counts_nodes_typed_as_the_class(self) -> None:
        usage = ontology_usage(_sample_graph(), str(LRMOO.F1_Work), "class")
        assert usage["count"] == 2
        assert usage["entity_kind"] == "class"

    def test_examples_carry_resolved_labels(self) -> None:
        usage = ontology_usage(_sample_graph(), str(LRMOO.F1_Work), "class")
        labels = {ex["label"] for ex in usage["examples"]}
        assert labels == {"Mishneh Torah", "Guide for the Perplexed"}

    def test_unused_class_returns_zero(self) -> None:
        usage = ontology_usage(_sample_graph(), str(LRMOO.F5_Item), "class")
        assert usage["count"] == 0
        assert usage["examples"] == []


class TestPropertyUsage:
    def test_counts_triples_with_that_predicate(self) -> None:
        usage = ontology_usage(_sample_graph(), str(LRMOO.R4_embodies), "property")
        assert usage["count"] == 1
        example = usage["examples"][0]
        assert example["subject_label"] == "MS Vatican Ebr. 44"
        assert example["object_is_literal"] is False

    def test_unused_property_returns_zero(self) -> None:
        usage = ontology_usage(_sample_graph(), str(LRMOO.R27_materialized), "property")
        assert usage["count"] == 0

    def test_total_triples_reflects_full_graph_size(self) -> None:
        graph = _sample_graph()
        usage = ontology_usage(graph, str(LRMOO.R4_embodies), "property")
        assert usage["total_triples"] == len(graph)
