"""Tests for the HMO Studio per-item override model + migration 0031.

Mirrors how ``WikidataItemOverride`` is exercised elsewhere (see
``test_export_router.py``): the table is created from the SQLAlchemy
model via the test DB's ``Base.metadata.create_all`` (see
``conftest.py::_engine``), so a clean round trip through ``db_session``
pins that the model's column set is self-consistent. The revision-chain
assertion pins that migration 0031 is wired onto the current head.
"""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy import select

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.hmo_studio_item_override import HmoStudioItemOverride


def test_model_imports_cleanly() -> None:
    assert HmoStudioItemOverride.__tablename__ == "hmo_studio_item_overrides"


def test_migration_0031_chains_onto_current_head() -> None:
    migration = importlib.import_module(
        "app.migrations.versions.0031_hmo_studio_item_overrides"
    )
    assert migration.revision == "0031_hmo_studio_item_overrides"
    assert migration.down_revision == "0030_run_job_claims"


@pytest.mark.asyncio
async def test_override_round_trips_through_the_test_db(db_session, sample_run) -> None:
    run_id = sample_run["run_id"]
    override = HmoStudioItemOverride(
        run_id=run_id,
        local_id="QDraft_MS1",
        labels={"en": "Test MS"},
        add_statements=[{"property_id": "P1", "datatype": "string", "value": "x"}],
        approved=True,
    )
    db_session.add(override)
    await db_session.commit()

    fetched = (
        await db_session.execute(
            select(HmoStudioItemOverride).where(HmoStudioItemOverride.run_id == run_id)
        )
    ).scalar_one()
    assert fetched.local_id == "QDraft_MS1"
    assert fetched.labels == {"en": "Test MS"}
    assert fetched.approved is True
    assert fetched.ai_verdict is None
    assert fetched.ai_verdict_at is None


@pytest.mark.asyncio
async def test_shacl_report_column_on_item_cache(db_session, sample_run) -> None:
    cache = HmoStudioItemCache(
        run_id=sample_run["run_id"],
        input_fingerprint="f" * 64,
        shacl_report={
            "QDraft_MS1": [
                {"path": "hm:has_title", "message": "missing", "severity": "Violation", "value": None},
            ],
        },
    )
    db_session.add(cache)
    await db_session.commit()

    fetched = (
        await db_session.execute(
            select(HmoStudioItemCache).where(HmoStudioItemCache.run_id == sample_run["run_id"])
        )
    ).scalar_one()
    assert fetched.shacl_report["QDraft_MS1"][0]["message"] == "missing"
