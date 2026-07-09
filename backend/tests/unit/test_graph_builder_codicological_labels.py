"""Regression tests for codicological-unit Wikibase label/description quality."""

from __future__ import annotations

from rdflib import RDF, RDFS

from converter.config.namespaces import HM, LRMOO
from converter.rdf.graph_builder import GraphBuilder
from converter.transformer.field_handlers import ExtractedData
from converter.wikibase.hmo_exporter import HmoWikibaseExporter

GENERIC_SUFFIX = "in the Hebrew Manuscripts Ontology (HMO)"


def _export_codicological_units(title: str = "ספר תהילים", shelfmark: str = "Heb. 12.34") -> list:
    data = ExtractedData(title=title, shelfmark=shelfmark)
    graph = GraphBuilder().build_graph(data, "990001800310205171")
    drafts = HmoWikibaseExporter().from_graph(graph)
    return [draft for draft in drafts if draft.entity_type == "Codicological_Unit"]


def test_main_codicological_unit_has_meaningful_description() -> None:
    title = "ספר תהילים"
    shelfmark = "Heb. 12.34"
    units = _export_codicological_units(title=title, shelfmark=shelfmark)

    assert len(units) == 1
    unit = units[0]
    assert unit.labels["en"] == "Main codicological unit of MS 990001800310205171"
    description = unit.descriptions["en"]
    assert GENERIC_SUFFIX not in description
    assert "990001800310205171" in description
    assert title in description
    assert shelfmark in description
    assert description.startswith("Primary codicological unit of manuscript")


def test_codicological_unit_rdf_comment_is_stamped() -> None:
    graph = GraphBuilder().build_graph(
        ExtractedData(title="ספר תהילים", shelfmark="Heb. 12.34"),
        "990001800310205171",
    )
    cu_nodes = list(graph.subjects(RDF.type, HM.Codicological_Unit))
    assert len(cu_nodes) == 1
    comment = str(graph.value(cu_nodes[0], RDFS.comment))
    assert GENERIC_SUFFIX not in comment
    assert "Primary codicological unit of manuscript 990001800310205171" in comment


def test_primary_entities_avoid_generic_wikibase_descriptions() -> None:
    data = ExtractedData(
        title="ספר תהילים",
        shelfmark="Heb. 12.34",
        authors=[{"name": "אהרן בן אליהו", "role": "author"}],
        place="ירושלים",
    )
    graph = GraphBuilder().build_graph(data, "990001800310205171")
    drafts = HmoWikibaseExporter().from_graph(graph)

    by_type = {draft.entity_type: draft for draft in drafts}
    for entity_type in (
        "F4_Manifestation_Singleton",
        "F1_Work",
        "F2_Expression",
        "E21_Person",
        "E53_Place",
        "E12_Production",
    ):
        draft = by_type.get(entity_type)
        assert draft is not None, entity_type
        description = draft.descriptions.get("en", "")
        assert GENERIC_SUFFIX not in description, (entity_type, description)
        if entity_type not in {"F1_Work", "E53_Place"}:
            assert "990001800310205171" in description


def test_expression_label_omits_in_ms_suffix() -> None:
    title = "ספר תהילים"
    graph = GraphBuilder().build_graph(
        ExtractedData(title=title, shelfmark="Heb. 12.34"),
        "990001800310205171",
    )
    expr_nodes = list(graph.subjects(RDF.type, LRMOO.F2_Expression))
    assert expr_nodes
    label = str(graph.value(expr_nodes[0], RDFS.label))
    assert "(in MS" not in label
    assert title in label
