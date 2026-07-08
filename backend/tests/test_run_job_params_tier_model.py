"""Tests for tier_model validation in verify job params."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.models.run_job import JOB_KIND_NER_VERIFY
from app.pipeline.run_job_params import prepare_job_params


@pytest.mark.asyncio
async def test_unknown_tier_model_rejected() -> None:
    auth = AsyncMock()
    auth.user.id = uuid.uuid4()
    auth.kek = b"x" * 32
    db = AsyncMock()
    with patch(
        "app.pipeline.run_job_params._validate_verify_params",
        new=AsyncMock(),
    ):
        with pytest.raises(HTTPException) as exc:
            await prepare_job_params(
                db, auth,
                run_id=uuid.uuid4(),
                kind=JOB_KIND_NER_VERIFY,
                params={"action_id": "audit_ner_extraction", "tier_model": "bogus-model"},
            )
    assert exc.value.status_code == 400
    assert "unknown tier-1 model" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_gemini_tier_requires_gemini_key() -> None:
    auth = AsyncMock()
    auth.user.id = uuid.uuid4()
    auth.kek = b"x" * 32
    db = AsyncMock()
    with patch(
        "app.pipeline.run_job_params._resolve_gemini_key",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.pipeline.run_job_params._validate_verify_params",
        new=AsyncMock(),
    ):
        with pytest.raises(HTTPException) as exc:
            await prepare_job_params(
                db, auth,
                run_id=uuid.uuid4(),
                kind=JOB_KIND_NER_VERIFY,
                params={"action_id": "audit_ner_extraction"},
            )
    assert exc.value.status_code == 400
    assert "gemini" in str(exc.value.detail).lower()
