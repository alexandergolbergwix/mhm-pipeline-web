"""Regression tests for philological-overlay Wikibase label/description quality."""

from __future__ import annotations

from rdflib import Literal, RDF, RDFS

from converter.config.namespaces import HM
from converter.rdf.graph_builder import GraphBuilder
from converter.transformer.field_handlers import ExtractedData
from converter.wikibase.hmo_exporter import HmoWikibaseExporter

GENERIC_SUFFIX = "in the Hebrew Manuscripts Ontology (HMO)"


def _build_overlay_graph(title: str = "ספר תהילים") -> tuple:
    data = ExtractedData(title=title)
    builder = GraphBuilder(add_philological_overlay=True)
    graph = builder.build_graph(data, "000123456")
    drafts = HmoWikibaseExporter().from_graph(graph)
    by_type = {draft.entity_type: draft for draft in drafts}
    return graph, by_type, title


def test_philological_overlay_entities_have_labels() -> None:
    # ParadigmBridge is intentionally excluded from Wikibase export (Rule
    # W-42) — it stays RDF-only, so its label quality is checked against the
    # graph directly in test_paradigm_bridge_label_is_not_uri_slug below.
    _, by_type, title = _build_overlay_graph()

    # TransmissionWitness / PhilologicalView carry intentional English system
    # labels; TextTradition mirrors the (Hebrew) work title, so its label is
    # `he` — Hebrew is NOT copied into the English slot (Rule W-45/W-51).
    for entity_type in ("TransmissionWitness", "PhilologicalView"):
        assert by_type[entity_type].labels.get("en"), f"{entity_type} missing English label"
    tradition = by_type["TextTradition"]
    assert tradition.labels.get("he") == title
    assert "en" not in tradition.labels


def test_philological_overlay_entities_have_meaningful_descriptions() -> None:
    _, by_type, title = _build_overlay_graph()

    for entity_type in (
        "TextTradition",
        "TransmissionWitness",
        "PhilologicalView",
    ):
        description = by_type[entity_type].descriptions["en"]
        assert GENERIC_SUFFIX not in description, entity_type
        assert description.strip()


def test_text_tradition_uses_clean_display_title_not_uri_suffix() -> None:
    graph, by_type, title = _build_overlay_graph()

    tradition_draft = by_type["TextTradition"]
    assert tradition_draft.labels["he"] == title
    assert not tradition_draft.labels["he"].endswith(" tradition")

    tradition_nodes = list(graph.subjects(RDF.type, HM.TextTradition))
    assert len(tradition_nodes) == 1
    tradition_name_values = [
        str(obj)
        for obj in graph.objects(tradition_nodes[0], HM.tradition_name)
    ]
    assert tradition_name_values == [title]
    assert not tradition_name_values[0].endswith(" tradition")


def test_work_and_expression_hebrew_labels_not_mirrored_to_english() -> None:
    # Rule W-45/W-51: a Hebrew title stays in the `he` slot and is never
    # duplicated into `en` (the export quality gate flags that as noise).
    _, by_type, title = _build_overlay_graph()

    work = by_type["F1_Work"]
    expression = by_type["F2_Expression"]
    assert work.labels["he"] == title
    assert "en" not in work.labels
    assert expression.labels["he"].startswith(title)
    assert "en" not in expression.labels


def test_paradigm_bridge_label_is_not_uri_slug() -> None:
    # ParadigmBridge is RDF-only (skipped from Wikibase export, Rule W-42),
    # so its label is checked directly on the graph rather than via by_type.
    graph, _, title = _build_overlay_graph()

    bridge_nodes = list(graph.subjects(RDF.type, HM.ParadigmBridge))
    assert len(bridge_nodes) == 1
    bridge_label = str(graph.value(bridge_nodes[0], RDFS.label))
    assert bridge_label.startswith("Paradigm bridge:")
    assert title in bridge_label
    assert "Bridge_" not in bridge_label


def test_transmission_witness_description_mentions_manuscript() -> None:
    _, by_type, _ = _build_overlay_graph()

    description = by_type["TransmissionWitness"].descriptions["en"]
    assert "000123456" in description
    assert "Attests the textual tradition" in description
