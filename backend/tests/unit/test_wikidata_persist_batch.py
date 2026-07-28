"""Wikidata verdict persist must not block the eval-agent stdout reader."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.wikidata_item_verify import WikidataVerdictPersistBatch


@pytest.mark.asyncio
async def test_enqueue_schedules_background_flush_without_blocking() -> None:
    batch = WikidataVerdictPersistBatch(
        run_id=uuid.uuid4(),
        items_by_id={},
        judge_model="gemini-3.5-flash",
        flush_size=1,
    )
    gate = asyncio.Event()
    original_flush = batch.flush

    async def slow_flush() -> None:
        gate.set()
        await asyncio.sleep(0.05)
        await original_flush()

    with patch.object(batch, "flush", side_effect=slow_flush):
        batch.enqueue({"candidate": {"_local_id": "ms:1"}, "verdict": {"overall": "pass"}})
        await asyncio.sleep(0)
        assert gate.is_set()
        if batch._pending_flush is not None:
            await batch._pending_flush
