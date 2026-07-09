"""HMO schema AI verdict cache invalidation."""

from __future__ import annotations

from app.pipeline.hmo_schema_verdict_cache import (
    HMO_SCHEMA_VERDICT_SCHEMA,
    sanitise_stale_schema_verdict,
    schema_verdict_input_fingerprint,
    schema_verdict_query_summary,
)


def test_query_summary_changes_when_description_changes() -> None:
    entry = {
        "ontology_uri": "http://example.org#folio_number",
        "entity_kind": "property",
        "label": "folio number",
        "description": "Count of folios",
        "status": "would_create",
    }
    a = schema_verdict_input_fingerprint(entry, "gemini-3.5-flash")
    entry["description"] = "Number of folios in the manuscript"
    b = schema_verdict_input_fingerprint(entry, "gemini-3.5-flash")
    assert a != b


def test_query_summary_includes_schema_salt() -> None:
    summary = schema_verdict_query_summary(
        {"ontology_uri": "x", "entity_kind": "class", "label": "y"},
        "gemini-3.5-flash",
    )
    assert summary["hmo_schema_verdict_schema"] == HMO_SCHEMA_VERDICT_SCHEMA


def test_sanitise_stale_schema_verdict_hides_mismatched_key() -> None:
    entry = {
        "ontology_uri": "http://example.org#folio_number",
        "entity_kind": "property",
        "label": "folio number",
        "description": "Count of folios",
        "status": "would_create",
    }
    stored = {
        "overall": "full",
        "cache_key": "stale-eval-agent-prompt-hash",
        "model": "gemini-3.5-flash",
        "evaluator": "hmo_wikibase_schema",
    }
    assert sanitise_stale_schema_verdict(entry, stored) is None
