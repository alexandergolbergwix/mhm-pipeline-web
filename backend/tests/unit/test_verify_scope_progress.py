"""Rules W-112 / W-113 — scope preparation must report steps, not a static string."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

from app.pipeline.verify_job import _publish_scope_progress, _scope_progress
from app.routers.wikidata_studio import VERIFY_SCOPE_PHASES


class TestScopeProgressShape:
    def test_the_first_step_is_1_based_with_a_named_phase(self) -> None:
        state = {"phase": VERIFY_SCOPE_PHASES[0], "done": 0, "total": 0}
        progress = _scope_progress(state, "sess-1")
        assert progress["step"] == 1
        assert progress["step_total"] == len(VERIFY_SCOPE_PHASES)
        assert progress["message"] == f"Step 1 of 4: {VERIFY_SCOPE_PHASES[0]}"

    def test_the_duplicate_step_reports_nested_lookup_counts(self) -> None:
        state = {"phase": VERIFY_SCOPE_PHASES[2], "done": 12, "total": 40}
        progress = _scope_progress(state, "sess-1")
        assert progress["step"] == 3
        assert progress["sub_processed"] == 12
        assert progress["sub_total"] == 40
        assert progress["sub_unit"] == "lookups"
        assert progress["sub_message"] == "12 of 40 lookups"

    def test_no_nested_counts_when_the_step_has_none(self) -> None:
        state = {"phase": VERIFY_SCOPE_PHASES[1], "done": 0, "total": 0}
        assert "sub_total" not in _scope_progress(state, "sess-1")

    def test_an_unknown_phase_falls_back_to_step_one(self) -> None:
        assert _scope_progress({"phase": "who knows"}, "s")["step"] == 1

    def test_an_empty_phase_keeps_the_original_message(self) -> None:
        # Published once before any phase callback fires.
        assert _scope_progress({"phase": ""}, "s")["message"] == "Loading Studio scope…"

    def test_a_sub_count_never_exceeds_its_total(self) -> None:
        state = {"phase": VERIFY_SCOPE_PHASES[2], "done": 99, "total": 40}
        assert _scope_progress(state, "s")["sub_processed"] == 40


class TestScopeProgressPublisher:
    def test_it_writes_only_when_the_state_changed(self) -> None:
        state = {"phase": VERIFY_SCOPE_PHASES[0], "done": 0, "total": 0}
        writes = AsyncMock()

        async def drive() -> None:
            with patch("app.pipeline.verify_job.update_job_progress", writes), \
                 patch("app.pipeline.verify_job.SCOPE_PROGRESS_INTERVAL_SECONDS", 0.01):
                task = asyncio.create_task(
                    _publish_scope_progress(uuid.uuid4(), state, "sess-1"),
                )
                await asyncio.sleep(0.05)      # unchanged → one write at most
                first = writes.await_count
                state["phase"] = VERIFY_SCOPE_PHASES[2]
                state["done"], state["total"] = 5, 40
                await asyncio.sleep(0.05)
                second = writes.await_count
                task.cancel()
                assert first <= 1
                assert second > first

        asyncio.run(drive())
