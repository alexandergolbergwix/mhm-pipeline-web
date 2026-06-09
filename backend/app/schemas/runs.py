"""Pydantic shapes for the run lifecycle + approvals."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RunStatus = Literal["pending", "running", "succeeded", "failed"]


class RunListItem(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    status: RunStatus
    record_count: int
    match_count: int
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class AuthorityMatchResponse(BaseModel):
    id: uuid.UUID
    control_number: str
    entity_text: str
    entity_kind: str
    role: str
    matched_name: str
    mazal_id: str
    viaf_id: str
    wikidata_qid: str
    confidence: str
    source: str
    payload: dict[str, Any] = Field(default_factory=dict)
    approved: bool
    approved_by: uuid.UUID | None
    approved_at: datetime | None


class RunDetail(RunListItem):
    matches: list[AuthorityMatchResponse]


class ApprovalUpdate(BaseModel):
    approved: bool


class ApprovalBatch(BaseModel):
    match_ids: list[uuid.UUID]
    approved: bool


class RunMarcRecord(BaseModel):
    control_number: str
    marc: dict[str, Any]


class AuthorityMatchEdit(BaseModel):
    """Partial update of an authority match — every field optional."""
    matched_name: str | None = None
    mazal_id: str | None = None
    viaf_id: str | None = None
    wikidata_qid: str | None = None
    confidence: Literal["high", "medium", "low"] | None = None
    role: str | None = None
    entity_text: str | None = None


class RecordEdit(BaseModel):
    """MARC JSON replacement — UI sends the dict, server stores it."""
    marc: dict[str, Any]


class AuthorityAutoApproveRule(BaseModel):
    """Rule for bulk-approving authority candidates by predicate."""
    confidence_levels: list[Literal["high", "medium", "low"]] = Field(
        default_factory=lambda: ["high", "medium", "low"],
    )
    sources: list[str] = Field(default_factory=list)     # empty = any source
    entity_kinds: list[str] = Field(default_factory=list) # empty = any kind
    min_source_count: int = Field(default=1, ge=1, le=4)
    require_ai_pass: bool = False
    respect_ai_fail: bool = True


class AiVerdictResponse(BaseModel):
    """Returned by /runs/{id}/matches/{mid}/ai-verify and embedded in
    payload['ai_verdict'] on the match itself."""

    overall: Literal["full", "partial", "fail", "abstain"]
    reasoning: str
    model: str
    judged_at: str
    fallback: bool = False    # true when we couldn't reach Gemini

