"""Tests for the JOB_KIND_HMO_ITEM_VERIFY branch of `_validate_verify_params`."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.models.run_job import JOB_KIND_HMO_ITEM_VERIFY
from app.pipeline.run_job_params import _validate_verify_params


@pytest.mark.asyncio
async def test_validate_verify_params_rejects_unknown_action(db_session) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _validate_verify_params(
            db_session, uuid.uuid4(), JOB_KIND_HMO_ITEM_VERIFY,
            {"action_id": "not_a_real_action"}, auth=None,
        )
    assert exc_info.value.status_code == 400
    assert "unknown action_id" in exc_info.value.detail


@pytest.mark.asyncio
async def test_validate_verify_params_rejects_empty_scope(db_session) -> None:
    with (
        patch(
            "app.routers.hmo_studio_items._fetch_verify_items",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.routers.hmo_studio_items._prepare_verify_scope",
            AsyncMock(side_effect=lambda _action, _items: _items),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _validate_verify_params(
                db_session, uuid.uuid4(), JOB_KIND_HMO_ITEM_VERIFY,
                {"action_id": "audit_hmo_wikibase_item"}, auth=None,
            )
    assert exc_info.value.status_code == 400
    assert "no HMO items in scope" in exc_info.value.detail


@pytest.mark.asyncio
async def test_validate_verify_params_accepts_nonempty_scope(db_session) -> None:
    items = [{"local_id": "QDraft_A", "source_uri": "http://x#A"}]
    with (
        patch(
            "app.routers.hmo_studio_items._fetch_verify_items",
            AsyncMock(return_value=items),
        ) as fetch_mock,
        patch(
            "app.routers.hmo_studio_items._prepare_verify_scope",
            AsyncMock(side_effect=lambda _action, _items: _items),
        ),
    ):
        await _validate_verify_params(
            db_session, uuid.uuid4(), JOB_KIND_HMO_ITEM_VERIFY,
            {"action_id": "audit_hmo_wikibase_item", "item_ids": ["QDraft_A"]}, auth=None,
        )
    fetch_mock.assert_awaited_once()
