"""Tests for fast Wikidata verification job parameter validation."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.models.run_job import JOB_KIND_WIKIDATA_VERIFY
from app.pipeline.run_job_params import _validate_verify_params
from app.pipeline.verify_job import _open_verify_stream, _requires_gemini_key


@pytest.mark.asyncio
async def test_wikidata_verify_enqueue_does_not_build_scope(db_session) -> None:
    with patch(
        "app.routers.wikidata_studio._fetch_wikidata_verify_items",
        new=AsyncMock(side_effect=AssertionError("scope build belongs to the worker")),
    ) as fetch_scope:
        await _validate_verify_params(
            db_session, uuid.uuid4(), JOB_KIND_WIKIDATA_VERIFY,
            {"action_id": "audit_wikidata_item", "item_ids": ["person::x"]}, auth=None,
        )
    fetch_scope.assert_not_awaited()


@pytest.mark.asyncio
async def test_wikidata_verify_enqueue_rejects_unknown_action(db_session) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _validate_verify_params(
            db_session, uuid.uuid4(), JOB_KIND_WIKIDATA_VERIFY,
            {"action_id": "not_a_real_action"}, auth=None,
        )
    assert exc_info.value.status_code == 400
    assert "unknown action_id" in exc_info.value.detail


@pytest.mark.asyncio
async def test_wikidata_verify_worker_reports_empty_scope(db_session) -> None:
    with patch(
        "app.routers.wikidata_studio._fetch_wikidata_verify_items",
        new=AsyncMock(return_value=([], [])),
    ):
        with pytest.raises(ValueError, match="no Wikidata Studio items in scope"):
            await _open_verify_stream(
                kind=JOB_KIND_WIKIDATA_VERIFY,
                run_id=uuid.uuid4(),
                job_id=uuid.uuid4(),
                session_id="s1",
                params={"action_id": "audit_wikidata_item", "override_cache": True},
                api_key="fake-key",
            )


def test_qubrid_tier_does_not_require_a_gemini_key() -> None:
    assert _requires_gemini_key("moonshotai/Kimi-K2.5") is False
    assert _requires_gemini_key("gemini-3.5-flash") is True
