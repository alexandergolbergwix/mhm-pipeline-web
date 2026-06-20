"""Unit tests for authority auto-approve rule filtering."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.routers.runs import _apply_auto_approve_rule, _match_source_count
from app.schemas.runs import AuthorityAutoApproveRule


def _match(
    *,
    approved: bool = False,
    confidence: str = "high",
    entity_kind: str = "person",
    source: str = "mazal",
    payload: dict | None = None,
    match_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=match_id or uuid.uuid4(),
        approved=approved,
        confidence=confidence,
        entity_kind=entity_kind,
        source=source,
        payload=payload or {},
    )


def test_source_count_falls_back_to_sources_list() -> None:
    m = _match(payload={"sources": ["mazal", "viaf"]})
    assert _match_source_count(m, m.payload) == 2


def test_source_count_falls_back_to_primary_source() -> None:
    m = _match(source="wikidata", payload={})
    assert _match_source_count(m, m.payload) == 1


def test_auto_approve_without_payload_source_count_still_matches() -> None:
    m = _match(payload={"sources": ["mazal"], "ai_verdict": {"overall": "full"}})
    rule = AuthorityAutoApproveRule(require_ai_pass=True)
    matched = _apply_auto_approve_rule([m], rule)
    assert matched == [m]


def test_auto_approve_respects_visible_scope() -> None:
    visible = _match(payload={"sources": ["mazal"]})
    hidden = _match(payload={"sources": ["mazal"]})
    rule = AuthorityAutoApproveRule(match_ids=[visible.id])
    matched = _apply_auto_approve_rule([visible, hidden], rule)
    assert matched == [visible]


def test_auto_approve_skips_already_approved() -> None:
    m = _match(approved=True, payload={"sources": ["mazal"]})
    rule = AuthorityAutoApproveRule()
    assert _apply_auto_approve_rule([m], rule) == []
