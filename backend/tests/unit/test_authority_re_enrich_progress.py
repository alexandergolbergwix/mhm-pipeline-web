"""Authority re-enrich emits throttled per-entity progress (Rule W-113)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.pipeline.authority_re_enrich import re_enrich_run


class _Matcher:
    async def match(self, entity, marc, **_kwargs):
        return []


@pytest.mark.asyncio
async def test_re_enrich_run_reports_entity_progress(db_session, sample_run) -> None:
    run_id = sample_run["run_id"]
    from app.models.run import Run, RunRecord

    run = await db_session.get(Run, run_id)
    assert run is not None

    marc = {
        "_control_number": "990001800310205171",
        "title": "Test MS",
        "authors": [{"name": "Author One", "role": "author"}],
        "contributors": [{"name": "Contributor Two", "role": "scribe"}],
    }
    db_session.add(RunRecord(
        run_id=run_id,
        control_number="990001800310205171",
        marc=marc,
    ))
    await db_session.commit()

    records = [SimpleNamespace(control_number="990001800310205171", marc=marc)]
    ticks: list[tuple[int, int, str]] = []

    async def on_progress(processed: int, total: int, message: str) -> None:
        ticks.append((processed, total, message))

    result = await re_enrich_run(
        db_session,
        run,
        _Matcher(),
        skip_cache=True,
        records=records,  # type: ignore[arg-type]
        existing_rows=[],
        on_progress=on_progress,
    )

    assert result["checked"] >= 1
    assert ticks, "expected at least first/last progress ticks"
    assert ticks[0][0] == 1
    assert ticks[0][1] == result["checked"]
    assert ticks[-1][0] == ticks[-1][1]
    assert "990001800310205171" in ticks[0][2]


@pytest.mark.asyncio
async def test_re_enrich_run_cancels_on_emit(db_session, sample_run) -> None:
    """A set cancel flag must stop enrichment on the next progress emit —
    the runner otherwise only checks at phase boundaries and Cancel stays
    unresponsive for the whole (multi-minute) sweep/gather (2026-09-17)."""
    from app.pipeline.authority_re_enrich import ReEnrichCancelled

    run_id = sample_run["run_id"]
    from app.models.run import Run, RunRecord

    run = await db_session.get(Run, run_id)
    assert run is not None

    marc = {
        "_control_number": "990001800310205171",
        "title": "Test MS",
        "authors": [{"name": "Author One", "role": "author"}],
        "contributors": [{"name": "Contributor Two", "role": "scribe"}],
    }
    db_session.add(RunRecord(
        run_id=run_id,
        control_number="990001800310205171",
        marc=marc,
    ))
    await db_session.commit()

    async def cancelled() -> bool:
        return True

    with pytest.raises(ReEnrichCancelled):
        await re_enrich_run(
            db_session,
            run,
            _Matcher(),
            skip_cache=True,
            records=[SimpleNamespace(control_number="990001800310205171", marc=marc)],  # type: ignore[arg-type]
            existing_rows=[],
            on_progress=None,
            should_cancel=cancelled,
        )
