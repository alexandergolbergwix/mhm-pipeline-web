from __future__ import annotations

import json

from eval_agent.evaluators import REGISTRY
from eval_agent.evaluators.hmo_wikibase_schema import HmoWikibaseSchemaEvaluator
from eval_agent.ingest import hmo_wikibase_schema, pipeline_run


def test_discover_accepts_hmo_wikibase_schema_without_ner_or_authority(tmp_path):
    (tmp_path / "marc_extracted.json").write_text("[]", encoding="utf-8")
    (tmp_path / "hmo_wikibase_schema.json").write_text(
        json.dumps({"entries": []}), encoding="utf-8"
    )

    run = pipeline_run.discover(tmp_path)

    assert run.ner_results is None
    assert run.authority_results is None
    assert run.wikidata_items is None
    assert run.hmo_wikibase_schema == tmp_path.resolve() / "hmo_wikibase_schema.json"


def test_load_includes_skipped_and_failed_entries(tmp_path):
    path = tmp_path / "hmo_wikibase_schema.json"
    path.write_text(
        json.dumps(
            {
                "entries": [
                    {"ontology_uri": "u1", "entity_kind": "class", "label": "A", "status": "created", "wikibase_id": "Q1"},
                    {"ontology_uri": "u2", "entity_kind": "property", "label": "B", "status": "skipped", "wikibase_id": "P1"},
                    {"ontology_uri": "u3", "entity_kind": "property", "label": "C", "status": "failed", "wikibase_id": None},
                    {"ontology_uri": "u4", "entity_kind": "class", "label": "D", "status": "would_create", "wikibase_id": None},
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = hmo_wikibase_schema.load(path)

    assert {e["ontology_uri"] for e in loaded} == {"u1", "u2", "u3", "u4"}


def test_evaluator_emits_one_candidate_per_entry():
    evaluator = HmoWikibaseSchemaEvaluator()
    entry = {
        "ontology_uri": "http://www.ontology.org.il/HebrewManuscripts/2025-12-06#has_folio_count",
        "entity_kind": "property",
        "label": "has folio count",
        "description": "Number of folios in the manuscript.",
        "aliases": ["מספר דפים"],
        "datatype": "string",
        "wikibase_id": "P42",
        "status": "created",
        "property_kind": "DatatypeProperty",
        "range_uri": "http://www.w3.org/2001/XMLSchema#integer",
    }

    candidates = list(
        evaluator.extract_candidates(ner_record=entry, marc_record={}, threshold=0.9)
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.evaluator_id == "hmo_wikibase_schema"
    assert candidate.sub_type == "property"
    assert candidate.payload["datatype"] == "string"
    assert candidate.payload["description"] == "Number of folios in the manuscript."
    assert candidate.payload["aliases"] == ["מספר דפים"]
    assert candidate.payload["property_kind"] == "DatatypeProperty"
    assert candidate.marc_context == {}


def test_evaluator_is_registered():
    assert REGISTRY["hmo_wikibase_schema"] is HmoWikibaseSchemaEvaluator


def test_build_prompt_includes_rubric_and_prediction_fields():
    evaluator = HmoWikibaseSchemaEvaluator()
    entry = {
        "ontology_uri": "http://example.org#Manuscript",
        "entity_kind": "class",
        "label": "Manuscript",
        "description": "A unique physical manuscript object.",
        "aliases": ["כתב יד"],
        "datatype": None,
        "wikibase_id": None,
        "status": "would_create",
        "parent_uri": "http://example.org#HumanMadeObject",
    }
    candidate = next(
        iter(evaluator.extract_candidates(ner_record=entry, marc_record={}, threshold=0.9))
    )

    prompt = evaluator.build_prompt(candidate)

    assert "HMO Wikibase Schema Rubric" in prompt
    assert "Manuscript" in prompt
    assert "description:" in prompt
    assert "A unique physical manuscript object." in prompt
    assert "aliases:" in prompt
    assert "כתב יד" in prompt
    assert "parent URI:" in prompt
    assert "(class — no datatype)" in prompt
    assert "(dry-run — not yet created)" in prompt
