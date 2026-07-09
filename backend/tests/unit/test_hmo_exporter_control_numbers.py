"""Tests for manuscript control-number propagation on HMO item export."""

from __future__ import annotations

from converter.rdf.graph_builder import GraphBuilder
from converter.transformer.field_handlers import ExtractedData
from converter.wikibase.hmo_exporter import HmoWikibaseExporter


def test_person_item_inherits_manuscript_control_number() -> None:
    data = ExtractedData(
        title="ספר תהילים",
        authors=[{"name": "אהרן בן אליהו", "role": "author"}],
    )
    graph = GraphBuilder().build_graph(data, "990000403370205171")
    drafts = HmoWikibaseExporter().from_graph(graph)

    ms = next(d for d in drafts if d.entity_type == "F4_Manifestation_Singleton")
    person = next(d for d in drafts if d.entity_type == "E21_Person")

    assert ms.control_numbers == ["990000403370205171"]
    assert "990000403370205171" in person.control_numbers
