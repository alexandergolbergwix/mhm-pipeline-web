"""Tests for resolving HMO exporter drafts against a live schema mapping
(Phase 4 — see dev-docs/hmo-wikibase-studio-plan.md).
"""

from __future__ import annotations

import pytest
from rdflib import Graph, Literal, RDF, RDFS

from converter.config.namespaces import HM
from converter.wikibase.hmo_exporter import HmoWikibaseExporter, resolve_against_mappings
from converter.wikibase.resolved_models import SchemaMappingEntry, UnmappedOntologyUriError


def _mapping(**kwargs: SchemaMappingEntry) -> dict[str, SchemaMappingEntry]:
    return dict(kwargs)


def test_resolve_builds_string_and_time_claims_and_defers_entity_links() -> None:
    graph = Graph()
    ms = HM.MS1
    scribe = HM.Person1
    graph.add((ms, RDF.type, HM.Codicological_Unit))
    graph.add((ms, RDFS.label, Literal("Test MS", lang="en")))
    graph.add((ms, HM.has_date_of_creation, Literal("1500")))
    graph.add((ms, HM.created_by, scribe))
    graph.add((scribe, RDF.type, HM.Person))
    graph.add((scribe, RDFS.label, Literal("Test Scribe", lang="en")))

    drafts = HmoWikibaseExporter().from_graph(graph)
    assert len(drafts) == 2

    mappings = {
        str(HM.Codicological_Unit): SchemaMappingEntry("Q1"),
        str(HM.Person): SchemaMappingEntry("Q2"),
        str(HM.has_date_of_creation): SchemaMappingEntry("P1", "time"),
        str(HM.created_by): SchemaMappingEntry("P2", "wikibase-item"),
    }

    resolved = resolve_against_mappings(drafts, mappings)
    by_class = {e.class_qid: e for e in resolved}

    ms_entity = by_class["Q1"]
    assert ms_entity.claims[0].property_id == "P1"
    assert ms_entity.claims[0].datatype == "time"
    assert ms_entity.claims[0].value == {"time": "+1500-00-00T00:00:00Z", "precision": 9}
    assert len(ms_entity.deferred_links) == 1
    assert ms_entity.deferred_links[0].property_id == "P2"
    assert ms_entity.deferred_links[0].target_local_id == by_class["Q2"].local_id
    assert ms_entity.skipped_statements == []


def test_resolve_raises_on_unmapped_property_uri() -> None:
    graph = Graph()
    ms = HM.MS1
    graph.add((ms, RDF.type, HM.Codicological_Unit))
    graph.add((ms, HM.has_material, Literal("parchment")))

    drafts = HmoWikibaseExporter().from_graph(graph)
    mappings = {str(HM.Codicological_Unit): SchemaMappingEntry("Q1")}

    with pytest.raises(UnmappedOntologyUriError) as excinfo:
        resolve_against_mappings(drafts, mappings)
    assert str(HM.has_material) in excinfo.value.missing_uris


def test_resolve_raises_on_unmapped_class_uri() -> None:
    graph = Graph()
    ms = HM.MS1
    graph.add((ms, RDF.type, HM.Codicological_Unit))

    drafts = HmoWikibaseExporter().from_graph(graph)

    with pytest.raises(UnmappedOntologyUriError) as excinfo:
        resolve_against_mappings(drafts, {})
    assert str(HM.Codicological_Unit) in excinfo.value.missing_uris


def test_draft_description_falls_back_to_clean_class_name_when_no_comment() -> None:
    """Regression: earlier code shipped "Offline HMO Wikibase draft for X"
    as the live description of every entity — leftover debug wording
    that read as broken/unfinished once it hit the production Wikibase.
    """
    graph = Graph()
    tradition = HM.TextTradition1
    graph.add((tradition, RDF.type, HM.TextTradition))
    graph.add((tradition, RDFS.label, Literal("Some tradition", lang="he")))

    drafts = HmoWikibaseExporter().from_graph(graph)
    assert len(drafts) == 1
    description = drafts[0].descriptions["en"]
    assert "Offline" not in description
    assert "draft" not in description.lower()
    assert "TextTradition" in description or "Text Tradition" in description


def test_draft_description_uses_rdfs_comment_when_present() -> None:
    """A real rdfs:comment (e.g. a MARC 520 summary) beats the generic
    per-class fallback and is surfaced under its own language."""
    graph = Graph()
    ms = HM.MS1
    graph.add((ms, RDF.type, HM.Codicological_Unit))
    graph.add((ms, RDFS.comment, Literal("A 15th-century Sephardic manuscript.", lang="en")))

    drafts = HmoWikibaseExporter().from_graph(graph)
    assert drafts[0].descriptions == {"en": "A 15th-century Sephardic manuscript."}


def test_draft_description_is_truncated_to_wikibase_limit() -> None:
    graph = Graph()
    ms = HM.MS1
    long_comment = "x" * 400
    graph.add((ms, RDF.type, HM.Codicological_Unit))
    graph.add((ms, RDFS.comment, Literal(long_comment, lang="he")))

    drafts = HmoWikibaseExporter().from_graph(graph)
    description = drafts[0].descriptions["he"]
    assert len(description) == 250
    assert description.endswith("…")


def test_resolve_skips_object_property_pointing_at_external_uri() -> None:
    graph = Graph()
    ms = HM.MS1
    external = HM.some_wikidata_reference  # not a typed instance node
    graph.add((ms, RDF.type, HM.Codicological_Unit))
    graph.add((ms, HM.associated_authority, external))

    drafts = HmoWikibaseExporter().from_graph(graph)
    mappings = {
        str(HM.Codicological_Unit): SchemaMappingEntry("Q1"),
        str(HM.associated_authority): SchemaMappingEntry("P9", "wikibase-item"),
    }

    resolved = resolve_against_mappings(drafts, mappings)

    assert resolved[0].claims == []
    assert resolved[0].deferred_links == []
    assert len(resolved[0].skipped_statements) == 1
