"""Tests for the JOB_KIND_HMO_ITEM_VERIFY branch of the background verify
job dispatcher (`_open_verify_stream`).

Mirrors the existing JOB_KIND_WIKIDATA_VERIFY branch: fetch the run's
items, apply the action's scope filter, load MARC records for grounding,
and hand everything to `hmo_item_verify_event_stream`.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.models.run_job import JOB_KIND_HMO_ITEM_VERIFY
from app.pipeline.verify_job import _open_verify_stream


@pytest.mark.asyncio
async def test_open_verify_stream_returns_none_for_unknown_action(db_session) -> None:
    stream = await _open_verify_stream(
        kind=JOB_KIND_HMO_ITEM_VERIFY,
        run_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        session_id="s1",
        params={"action_id": "not_a_real_action", "override_cache": True},
        api_key="fake-key",
    )
    assert stream is None


@pytest.mark.asyncio
async def test_open_verify_stream_returns_none_when_scope_is_empty(db_session) -> None:
    with (
        patch(
            "app.routers.hmo_studio_items._fetch_verify_items",
            AsyncMock(return_value=[{"local_id": "QDraft_A", "source_uri": "http://x#A"}]),
        ),
        patch(
            "app.routers.hmo_studio_items._prepare_verify_scope",
            AsyncMock(return_value=[]),
        ),
    ):
        stream = await _open_verify_stream(
            kind=JOB_KIND_HMO_ITEM_VERIFY,
            run_id=uuid.uuid4(),
            job_id=uuid.uuid4(),
            session_id="s1",
            params={"action_id": "audit_hmo_wikibase_item", "override_cache": True},
            api_key="fake-key",
        )
    assert stream is None


@pytest.mark.asyncio
async def test_open_verify_stream_wires_hmo_action_end_to_end(db_session) -> None:
    run_id = uuid.uuid4()
    items = [{"local_id": "QDraft_A", "source_uri": "http://x#A"}]
    marc_records = [{"control_number": "1"}]
    sentinel_stream = object()

    with (
        patch(
            "app.routers.hmo_studio_items._fetch_verify_items",
            AsyncMock(return_value=items),
        ) as fetch_mock,
        patch(
            "app.routers.hmo_studio_items._prepare_verify_scope",
            AsyncMock(side_effect=lambda _action, _items: _items),
        ) as scope_mock,
        patch(
            "app.routers.hmo_studio_items._load_marc_records",
            AsyncMock(return_value=marc_records),
        ) as marc_mock,
        patch(
            "app.pipeline.hmo_item_verify.hmo_item_verify_event_stream",
            return_value=sentinel_stream,
        ) as stream_mock,
    ):
        stream = await _open_verify_stream(
            kind=JOB_KIND_HMO_ITEM_VERIFY,
            run_id=run_id,
            job_id=uuid.uuid4(),
            session_id="s1",
            params={
                "action_id": "audit_hmo_wikibase_item",
                "item_ids": ["QDraft_A"],
                "override_cache": True,
            },
            api_key="fake-key",
        )

    assert stream is sentinel_stream
    fetch_mock.assert_awaited_once()
    scope_mock.assert_awaited_once()
    marc_mock.assert_awaited_once()
    stream_mock.assert_called_once()
    call_kwargs = stream_mock.call_args.kwargs
    assert call_kwargs["items"] == items
    # override_cache=True must skip the per-item inference-cache lookup
    # and treat every item as uncached, so the eval-agent judges it fresh.
    assert call_kwargs["uncached_items"] == items
    assert call_kwargs["pre_cached"] == []
    assert call_kwargs["marc_records"] == marc_records
    assert call_kwargs["session_id"] == "s1"
    assert call_kwargs["run_id"] == str(run_id)


@pytest.mark.asyncio
async def test_open_verify_stream_uses_inference_cache_when_not_overridden(db_session) -> None:
    """When override_cache is False, an inference-cache hit must route
    the item into pre_cached (skipping a fresh Gemini call) rather than
    uncached_items."""
    run_id = uuid.uuid4()
    items = [{"local_id": "QDraft_A", "source_uri": "http://x#A"}]
    cached_verdict = {"overall": "pass", "reasoning": "looks fine"}

    with (
        patch(
            "app.routers.hmo_studio_items._fetch_verify_items",
            AsyncMock(return_value=items),
        ),
        patch(
            "app.routers.hmo_studio_items._prepare_verify_scope",
            AsyncMock(side_effect=lambda _action, _items: _items),
        ),
        patch(
            "app.routers.hmo_studio_items._load_marc_records",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.pipeline.inference_cache.read_from_inference_cache",
            AsyncMock(return_value=cached_verdict),
        ),
        patch(
            "app.pipeline.hmo_item_verify.hmo_item_verify_event_stream",
            return_value=object(),
        ) as stream_mock,
    ):
        await _open_verify_stream(
            kind=JOB_KIND_HMO_ITEM_VERIFY,
            run_id=run_id,
            job_id=uuid.uuid4(),
            session_id="s1",
            params={"action_id": "audit_hmo_wikibase_item", "override_cache": False},
            api_key="fake-key",
        )

    call_kwargs = stream_mock.call_args.kwargs
    assert call_kwargs["uncached_items"] == []
    assert call_kwargs["pre_cached"] == [(items[0], cached_verdict)]
