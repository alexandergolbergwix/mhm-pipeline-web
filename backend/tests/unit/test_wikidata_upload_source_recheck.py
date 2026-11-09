"""Upload job re-checks the Studio source before each write (fail closed)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.pipeline import wikidata_upload as upload
from app.pipeline import wikidata_upload_job as upload_job
from app.models.run_job import RunJob
from converter.wikidata.item_models import WikidataItem


def _patch_upload_job(monkeypatch, *, build_items, cache_is_current) -> list[str]:
    writes: list[str] = []

    async def build(*args, **kwargs):
        return build_items

    async def get_cache_row(*args, **kwargs):
        return SimpleNamespace(
            input_fingerprint="a" * 64,
            summary={"overrides_applied": True, "source_items_sha256": "b" * 64},
        )

    class FakeUploader:
        def __init__(self, **kwargs):
            pass

        def ensure_authenticated(self):
            return None

    async def upload_items(items, **kwargs):
        writes.append(items[0].local_id)
        return [upload.UploadOutcome(
            local_id=items[0].local_id, label=items[0].local_id, entity_type="other",
            qid="Q1", status="success", message="written", added_properties=[],
        )]

    async def persist(*args, **kwargs):
        return None

    monkeypatch.setattr("app.routers.wikidata_studio._build_native_items", build)
    monkeypatch.setattr("app.routers.wikidata_studio._get_studio_cache_row", get_cache_row)
    monkeypatch.setattr("app.routers.wikidata_studio._studio_cache_is_current", cache_is_current)
    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", FakeUploader)
    monkeypatch.setattr(
        "converter.wikidata.reconciler.prewarm_reconciler_for_items", lambda *a: None,
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.prefetch_entities_for_upload",
        lambda *a, **kw: ({}, {}),
    )
    monkeypatch.setattr(upload, "_make_reconciler", lambda: SimpleNamespace(_cache={}))
    monkeypatch.setattr(upload, "upload_items", upload_items)
    monkeypatch.setattr(upload, "persist_upload_outcomes", persist)
    monkeypatch.setattr(upload, "remember_created_qid", lambda *a, **kw: None)
    monkeypatch.setattr(
        upload, "build_upload_corpus_quality_preflight",
        lambda items: upload.UploadCorpusQualityPreflight(
            decisions={
                item.local_id: upload.UploadQualityDecision(eligible=True)
                for item in items
            },
        ),
    )
    return writes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed_from_check, expected_writes, expected_status",
    [
        (None, 2, "succeeded"),
        (2, 1, "failed"),
    ],
)
async def test_job_rechecks_studio_source_before_each_write(
    sample_run, db_session, monkeypatch,
    changed_from_check, expected_writes, expected_status,
) -> None:
    monkeypatch.setattr(upload_job, "WRITE_FRESHNESS_BATCH_SIZE", 1)
    items = [
        WikidataItem(local_id=f"item-{n}", entity_type="other", labels={"en": f"Item {n}"}, statements=[])
        for n in (1, 2)
    ]
    checks: list[str] = []

    async def cache_is_current(db, run_id, **kwargs):
        checks.append(kwargs["fingerprint"])
        return changed_from_check is None or len(checks) < changed_from_check

    writes = _patch_upload_job(monkeypatch, build_items=items, cache_is_current=cache_is_current)
    job = RunJob(
        project_id=sample_run["project_id"], run_id=sample_run["run_id"],
        kind="wikidata_upload", status="running",
        params={"upload_target": "live", "_wikidata_token": "fake"}, progress={},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    await upload_job.run_wikidata_upload_job(job.id)
    await db_session.refresh(job)

    assert job.status == expected_status
    assert len(writes) == expected_writes
    assert checks == ["a" * 64, "a" * 64]
    if expected_status == "failed":
        assert "changed during upload" in (job.error or "")
