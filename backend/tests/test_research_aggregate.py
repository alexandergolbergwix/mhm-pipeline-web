"""Unit tests for cross-source aggregation (RDF + Wikibase + Wikidata).

Covers the merge/identity-resolution logic, the per-source providers, the
graceful-degradation contract for Wikibase, and the aggregated summary shape.
"""
from __future__ import annotations

import asyncio

import rdflib

from app.pipeline.research_aggregate import (
    SOURCE_RDF,
    SOURCE_WIKIBASE,
    SOURCE_WIKIDATA,
    ProviderEntity,
    build_aggregated_summary,
    compute_aggregated_summary,
    merge_entities,
    wikibase_provider,
    wikidata_provider,
)

HM = "http://www.ontology.org.il/HebrewManuscripts/2025-12-06#"
LRMOO = "http://iflastandards.info/ns/lrm/lrmoo/"
CIDOC = "http://www.cidoc-crm.org/cidoc-crm/"


def _ms(source, *, qid=None, cn=None, label="MS", uri=None):
    return ProviderEntity("manuscript", label, source, qid=qid, control_number=cn, raw_uri=uri)


def _person(source, *, qid=None, viaf=None, nli=None, label="P", uri=None):
    return ProviderEntity(
        "person", label, source, qid=qid, viaf_id=viaf, nli_authority_id=nli, raw_uri=uri,
    )


class TestMergeEntities:
    def test_rdf_cn_merges_with_wikidata_qid_plus_cn(self):
        """An RDF manuscript known only by control number and a Wikidata
        manuscript with QID + same control number collapse to ONE entity."""
        merged = merge_entities([
            _ms(SOURCE_RDF, cn="990001"),
            _ms(SOURCE_WIKIDATA, qid="Q42", cn="990001"),
        ])
        assert len(merged) == 1
        assert merged[0].sources == {SOURCE_RDF, SOURCE_WIKIDATA}
        assert merged[0].id_key == "qid:Q42"  # QID wins precedence

    def test_distinct_control_numbers_do_not_merge_on_shared_label(self):
        """Two manuscripts with different control numbers but the same label
        must stay separate — the label is a fallback only."""
        merged = merge_entities([
            _ms(SOURCE_RDF, cn="111", label="Genesis"),
            _ms(SOURCE_RDF, cn="222", label="Genesis"),
        ])
        assert len(merged) == 2

    def test_person_merges_on_viaf(self):
        merged = merge_entities([
            _person(SOURCE_RDF, viaf="123", uri="u1"),
            _person(SOURCE_WIKIDATA, qid="Q9", viaf="123"),
        ])
        assert len(merged) == 1
        assert merged[0].sources == {SOURCE_RDF, SOURCE_WIKIDATA}

    def test_label_only_entities_merge_when_no_strong_id(self):
        merged = merge_entities([
            ProviderEntity("work", "Mishneh Torah", SOURCE_RDF, raw_uri="w1"),
            ProviderEntity("work", "mishneh  torah", SOURCE_WIKIDATA, qid=None, raw_uri="w2"),
        ])
        assert len(merged) == 1

    def test_label_less_entities_stay_distinct_via_raw_key(self):
        merged = merge_entities([
            ProviderEntity("work", "", SOURCE_RDF, raw_uri="w1"),
            ProviderEntity("work", "", SOURCE_RDF, raw_uri="w2"),
        ])
        assert len(merged) == 2


class TestWikidataProvider:
    def test_excludes_items_without_real_qid(self):
        items = [
            {"entity_type": "manuscript", "existing_qid": "", "local_id": "1",
             "labels": {"he": "א"}},
            {"entity_type": "person", "existing_qid": None, "labels": {"he": "ב"}},
            {"entity_type": "work", "existing_qid": "notaqid", "labels": {}},
            {"entity_type": "person", "existing_qid": "Q5", "labels": {"en": "Maimonides"},
             "statements": [{"property": "P214", "value": "99"}]},
        ]
        ents = wikidata_provider(items)
        assert len(ents) == 1
        assert ents[0].qid == "Q5"
        assert ents[0].viaf_id == "99"

    def test_extracts_manuscript_control_number_from_local_id(self):
        ents = wikidata_provider([
            {"entity_type": "manuscript", "existing_qid": "Q7", "local_id": "990123", "labels": {}},
        ])
        assert ents[0].control_number == "990123"


