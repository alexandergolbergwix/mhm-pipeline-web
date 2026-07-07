"""Tests for resolving HMO exporter drafts against a live schema mapping
(Phase 4 — see dev-docs/hmo-wikibase-studio-plan.md).
"""

from __future__ import annotations

import pytest
from rdflib import Graph, Literal, RDF, RDFS

from converter.config.namespaces import CIDOC, HM, LRMOO
from converter.wikibase.hmo_exporter import (
    HMO_SOURCE_URI,
    HmoWikibaseExporter,
    resolve_against_mappings,
)
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
        HMO_SOURCE_URI: SchemaMappingEntry("P99", "string"),
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
    source_uri_claim = ms_entity.claims[-1]
    assert source_uri_claim.property_id == "P99"
    assert source_uri_claim.datatype == "string"
    assert source_uri_claim.value == ms_entity.source_uri


def test_resolve_skips_source_uri_claim_gracefully_when_unmapped() -> None:
    """A database that hasn't run the schema bootstrap since
    ``hmo_source_uri`` was added must not fail the whole batch — it's a
    graceful skip, unlike an unmapped class/statement property URI."""
    graph = Graph()
    ms = HM.MS1
    graph.add((ms, RDF.type, HM.Codicological_Unit))

    drafts = HmoWikibaseExporter().from_graph(graph)
    mappings = {str(HM.Codicological_Unit): SchemaMappingEntry("Q1")}

    resolved = resolve_against_mappings(drafts, mappings)

    assert resolved[0].claims == []
    assert any("hmo_source_uri" in note for note in resolved[0].skipped_statements)


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


def test_draft_labels_mirror_hebrew_into_english_when_missing() -> None:
    graph = Graph()
    work = HM.Work1
    graph.add((work, RDF.type, LRMOO.F1_Work))
    graph.add((work, RDFS.label, Literal("ספר תהילים", lang="he")))

    drafts = HmoWikibaseExporter().from_graph(graph)
    assert len(drafts) == 1
    assert drafts[0].labels["he"] == "ספר תהילים"
    assert drafts[0].labels["en"] == "ספר תהילים"


def test_draft_labels_keep_existing_english_untouched() -> None:
    graph = Graph()
    person = HM.Person1
    graph.add((person, RDF.type, CIDOC.E21_Person))
    graph.add((person, RDFS.label, Literal("רבי משה", lang="he")))
    graph.add((person, RDFS.label, Literal("Moses Maimonides", lang="en")))

    drafts = HmoWikibaseExporter().from_graph(graph)
    assert drafts[0].labels == {"he": "רבי משה", "en": "Moses Maimonides"}


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


def test_draft_label_is_truncated_to_wikibase_limit() -> None:
    """Long Hebrew labels must never exceed Wikibase's 250-char cap."""
    graph = Graph()
    node = HM.MS1
    long_label = "x" * 300
    graph.add((node, RDF.type, HM.Codicological_Unit))
    graph.add((node, RDFS.label, Literal(long_label, lang="he")))

    drafts = HmoWikibaseExporter().from_graph(graph)
    label = drafts[0].labels["he"]
    assert len(label) == 250
    assert label.endswith("…")
    assert len(drafts[0].labels["en"]) == 250


def test_paradigm_bridge_nodes_are_not_exported_as_items() -> None:
    graph = Graph()
    bridge = HM.Bridge1
    graph.add((bridge, RDF.type, HM.ParadigmBridge))
    graph.add((bridge, RDFS.label, Literal("Paradigm bridge", lang="en")))

    drafts = HmoWikibaseExporter().from_graph(graph)
    assert drafts == []


def test_labels_never_use_und_language_code() -> None:
    graph = Graph()
    node = HM.MS1
    graph.add((node, RDF.type, HM.Codicological_Unit))
    graph.add((node, RDFS.label, Literal("שנה", lang=None)))

    drafts = HmoWikibaseExporter().from_graph(graph)
    assert "und" not in drafts[0].labels
    assert "en" in drafts[0].labels


def test_resolve_truncates_overlong_string_and_monolingualtext_claim_values() -> None:
    """Regression: 'x' claims (e.g. hm:tradition_name) and monolingualtext
    claims (e.g. the rdfs:comment-derived 'comment' claim) both hit
    Wikibase's default 400-char string-value cap on real long titles —
    truncate rather than let the whole item write fail."""
    graph = Graph()
    ms = HM.MS1
    long_value = "y" * 500
    graph.add((ms, RDF.type, HM.Codicological_Unit))
    graph.add((ms, HM.tradition_name, Literal(long_value)))
    graph.add((ms, HM.intervention_description, Literal(long_value, lang="he")))

    drafts = HmoWikibaseExporter().from_graph(graph)
    mappings = {
        str(HM.Codicological_Unit): SchemaMappingEntry("Q1"),
        str(HM.tradition_name): SchemaMappingEntry("P10", "string"),
        str(HM.intervention_description): SchemaMappingEntry("P11", "monolingualtext"),
        HMO_SOURCE_URI: SchemaMappingEntry("P99", "string"),
    }

    resolved = resolve_against_mappings(drafts, mappings)
    claims_by_pid = {c.property_id: c for c in resolved[0].claims}

    string_claim = claims_by_pid["P10"]
    assert len(string_claim.value) == 400
    assert string_claim.value.endswith("…")

    mono_claim = claims_by_pid["P11"]
    assert len(mono_claim.value["text"]) == 400
    assert mono_claim.value["text"].endswith("…")


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
        HMO_SOURCE_URI: SchemaMappingEntry("P99", "string"),
    }

    resolved = resolve_against_mappings(drafts, mappings)

    assert resolved[0].claims[0].property_id == "P99"
    assert resolved[0].deferred_links == []
    assert len(resolved[0].skipped_statements) == 1
