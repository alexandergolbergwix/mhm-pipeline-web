"""Bounded work and cleanup for automatic Publication assessments."""
import asyncio
from contextlib import aclosing

import pytest

from app.pipeline.wikidata_publication_auto_job import _completed_assessments


@pytest.mark.asyncio
async def test_fast_items_complete_without_waiting_for_slow_first_item():
    started = []
    stopped = []
    release = asyncio.Event()

    async def assess(item):
        started.append(item)
        try:
            if item != 1:
                await release.wait()
            return item
        finally:
            stopped.append(item)

    async with aclosing(_completed_assessments(range(6), assess)) as results:
        assert await asyncio.wait_for(anext(results), 1) == 1
        assert sorted(started) == [0, 1, 2]
    assert sorted(stopped) == [0, 1, 2]
    assert started == [0, 1, 2]


@pytest.mark.asyncio
async def test_failed_worker_cancels_and_awaits_its_peers():
    all_started = asyncio.Event()
    started, stopped = [], []

    async def assess(item):
        started.append(item)
        if len(started) == 3:
            all_started.set()
        try:
            await all_started.wait()
            if item == 1:
                raise RuntimeError('fixture assessment failure')
            await asyncio.Event().wait()
        finally:
            stopped.append(item)

    with pytest.raises(RuntimeError, match='fixture assessment failure'):
        async with aclosing(_completed_assessments(range(6), assess)) as results:
            await asyncio.wait_for(anext(results), 1)
    assert sorted(stopped) == [0, 1, 2]
