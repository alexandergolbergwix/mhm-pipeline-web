"""Wire contract for advisory Publication reviews."""
from datetime import datetime
from typing import Literal
from pydantic import Field
from app.schemas.publication import PublicationSchema, PublicationForeignQidConsent


class StartPublicationAiReview(PublicationSchema):
    plan_id: str = Field(min_length=1, max_length=128)
    plan_digest: str = Field(min_length=1, max_length=128)
    tier_model: str | None = Field(default=None, max_length=128)
    force_refresh: bool = False
    automatic: bool = False
    verification_model: str | None = Field(default=None, max_length=128)


class PublicationAiReviewItem(PublicationSchema):
    entity_key: str
    label: str
    qid: str | None = None
    status: Literal['recommended', 'review_required', 'lookup_resolved', 'error', 'reuse_existing', 'create', 'deferred']
    reason: str
    consent: PublicationForeignQidConsent | None = None
    resolution: dict[str, object] | None = None
    evidence: list[dict[str, object]] = Field(default_factory=list)
    decisions: list[dict[str, object]] = Field(default_factory=list)


class PublicationAiReport(PublicationSchema):
    publication_id: str
    plan_id: str
    plan_digest: str
    release_digest: str
    tier_model: str
    created_at: datetime
    automatic: bool = False
    result_publication_id: str | None = None
    policy_version: str | None = None
    items: list[PublicationAiReviewItem] = Field(default_factory=list)


class PublicationAiReviewState(PublicationSchema):
    job_id: str | None = None
    status: Literal['queued', 'running', 'succeeded', 'failed', 'cancelled'] | None = None
    phase: str | None = None
    message: str | None = None
    processed: int = 0
    total: int = 0
    report: PublicationAiReport | None = None
    error: str | None = None