class TestWikibaseProvider:
    def test_returns_empty_when_unconfigured(self):
        async def runner(url, query):  # pragma: no cover - never called
            raise AssertionError("should not be called when url empty")

        assert asyncio.run(wikibase_provider("", runner)) == []

    def test_returns_empty_on_http_error(self):
        async def runner(url, query):
            raise RuntimeError("boom")

        assert asyncio.run(wikibase_provider("http://wb.example/sparql", runner)) == []

    def test_parses_bindings_when_configured(self):
        async def runner(url, query):
            return {"results": {"bindings": [
                {"uri": {"value": f"{HM}MS_1"}, "label": {"value": "Item"}},
            ]}}

        ents = asyncio.run(wikibase_provider("http://wb.example/sparql", runner))
        # one entity per type that returns a row (runner ignores the type)
        assert all(e.source == SOURCE_WIKIBASE for e in ents)
        assert any(e.entity_type == "manuscript" for e in ents)


class TestBuildAggregatedSummary:
    def test_invariant_max_le_total_le_sum(self):
        merged = merge_entities([
            _ms(SOURCE_RDF, cn="1"),
            _ms(SOURCE_WIKIDATA, qid="Q1", cn="1"),  # merges with above
            _ms(SOURCE_WIKIDATA, qid="Q2", cn="2"),
        ])
        summary = build_aggregated_summary(
            merged, {"scribe": 0, "owner": 0, "author": 0}, 100,
            {SOURCE_RDF: True, SOURCE_WIKIBASE: False, SOURCE_WIKIDATA: True},
        )
        ms = summary["by_type"]["manuscript"]
        assert ms["total"] == 2
        assert ms["by_source"] == {"rdf": 1, "wikibase": 0, "wikidata": 2}
        vals = list(ms["by_source"].values())
        assert max(vals) <= ms["total"] <= sum(vals)
        assert summary["total_manuscripts"] == 2


class TestComputeAggregatedSummaryOverRdf:
    def _graph(self) -> rdflib.Graph:
        g = rdflib.Graph()
        g.parse(data=f"""
        @prefix hm: <{HM}> .
        @prefix lrmoo: <{LRMOO}> .
        @prefix cidoc: <{CIDOC}> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        <{HM}MS_990001> a lrmoo:F4_Manifestation_Singleton ; rdfs:label "כתב יד" ;
            hm:has_work <{HM}W1> .
        <{HM}W1> rdfs:label "ספר" .
        <{HM}Person_x> a cidoc:E21_Person ; rdfs:label "אדם" .
        <{HM}Place_y> a cidoc:E53_Place ; rdfs:label "מקום" .
        """, format="turtle")
        return g

    def test_rdf_only(self):
        s = compute_aggregated_summary(self._graph(), [], [], wikibase_configured=False)
        assert s["total_manuscripts"] == 1
        assert s["total_works"] == 1
        assert s["total_persons"] == 1
        assert s["total_places"] == 1
        assert s["sources_available"] == {"rdf": True, "wikibase": False, "wikidata": False}

    def test_wikidata_manuscript_merges_into_rdf_via_control_number(self):
        studio = [{"entity_type": "manuscript", "existing_qid": "Q100",
                   "local_id": "990001", "labels": {"he": "כתב יד"}}]
        s = compute_aggregated_summary(self._graph(), studio, [], wikibase_configured=False)
        ms = s["by_type"]["manuscript"]
        assert ms["total"] == 1                       # merged, not double-counted
        assert ms["by_source"] == {"rdf": 1, "wikibase": 0, "wikidata": 1}
        assert s["sources_available"]["wikidata"] is True
