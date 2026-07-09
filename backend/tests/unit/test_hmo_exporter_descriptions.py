"""HMO exporter description fallback hygiene."""

from __future__ import annotations

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from converter.config.namespaces import HM, LRMOO
from converter.wikibase.hmo_exporter import _descriptions_for_node, _labels_for_node

GENERIC_SUFFIX = "in the Hebrew Manuscripts Ontology (HMO)"


def test_description_fallback_uses_manuscript_not_generic_hmo() -> None:
    graph = Graph()
    ms = URIRef(f"{HM}Manuscript_990001800310205171")
    person = URIRef(f"{HM}Person_990001800310205171")
    graph.add((ms, RDF.type, LRMOO.F4_Manifestation_Singleton))
    graph.add((person, RDF.type, HM.E21_Person))
    graph.add((person, RDFS.label, Literal("Example Author", lang="he")))
    desc = _descriptions_for_node(graph, person, HM.E21_Person)
    assert GENERIC_SUFFIX not in desc["en"]
    assert "990001800310205171" in desc["en"]


def test_description_uses_rdfs_comment_when_present() -> None:
    graph = Graph()
    node = URIRef(f"{HM}CatalogStep_990001800310205171")
    graph.add((node, RDF.type, HM.CatalogStep))
    graph.add((node, RDFS.comment, Literal("Catalog step for MS 990001800310205171.", lang="en")))
    desc = _descriptions_for_node(graph, node, HM.CatalogStep)
    assert desc["en"].startswith("Catalog step")


def test_hebrew_only_label_not_copied_to_en() -> None:
    graph = Graph()
    work = URIRef(f"{HM}Work_test")
    graph.add((work, RDF.type, LRMOO.F1_Work))
    graph.add((work, RDFS.label, Literal("קטע מפרוש התורה", lang="he")))
    labels = _labels_for_node(graph, work)
    assert labels.get("he") == "קטע מפרוש התורה"
    assert "en" not in labels
