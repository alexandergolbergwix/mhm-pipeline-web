"""Idempotence short-circuit for the HMO item build (Rule W-239).

"Build items" on an unchanged run must return the cache instantly —
never re-run the 5k-entity authority pass, RDF rebuild, or export.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.run import AuthorityMatch, RunRecord
from app.pipeline import hmo_item_build_exec as exec_module
from tests.unit.test_hmo_item_build_progress import _stub_build_pipeline


async def _seed_run(db_session, sample_run) -> None:
    db_session.add(RunRecord(
        run_id=sample_run["run_id"],
        control_number="990001800310205171",
        marc={"_control_number": "990001800310205171", "title": "t"},
    ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_unchanged_run_returns_cache_without_any_pipeline_step(
    sample_run, db_session, monkeypatch, tmp_path,
) -> None:
    await _stub_build_pipeline(monkeypatch, tmp_path)
    await _seed_run(db_session, sample_run)

    built_at = datetime.now(timezone.utc)
    db_session.add(HmoStudioItemCache(
        run_id=sample_run["run_id"],
        input_fingerprint="a" * 64,
        resolved_entities=[{"local_id": "ms_1"}],
        entity_count=1,
        deferred_link_count=0,
        skipped_statement_count=0,
        shacl_report={},
        built_at=built_at,
    ))
    # An authority match that predates the cached build.
    db_session.add(AuthorityMatch(
        run_id=sample_run["run_id"],
        control_number="990001800310205171",
        entity_text="Moses",
        entity_kind="person",
        created_at=built_at - timedelta(days=1),
    ))
    await db_session.commit()

    # Any pipeline step running would blow up — nothing is stubbed to work.
    result = await exec_module.execute_hmo_item_build(
        db_session, sample_run["run_id"],
        force_rebuild=False, refresh_authority=True,
        on_progress=None,
    )
    assert result.from_cache is True
    assert result.entity_count == 1
    assert result.refreshed_authority is False


@pytest.mark.asyncio
async def test_approval_after_cache_bypasses_short_circuit(
    sample_run, db_session, monkeypatch, tmp_path,
) -> None:
    await _stub_build_pipeline(monkeypatch, tmp_path)
    await _seed_run(db_session, sample_run)

    built_at = datetime.now(timezone.utc)
    db_session.add(HmoStudioItemCache(
        run_id=sample_run["run_id"],
        input_fingerprint="b" * 64,
        resolved_entities=[{"local_id": "ms_1"}],
        entity_count=1,
        deferred_link_count=0,
        skipped_statement_count=0,
        shacl_report={},
        built_at=built_at,
    ))
    db_session.add(AuthorityMatch(
        run_id=sample_run["run_id"],
        control_number="990001800310205171",
        entity_text="Moses",
        entity_kind="person",
        created_at=built_at - timedelta(days=1),
        approved_at=built_at + timedelta(minutes=5),  # approved AFTER the build
    ))
    await db_session.commit()

    executed = {"ran": False}

    async def fake_re_enrich(*_a, **_k):
        executed["ran"] = True
        return {"content_changed": False}

    monkeypatch.setattr(
        "app.pipeline.authority_re_enrich.re_enrich_run", fake_re_enrich,
    )

    result = await exec_module.execute_hmo_item_build(
        db_session, sample_run["run_id"],
        force_rebuild=False, refresh_authority=True,
        on_progress=None,
    )
    assert executed["ran"] is True  # the pipeline re-ran past the short-circuit
    assert result.refreshed_authority is True
