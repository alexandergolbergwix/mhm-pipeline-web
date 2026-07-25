"""Progress contract for the HMO item build orchestrator (Rules W-112 / W-113)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.pipeline import hmo_item_build_exec as exec_module


async def _stub_build_pipeline(monkeypatch, tmp_path, *, re_enrich=None, build_rdf=None):
    ttl = tmp_path / "manuscripts.ttl"
    ttl.write_text("@prefix : <http://example.org#> .", encoding="utf-8")
    monkeypatch.setattr(exec_module, "rdf_output_path_for_run", lambda _rid: str(ttl))

    async def default_re_enrich(*_a, **_k):
        return None

    class _FakeMatcher:
        pass

    monkeypatch.setattr(
        "app.pipeline.authority.get_default_matcher",
        lambda: _FakeMatcher(),
    )
    monkeypatch.setattr(
        "app.pipeline.authority_re_enrich.re_enrich_run",
        re_enrich or default_re_enrich,
    )

    async def default_build_rdf(**_k):
        class _R:
            triples_count = 1
            manuscripts_count = 1
        return _R()

    monkeypatch.setattr(exec_module, "build_rdf_graph", build_rdf or default_build_rdf)
    monkeypatch.setattr(exec_module, "upsert_rdf_artifact", AsyncMock())

    class _BuildResult:
        from_cache = False
        entity_count = 12
        deferred_link_count = 0
        skipped_statement_count = 0

    async def fake_build_items(*_a, **_k):
        return _BuildResult()

    monkeypatch.setattr(exec_module.hmo_item_build, "build_items_for_run", fake_build_items)


@pytest.mark.asyncio
async def test_execute_hmo_item_build_reports_one_based_step_progress(
    sample_run, db_session, monkeypatch, tmp_path,
) -> None:
    """Authority must report 1/3 while running — never 0/3 for an active step."""
    run_id = sample_run["run_id"]
    ticks: list[tuple[str, int, int, str]] = []

    async def capture(phase: str, processed: int, total: int, message: str, **_kw) -> None:
        ticks.append((phase, processed, total, message))

    await _stub_build_pipeline(monkeypatch, tmp_path)

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
    assert all(p > 0 for phase, p, _t, _m in ticks if phase != "done")


@pytest.mark.asyncio
async def test_execute_hmo_item_build_forwards_authority_sub_progress(
    sample_run, db_session, monkeypatch, tmp_path,
) -> None:
    run_id = sample_run["run_id"]
    ticks: list[dict] = []

    async def capture(
        phase: str,
        processed: int,
        total: int,
        message: str,
        *,
        sub_processed: int | None = None,
        sub_total: int | None = None,
        sub_unit: str | None = None,
        sub_message: str | None = None,
    ) -> None:
        ticks.append({
            "phase": phase,
            "processed": processed,
            "total": total,
            "message": message,
            "sub_processed": sub_processed,
            "sub_total": sub_total,
            "sub_unit": sub_unit,
            "sub_message": sub_message,
        })

    async def fake_re_enrich(*_a, **kwargs):
        on_progress = kwargs.get("on_progress")
        if on_progress:
            await on_progress(12, 100, "990001800310205171: Example person")
        return None

    async def fake_build_rdf_graph(**kwargs):
        on_progress = kwargs.get("on_progress")
        if on_progress:
            await on_progress({
                "processed": 3,
                "total": 10,
                "message": "Mapping records…",
                "current_control_number": "990001800310205171",
            })

        class _R:
            triples_count = 1
            manuscripts_count = 1
        return _R()

    await _stub_build_pipeline(
        monkeypatch,
        tmp_path,
        re_enrich=fake_re_enrich,
        build_rdf=fake_build_rdf_graph,
    )

    from app.models.run import RunRecord

    db_session.add(RunRecord(
        run_id=run_id,
        control_number="990001800310205171",
        marc={"_control_number": "990001800310205171", "title": "t"},
    ))
    await db_session.commit()

    await exec_module.execute_hmo_item_build(
        db_session,
        run_id,
        force_rebuild=True,
        refresh_authority=True,
        on_progress=capture,
    )

    auth_subs = [
        t for t in ticks
        if t["phase"] == "authority" and t["sub_total"] is not None
    ]
    assert auth_subs, "expected nested authority sub-progress"
    assert auth_subs[0]["processed"] == 1
    assert auth_subs[0]["total"] == 3
    assert auth_subs[0]["sub_processed"] == 12
    assert auth_subs[0]["sub_total"] == 100
    assert auth_subs[0]["sub_unit"] == "entities"
    assert "Example person" in (auth_subs[0]["sub_message"] or "")

    rdf_subs = [
        t for t in ticks
        if t["phase"] == "rdf" and t["sub_total"] is not None
    ]
    assert rdf_subs, "expected nested RDF sub-progress"
    assert rdf_subs[0]["processed"] == 2
    assert rdf_subs[0]["sub_processed"] == 3
    assert rdf_subs[0]["sub_total"] == 10
    assert rdf_subs[0]["sub_unit"] == "records"
