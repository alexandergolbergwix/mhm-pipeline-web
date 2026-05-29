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


class AiVerdictResponse(BaseModel):
    """Returned by /runs/{id}/matches/{mid}/ai-verify and embedded in
    payload['ai_verdict'] on the match itself."""

    overall: Literal["full", "partial", "fail", "abstain"]
    reasoning: str
    model: str
    judged_at: str
    fallback: bool = False    # true when we couldn't reach Gemini

