"""Progress contract for the HMO item build orchestrator (3 pipeline steps)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.pipeline import hmo_item_build_exec as exec_module


@pytest.mark.asyncio
async def test_execute_hmo_item_build_reports_one_based_step_progress(
    sample_run, db_session, monkeypatch, tmp_path,
) -> None:
    """Authority must report 1/3 while running — never 0/3 for an active step."""
    run_id = sample_run["run_id"]
    ticks: list[tuple[str, int, int, str]] = []

    async def capture(phase: str, processed: int, total: int, message: str) -> None:
        ticks.append((phase, processed, total, message))

    ttl = tmp_path / "manuscripts.ttl"
    ttl.write_text("@prefix : <http://example.org#> .", encoding="utf-8")

    monkeypatch.setattr(exec_module, "rdf_output_path_for_run", lambda _rid: str(ttl))

    async def fake_re_enrich(*_a, **_k):
        return None

    class _FakeMatcher:
        pass

    monkeypatch.setattr(
        "app.pipeline.authority.get_default_matcher",
        lambda: _FakeMatcher(),
    )
    monkeypatch.setattr(
        "app.pipeline.authority_re_enrich.re_enrich_run",
        fake_re_enrich,
    )

    async def fake_build_rdf_graph(**_k):
        class _R:
            triples_count = 1
            manuscripts_count = 1
        return _R()

    monkeypatch.setattr(exec_module, "build_rdf_graph", fake_build_rdf_graph)
    monkeypatch.setattr(exec_module, "upsert_rdf_artifact", AsyncMock())

    class _BuildResult:
        from_cache = False
        entity_count = 12
        deferred_link_count = 0
        skipped_statement_count = 0

    async def fake_build_items(*_a, **_k):
        return _BuildResult()

    monkeypatch.setattr(exec_module.hmo_item_build, "build_items_for_run", fake_build_items)

    # Need at least one RunRecord for the RDF rebuild branch.
    from app.models.run import RunRecord

    db_session.add(RunRecord(
        run_id=run_id,
        control_number="990001800310205171",
        marc={"_control_number": "990001800310205171", "title": "t"},
    ))
    await db_session.commit()

    result = await exec_module.execute_hmo_item_build(
        db_session,
        run_id,
        force_rebuild=True,
        refresh_authority=True,
        on_progress=capture,
    )
    assert result.entity_count == 12
    assert result.refreshed_authority is True
    assert result.rebuilt_rdf is True

    by_phase = {phase: (processed, total, message) for phase, processed, total, message in ticks}
    assert by_phase["authority"][0] == 1
    assert by_phase["authority"][1] == 3
    assert "Step 1 of 3" in by_phase["authority"][2]
    assert by_phase["rdf"] == (2, 3, by_phase["rdf"][2])
    assert "Step 2 of 3" in by_phase["rdf"][2]
    assert by_phase["export"][0] == 3
    assert by_phase["export"][1] == 3
    assert "Step 3 of 3" in by_phase["export"][2]
    # Never report an in-progress pipeline step as 0/N.
    assert all(p > 0 for phase, p, _t, _m in ticks if phase != "done")
