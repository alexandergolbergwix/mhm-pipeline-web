"""Authority AI verdict cache invalidation."""

from __future__ import annotations

import uuid

from app.models.run import AuthorityMatch
from app.pipeline.authority_verdict_cache import (
    AUTHORITY_VERDICT_SCHEMA,
    authority_verdict_input_fingerprint,
    authority_verdict_query_summary,
    sanitise_stale_authority_verdict,
)


def _match(**overrides: object) -> AuthorityMatch:
    base = {
        "id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "control_number": "990000403370205171",
        "entity_text": "Moses Maimonides",
        "entity_kind": "person",
        "role": "author",
        "matched_name": "Maimonides, Moses",
        "mazal_id": "123",
        "viaf_id": "",
        "wikidata_qid": "Q127398",
        "confidence": "high",
        "source": "mazal",
        "payload": {"guard_flags": [], "sources": ["mazal"]},
    }
    base.update(overrides)
    return AuthorityMatch(**base)


def test_query_summary_changes_when_entity_text_changes() -> None:
    match = _match()
    a = authority_verdict_input_fingerprint(match, "gemini-3.5-flash")
    match.entity_text = "Rambam"
    b = authority_verdict_input_fingerprint(match, "gemini-3.5-flash")
    assert a != b


def test_query_summary_changes_when_marc_context_changes() -> None:
    match = _match()
    a = authority_verdict_input_fingerprint(
        match,
        "gemini-3.5-flash",
        marc_context={"authors": "Moses Maimonides"},
    )
    b = authority_verdict_input_fingerprint(
        match,
        "gemini-3.5-flash",
        marc_context={"authors": "Moses Maimonides", "notes": "Colophon note"},
    )
    assert a != b


def test_query_summary_includes_schema_salt() -> None:
    summary = authority_verdict_query_summary(_match(), "gemini-3.5-flash")
    assert summary["authority_verdict_schema"] == AUTHORITY_VERDICT_SCHEMA


def test_sanitise_stale_authority_verdict_hides_mismatched_key() -> None:
    match = _match(payload={
        "ai_verdict": {
            "overall": "full",
            "cache_key": "stale-eval-agent-prompt-hash",
            "model": "gemini-3.5-flash",
        },
    })
    assert sanitise_stale_authority_verdict(match) is None


def test_sanitise_stale_authority_verdict_keeps_matching_key() -> None:
    match = _match()
    fp = authority_verdict_input_fingerprint(match, "gemini-3.5-flash")
    match.payload = {
        **(match.payload or {}),
        "ai_verdict": {
            "overall": "full",
            "cache_key": fp,
            "model": "gemini-3.5-flash",
        },
    }
    kept = sanitise_stale_authority_verdict(match)
    assert kept is not None
    assert kept["cache_key"] == fp
