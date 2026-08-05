"""Rule W-167 — the stream route and the job worker partition identically.

The cache-hit loop was copy-pasted between `start_wikidata_verify` and
`verify_job`, so any rule added to one path was silently missing from the other.
These tests pin the shared function and the re-judge trigger it carries.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, patch

from app.pipeline.wikidata_verify_scope import partition_wikidata_verify_cache


def _item(local_id: str, status: str) -> dict:
    return {"local_id": local_id, "_local_id": local_id, "_duplicate_status": status}


def _partition(items, hits, *, override_cache=False):
    async def fake_read(_db, *, kind, query_summary):
        return hits.get(query_summary["local_id"])

    with patch(
        "app.pipeline.wikidata_verify_scope.read_from_inference_cache", fake_read,
    ):
        return asyncio.run(
            partition_wikidata_verify_cache(
                AsyncMock(), items,
                judge_model="m", evaluator_id="wikidata_item",
                override_cache=override_cache,
            ),
        )


def test_a_hit_is_reused_when_the_probe_was_already_conclusive() -> None:
    items = [_item("a", "absent")]
    hits = {"a": {"verdict": {"overall": "full", "duplicate_class": "probed-conclusive"}}}
    pre_cached, uncached, stats = _partition(items, hits)
    assert [i["local_id"] for i, _ in pre_cached] == ["a"]
    assert uncached == []
    assert stats["rejudge_duplicate_resolved"] == 0


def test_a_verdict_judged_without_a_probe_is_rejudged_once_one_exists() -> None:
    items = [_item("a", "candidates_found")]
    hits = {"a": {"verdict": {"overall": "partial", "duplicate_class": "unknown"}}}
    pre_cached, uncached, stats = _partition(items, hits)
    assert pre_cached == []
    assert [i["local_id"] for i in uncached] == ["a"]
    assert stats["rejudge_duplicate_resolved"] == 1


def test_a_still_unknown_probe_is_not_rejudged() -> None:
    items = [_item("a", "not_run")]
    hits = {"a": {"verdict": {"overall": "partial", "duplicate_class": "unknown"}}}
    pre_cached, uncached, stats = _partition(items, hits)
    assert len(pre_cached) == 1
    assert stats["rejudge_duplicate_resolved"] == 0


def test_a_miss_goes_to_the_judge() -> None:
    pre_cached, uncached, stats = _partition([_item("a", "absent")], {})
    assert pre_cached == []
    assert stats["uncached"] == 1


def test_override_cache_sends_everything_to_the_judge() -> None:
    items = [_item("a", "absent")]
    hits = {"a": {"verdict": {"overall": "full", "duplicate_class": "probed-conclusive"}}}
    pre_cached, uncached, stats = _partition(items, hits, override_cache=True)
    assert pre_cached == []
    assert len(uncached) == 1


def test_both_entry_points_use_the_shared_partitioner() -> None:
    """A re-judge rule must not be able to exist in only one of the two paths."""
    from app.pipeline import verify_job
    from app.routers import wikidata_studio

    job_src = inspect.getsource(verify_job)
    route_src = inspect.getsource(wikidata_studio.start_verify_stream)
    assert "partition_wikidata_verify_cache" in job_src
    assert "partition_wikidata_verify_cache" in route_src
    # The hand-rolled loop must be gone from both, or it will drift again.
    assert 'kind="ai_verdict"' not in route_src
