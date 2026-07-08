"""Regression: 561 provenance text must not mint bogus Acquisition items."""

from __future__ import annotations

from converter.transformer.field_handlers import ExtractedData
from converter.transformer.mapper import MarcToRdfMapper


def test_provenance_561_does_not_emit_acquisition_entity() -> None:
    extracted = ExtractedData()
    extracted.control_number = "990000403370205171"
    extracted.title = "Test MS"
    extracted.provenance = 'בראש כה"י ציון הבעלים: "עמנואל ריקי"'

    graph = MarcToRdfMapper().graph_builder.build_graph(extracted, "990000403370205171")
    ttl = graph.serialize(format="turtle")

    assert "Acquisition_990000403370205171" not in ttl
    assert "ownership_history" in ttl
