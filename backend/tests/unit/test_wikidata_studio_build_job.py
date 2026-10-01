"""Wikidata Studio background build job contract."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.run_job import (
    JOB_KIND_WIKIDATA_STUDIO_BUILD,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.run_job_service import JobCancelledError
from app.pipeline.wikidata_studio_build_job import (
    BUILD_PHASES,
    _MEASURED_PROCESS_SECONDS,
    _build_progress,
    _phase_plan,
    run_wikidata_studio_build_job,
)


@pytest.mark.asyncio
async def test_build_job_finalizes_cancelled_when_the_build_is_cancelled(
    db_session,
) -> None:
    """Rule R28: a Cancel during the build must finalize the row as
    cancelled at the record/phase boundary where the flag was seen — not
    after the whole build crawled to its end (2026-09-17 incident)."""
    run_id, job_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        RunJob(
            id=job_id,
            project_id=uuid.uuid4(),
            run_id=run_id,
            kind=JOB_KIND_WIKIDATA_STUDIO_BUILD,
            status="running",
            params={"approved_only": True, "source": "canonical"},
            progress={},
            created_by=uuid.uuid4(),
        )
    )
    await db_session.commit()

    with (
        patch(
            "app.routers.wikidata_studio.execute_studio_build",
            new=AsyncMock(side_effect=JobCancelledError("cancelled")),
        ),
        patch(
            "app.pipeline.wikidata_studio_build_job.is_cancel_requested",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.pipeline.wikidata_studio_build_job.finish_job",
            new=AsyncMock(),
        ) as finish,
        patch(
            "app.pipeline.wikidata_studio_build_job.update_job_progress",
            new=AsyncMock(),
        ),
    ):
        await run_wikidata_studio_build_job(job_id)

    finish.assert_awaited_once()
    kwargs = finish.await_args.kwargs
    assert kwargs["status"] == JOB_STATUS_CANCELLED
    assert kwargs["error"] == "Cancelled by user"
    assert kwargs["progress"]["phase"] == "cancelled"


@pytest.mark.asyncio
async def test_build_job_finalizes_cancelled_when_cancel_lands_during_mining(
    db_session,
) -> None:
    """A Cancel that lands after the build check must not be overridden by
    the mining tail — the job finalizes cancelled, never succeeded."""
    run_id, job_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        RunJob(
            id=job_id,
            project_id=uuid.uuid4(),
            run_id=run_id,
            kind=JOB_KIND_WIKIDATA_STUDIO_BUILD,
            status="running",
            params={"approved_only": True, "source": "canonical"},
            progress={},
            created_by=uuid.uuid4(),
        )
    )
    await db_session.commit()

    cached = SimpleNamespace(
        result_items=[{"local_id": "ms1"}],
        summary={},
        record_count=1,
        approved_match_count=0,
    )

    with (
        patch(
            "app.routers.wikidata_studio.execute_studio_build",
            new=AsyncMock(return_value=cached),
        ),
        patch(
            "app.pipeline.wikidata_studio_build_job.is_cancel_requested",
            new=AsyncMock(side_effect=[False, False, True]),
        ),
        patch(
            "app.pipeline.wikidata_studio_build_job._mine_provenance_prose",
            new=AsyncMock(),
        ),
        patch(
            "app.pipeline.wikidata_studio_build_job.finish_job",
            new=AsyncMock(),
        ) as finish,
        patch(
            "app.pipeline.wikidata_studio_build_job.update_job_progress",
            new=AsyncMock(),
        ),
    ):
        await run_wikidata_studio_build_job(job_id)

    finish.assert_awaited_once()
    assert finish.await_args.kwargs["status"] == JOB_STATUS_CANCELLED


@pytest.mark.asyncio
async def test_execute_studio_build_raises_when_cancelled_before_start() -> None:
    """The top-of-function check must abort the build before any work."""
    from unittest.mock import MagicMock

    from app.routers.wikidata_studio import execute_studio_build

    async def cancelled() -> None:
        raise JobCancelledError("cancel requested")

    with pytest.raises(JobCancelledError):
        await execute_studio_build(
            MagicMock(),
            run_id=uuid.uuid4(),
            approved_only=True,
            source="legacy",
            force_rebuild=True,
            run_user_id=None,
            should_cancel=cancelled,
        )


@pytest.mark.asyncio
async def test_build_job_skips_wdqs_reconcile(db_session) -> None:
    run_id = uuid.uuid4()
    job_id = uuid.uuid4()
    db_session.add(
        RunJob(
            id=job_id,
            project_id=uuid.uuid4(),
            run_id=run_id,
            kind=JOB_KIND_WIKIDATA_STUDIO_BUILD,
            status="running",
            params={
                "approved_only": True,
                "force_rebuild": True,
                "source": "canonical",
            },
            progress={},
            created_by=uuid.uuid4(),
        )
    )
    await db_session.commit()

    cached = SimpleNamespace(
        result_items=[{"local_id": "ms1", "entity_type": "manuscript"}],
        summary={"total_items": 1},
        record_count=1,
        approved_match_count=0,
    )

    with (
        patch(
            "app.routers.wikidata_studio.execute_studio_build",
            new=AsyncMock(return_value=cached),
        ) as build,
        patch(
            "app.pipeline.wikidata_studio_build_job.is_cancel_requested",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.pipeline.wikidata_studio_build_job.finish_job",
            new=AsyncMock(),
        ) as finish,
        patch(
            "app.pipeline.wikidata_studio_build_job.update_job_progress",
            new=AsyncMock(),
        ),
    ):
        await run_wikidata_studio_build_job(job_id)

    build.assert_awaited_once()
    assert build.await_args.kwargs["reconcile"] is False
    assert build.await_args.kwargs["source"] == "canonical"
    finish.assert_awaited_once()
    assert finish.await_args.kwargs["status"] == JOB_STATUS_SUCCEEDED


@pytest.mark.asyncio
async def test_build_job_reports_phase_steps_and_nested_records(db_session, monkeypatch) -> None:
    """Rules W-112 / W-113 — 1-based phases outside, record loop nested inside.

    Reporting only the item loop left the bar on 0/1 for every slow stage that
    runs before it (canonical read-back, transliteration prewarm).
    """
    run_id, job_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        RunJob(
            id=job_id,
            project_id=uuid.uuid4(),
            run_id=run_id,
            kind=JOB_KIND_WIKIDATA_STUDIO_BUILD,
            status="running",
            params={"approved_only": True, "source": "canonical"},
            progress={},
            created_by=uuid.uuid4(),
        )
    )
    await db_session.commit()

    cached = SimpleNamespace(
        result_items=[{"local_id": "ms1"}, {"local_id": "ms2"}],
        summary={},
        record_count=3,
        approved_match_count=0,
    )

    async def fake_build(_db, **kwargs):
        phase, record = kwargs["phase_cb"], kwargs["progress_cb"]
        phase("loading records")
        await asyncio.sleep(0)
        phase("loading canonical entities")
        await asyncio.sleep(0)
        phase("building items")
        for done in (1, 2, 3):
            record(done, 3)
            await asyncio.sleep(0)
        phase("assembling canonical projection")
        await asyncio.sleep(0)
        return cached

    monkeypatch.setattr(
        "app.pipeline.wikidata_studio_build_job.PROGRESS_INTERVAL_SECONDS", 0,
    )

    with (
        patch("app.routers.wikidata_studio.execute_studio_build", new=fake_build),
        patch(
            "app.pipeline.wikidata_studio_build_job.is_cancel_requested",
            new=AsyncMock(return_value=False),
        ),
        patch("app.pipeline.wikidata_studio_build_job.finish_job", new=AsyncMock()) as finish,
        patch(
            "app.pipeline.wikidata_studio_build_job.update_job_progress",
            new=AsyncMock(),
        ) as progress,
    ):
        await run_wikidata_studio_build_job(job_id)

    published = [c.args[1] for c in progress.await_args_list]
    assert published, "no progress published"

    # The very first write must already be a real step, never 0/1.
    assert published[0]["processed"] >= 1
    assert published[0]["total"] == len(BUILD_PHASES)
    assert published[0]["unit"] == "steps"
    assert not any(p["processed"] == 0 for p in published)

    seen_phases = [p["phase"] for p in published]
    assert "loading canonical entities" in seen_phases
    assert "building items" in seen_phases

    building = [p for p in published if p["phase"] == "building items"]
    assert building, f"item loop never reported: {seen_phases}"
    assert building[-1]["sub_total"] == 3
    assert building[-1]["sub_unit"] == "records"
    assert "3 of 3" in building[-1]["sub_message"]
    build_step = BUILD_PHASES.index("building items") + 1
    assert building[-1]["message"] == f"Step {build_step} of {len(BUILD_PHASES)}: building items"

    done_progress = finish.await_args.kwargs["progress"]
    assert done_progress["processed"] == 2
    assert done_progress["message"] == "Built 2 items from 3 records"


def test_legacy_source_omits_the_canonical_phases() -> None:
    """A legacy build must not show steps it will never reach."""
    assert _phase_plan("canonical") == BUILD_PHASES
    legacy = _phase_plan("legacy")
    assert "loading canonical entities" not in legacy
    assert "fingerprinting canonical entities" not in legacy
    assert "assembling canonical projection" not in legacy
    assert legacy[0] == "loading records"


def test_assembly_progress_names_the_pass_and_counts_items() -> None:
    progress = _build_progress(
        {
            "phase": "assembling canonical projection",
            "done": 1200,
            "records": 18524,
            "unit": "items",
            "detail": "merging records",
        },
        BUILD_PHASES,
    )
    running = next(step for step in progress["steps"] if step["status"] == "running")
    assert running["processed"] == 1200
    assert running["total"] == 18524
    assert running["unit"] == "items"
    assert running["current_label"] == "merging records"
    assert "Merge records" in str(running["description"])
    assert progress["sub_message"] == "merging records: 1200 of 18524"
    assert progress["sub_unit"] == "items"


def test_step_countdown_locks_then_can_run_over() -> None:
    state: dict[str, object] = {
        "phase": "assembling canonical projection",
        "done": 10,
        "records": 100,
        "detail": "native items",
        "now": 100.0,
        "job_started": 0.0,
    }
    first = _build_progress(state, BUILD_PHASES)
    assert first["eta_seconds"] is None
    state["done"] = 20
    state["now"] = 110.0
    second = _build_progress(state, BUILD_PHASES)
    assert second["eta_seconds"] == 80
    assert "step 1 min left" in str(second["message"])
    state["now"] = 200.0
    third = _build_progress(state, BUILD_PHASES)
    assert third["eta_seconds"] == -10
    assert "step 10s over" in str(third["message"])


def test_whole_build_countdown_uses_the_measured_run() -> None:
    state: dict[str, object] = {
        "phase": "loading canonical entities",
        "done": 100,
        "records": 18524,
        "now": 100.0,
        "job_started": 0.0,
    }
    progress = _build_progress(state, BUILD_PHASES)
    assert progress["process_eta_seconds"] == _MEASURED_PROCESS_SECONDS - 100
    assert "all" in str(progress["message"])
    state["now"] = _MEASURED_PROCESS_SECONDS + 90
    late = _build_progress(state, BUILD_PHASES)
    assert late["process_eta_seconds"] == -90
    assert "over" in str(late["message"])


def test_progress_falls_back_to_the_first_step_for_an_unknown_phase() -> None:
    progress = _build_progress({"phase": "who knows"}, BUILD_PHASES)
    assert progress["processed"] == 1
    assert progress["total"] == len(BUILD_PHASES)
    assert "sub_total" not in progress


def test_record_progress_counts_finished_records() -> None:
    """The tray must show the last finished batch, not one past it.

    A count of 10000 was displayed as 10001, so a paused read looked one
    record ahead of the cursor the retry would resume.
    """
    progress = _build_progress(
        {"phase": "loading canonical entities", "done": 10000, "records": 18524},
        BUILD_PHASES,
    )
    assert progress["sub_processed"] == 10000
    assert progress["sub_message"] == "record 10000 of 18524"
    running = next(step for step in progress["steps"] if step["status"] == "running")
    assert running["processed"] == 10000
    assert running["label"] == "loading canonical entities"


def test_retry_progress_names_the_attempt_and_keeps_the_phase() -> None:
    progress = _build_progress(
        {
            "phase": "loading canonical entities",
            "done": 10000,
            "records": 18524,
            "retry": 2,
            "retry_max": 3,
        },
        BUILD_PHASES,
    )
    assert progress["phase"] == "loading canonical entities"
    assert "retry 2 of 3" in str(progress["message"])
    assert "retry 2 of 3" in str(progress["sub_message"])


def _running_build_job(db_session, job_id: uuid.UUID, run_id: uuid.UUID) -> None:
    db_session.add(
        RunJob(
            id=job_id,
            project_id=uuid.uuid4(),
            run_id=run_id,
            kind=JOB_KIND_WIKIDATA_STUDIO_BUILD,
            status="running",
            params={"approved_only": True, "source": "canonical"},
            progress={},
            created_by=uuid.uuid4(),
        )
    )


@pytest.mark.asyncio
async def test_build_job_resumes_the_same_cursor_after_a_dropped_connection(
    db_session, monkeypatch,
) -> None:
    """A dropped connection retries, and the next attempt sees the saved cursor."""
    run_id, job_id = uuid.uuid4(), uuid.uuid4()
    _running_build_job(db_session, job_id, run_id)
    await db_session.commit()
    monkeypatch.setattr(
        "app.pipeline.wikidata_studio_build_job.BUILD_RETRY_SLEEP_SECONDS", 0,
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_studio_build_job.PROGRESS_INTERVAL_SECONDS", 0,
    )
    cached = SimpleNamespace(
        result_items=[{"local_id": "ms1"}],
        summary={},
        record_count=1,
        approved_match_count=0,
    )
    seen: list[dict[str, object]] = []

    async def fake_build(_db, **kwargs):
        resume = kwargs["resume"]
        phase, record = kwargs["phase_cb"], kwargs["progress_cb"]
        seen.append(resume)
        if len(seen) == 1:
            phase("loading canonical entities")
            record(10000, 18524)
            resume["entities"] = ["kept"]
            resume["last_id"] = uuid.UUID(int=10000)
            resume["entity_total"] = 18524
            await asyncio.sleep(0)
            raise ConnectionError("connection was closed in the middle of operation")
        assert resume.get("entities") == ["kept"]
        assert resume.get("last_id") == uuid.UUID(int=10000)
        phase("loading records")
        await asyncio.sleep(0)
        phase("building items")
        await asyncio.sleep(0)
        return cached

    with (
        patch("app.routers.wikidata_studio.execute_studio_build", new=fake_build),
        patch(
            "app.pipeline.wikidata_studio_build_job.is_cancel_requested",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.pipeline.wikidata_studio_build_job._mine_provenance_prose",
            new=AsyncMock(),
        ),
        patch(
            "app.pipeline.wikidata_studio_build_job.finish_job",
            new=AsyncMock(),
        ) as finish,
        patch(
            "app.pipeline.wikidata_studio_build_job.update_job_progress",
            new=AsyncMock(),
        ) as progress,
    ):
        await run_wikidata_studio_build_job(job_id)

    assert len(seen) == 2
    assert seen[0] is seen[1]
    assert finish.await_args.kwargs["status"] == JOB_STATUS_SUCCEEDED
    published = [call.args[1]["phase"] for call in progress.await_args_list]
    assert "loading canonical entities" in published
    assert "building items" in published
    canonical_at = published.index("loading canonical entities")
    assert "loading records" not in published[canonical_at:]


@pytest.mark.asyncio
async def test_build_job_stops_after_three_retries(db_session, monkeypatch) -> None:
    run_id, job_id = uuid.uuid4(), uuid.uuid4()
    _running_build_job(db_session, job_id, run_id)
    await db_session.commit()
    monkeypatch.setattr(
        "app.pipeline.wikidata_studio_build_job.BUILD_RETRY_SLEEP_SECONDS", 0,
    )
    attempts = 0

    async def fake_build(_db, **kwargs):
        nonlocal attempts
        attempts += 1
        raise ConnectionError("connection was closed in the middle of operation")

    with (
        patch("app.routers.wikidata_studio.execute_studio_build", new=fake_build),
        patch(
            "app.pipeline.wikidata_studio_build_job.is_cancel_requested",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.pipeline.wikidata_studio_build_job.finish_job",
            new=AsyncMock(),
        ) as finish,
        patch(
            "app.pipeline.wikidata_studio_build_job.update_job_progress",
            new=AsyncMock(),
        ),
    ):
        await run_wikidata_studio_build_job(job_id)

    assert attempts == 4
    assert finish.await_args.kwargs["status"] == JOB_STATUS_FAILED
    assert "stopped after 3 retries" in finish.await_args.kwargs["error"]


@pytest.mark.asyncio
async def test_build_job_does_not_retry_a_missing_canonical_corpus(db_session) -> None:
    run_id, job_id = uuid.uuid4(), uuid.uuid4()
    _running_build_job(db_session, job_id, run_id)
    await db_session.commit()
    attempts = 0

    async def fake_build(_db, **kwargs):
        nonlocal attempts
        attempts += 1
        raise ValueError("no durable HMO canonical entities for run")

    with (
        patch("app.routers.wikidata_studio.execute_studio_build", new=fake_build),
        patch(
            "app.pipeline.wikidata_studio_build_job.is_cancel_requested",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.pipeline.wikidata_studio_build_job.finish_job",
            new=AsyncMock(),
        ) as finish,
        patch(
            "app.pipeline.wikidata_studio_build_job.update_job_progress",
            new=AsyncMock(),
        ),
    ):
        await run_wikidata_studio_build_job(job_id)

    assert attempts == 1
    assert finish.await_args.kwargs["status"] == JOB_STATUS_FAILED


class TestMiningReadsMarcProse:
    """Rule W-140: built items carry no MARC, so mining must load it itself.

    Without this the extractor found zero prose on every manuscript, the phase
    was a sub-second no-op, and every export reported `llm_proposals: not_run`.
    """

    @pytest.mark.asyncio
    async def test_prose_context_is_stamped_from_the_run_records(self, monkeypatch) -> None:
        from app.pipeline import wikidata_studio_build_job as job

        item = {
            "entity_type": "manuscript",
            "record_ids": ["990000592310205171"],
            "source_uri": "https://w3id.org/mhm/ontology#MS_990000592310205171",
            "local_id": "ms:990000592310205171",
        }
        records = [{
            "_control_number": "990000592310205171",
            "561$a": "מאוסף הספרייה הלאומית",
        }]

        async def fake_scoped(_db, _run_id, wanted):
            assert "990000592310205171" in wanted
            return records

        monkeypatch.setattr(
            "app.pipeline.marc_verify_context.load_run_marc_records_scoped", fake_scoped,
        )
        await job._attach_prose_context(
            uuid.uuid4(), [item], ["provenance", "notes", "colophon_text"],
        )

        from app.pipeline.marc_llm_extract import source_text

        assert item["_primary_control_number"] == "990000592310205171"
        assert "מאוסף הספרייה הלאומית" in source_text(item["_marc_context"])

    @pytest.mark.asyncio
    async def test_non_manuscripts_are_never_loaded(self, monkeypatch) -> None:
        from app.pipeline import wikidata_studio_build_job as job

        called = False

        async def fake_scoped(_db, _run_id, _wanted):
            nonlocal called
            called = True
            return []

        monkeypatch.setattr(
            "app.pipeline.marc_verify_context.load_run_marc_records_scoped", fake_scoped,
        )
        items = [{"entity_type": "work", "record_ids": ["990000592310205171"]}]
        await job._attach_prose_context(uuid.uuid4(), items, ["provenance"])
        assert called is False
        assert "_marc_context" not in items[0]


def _snapshot(number: int) -> dict[str, object]:
    return {
        "local_id": f"Q{number}",
        "source_uri": f"https://example.org/{number}",
        "wikibase_id": f"Q{number}",
        "entity_type": "manuscript",
        "labels": {},
        "descriptions": {},
        "aliases": {},
        "claims": [],
        "authority_evidence": [],
    }


def _keyset_cursor(stmt: object, run_id: uuid.UUID) -> uuid.UUID:
    from sqlalchemy.sql import visitors
    from sqlalchemy.sql.elements import BindParameter

    found: list[uuid.UUID] = []

    def visit_bind(bind: BindParameter) -> None:
        if isinstance(bind.value, uuid.UUID):
            found.append(bind.value)

    visitors.traverse(stmt, {}, {"bindparam": visit_bind})
    cursors = [value for value in found if value != run_id]
    assert len(cursors) == 1
    return cursors[0]


@pytest.mark.asyncio
async def test_canonical_entity_load_continues_after_the_last_saved_id(monkeypatch) -> None:
    """A failed batch keeps the earlier entities. The next call reads the rest."""
    from contextlib import asynccontextmanager

    from app.routers import wikidata_studio as router

    run_id = uuid.uuid4()
    rows = [
        (_snapshot(number), uuid.UUID(int=number))
        for number in (1, 2, 3)
    ]
    executes = {"n": 0}

    class _Page:
        def __init__(self, page: list[tuple[dict[str, object], uuid.UUID]]) -> None:
            self._page = page

        def all(self) -> list[tuple[dict[str, object], uuid.UUID]]:
            return self._page

    class _BatchDB:
        async def scalar(self, _stmt: object) -> int:
            return len(rows)

        async def execute(self, stmt: object) -> _Page:
            executes["n"] += 1
            if executes["n"] in (2, 3, 4):
                raise ConnectionError("connection was closed in the middle of operation")
            cursor = _keyset_cursor(stmt, run_id)
            page = [row for row in rows if row[1] > cursor][:1]
            return _Page(page)

    @asynccontextmanager
    async def _scope():
        yield _BatchDB()

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(router, "_CANONICAL_LOAD_BATCH", 1)
    monkeypatch.setattr("app.db.session_scope", _scope)
    monkeypatch.setattr(router.asyncio, "sleep", _no_sleep)

    resume: dict[str, object] = {}
    with pytest.raises(ConnectionError):
        await router._canonical_entities_for_run(
            AsyncMock(), run_id, resume=resume,
        )

    assert [entity.local_id for entity in resume["entities"]] == ["Q1"]
    assert resume["last_id"] == uuid.UUID(int=1)
    assert resume.get("entities_complete") is not True

    loaded = await router._canonical_entities_for_run(
        AsyncMock(), run_id, resume=resume,
    )
    assert [entity.local_id for entity in loaded] == ["Q1", "Q2", "Q3"]
    assert resume["entities_complete"] is True


def test_statement_cap_is_cleared_on_one_connection_only() -> None:
    from collections import namedtuple

    from app.routers.wikidata_studio import _without_statement_cap

    Config = namedtuple("Config", ["command_timeout", "other"])

    class _Driver:
        def __init__(self) -> None:
            self._config = Config(300.0, "keep")

    driver = _Driver()
    _without_statement_cap(driver)
    assert driver._config.command_timeout is None
    assert driver._config.other == "keep"

    untouched = object()
    _without_statement_cap(untouched)
