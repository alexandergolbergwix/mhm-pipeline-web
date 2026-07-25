"""Router/worker tests for POST /hmo-studio/upload-manifests (background job)."""

from __future__ import annotations

import json
import uuid

import pytest

from app.models.run_job import JOB_KIND_HMO_MANIFEST_UPLOAD, JOB_STATUS_SUCCEEDED, RunJob
from app.pipeline import hmo_studio as hmo_pipeline


@pytest.mark.asyncio
async def test_upload_manifests_requires_build_first(sample_run) -> None:
    run_id = sample_run["run_id"]
    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/upload-manifests",
        json={"dry_run": True},
    )
    assert response.status_code == 409
    assert "Build manifests" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upload_manifests_enqueues_dry_run_job(
    sample_run, db_session, monkeypatch,
) -> None:
    from app.pipeline.hmo_manifest_upload_job import run_hmo_manifest_upload_job

    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", lambda *_a, **_k: None)

    run_id = sample_run["run_id"]
    manifest_dir = hmo_pipeline.manifest_dir_for_run(str(run_id))
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "MS_Test.json").write_text(
        json.dumps({"items": [], "structures": [], "annotations": []}),
        encoding="utf-8",
    )

    async def _fake_upload(**_kwargs):
        from app.pipeline.hmo_studio import HmoUploadOutcome, HmoUploadResult

        return HmoUploadResult(
            dry_run=True,
            uploaded=1,
            unchanged=0,
            failed=0,
            outcomes=[HmoUploadOutcome(
                shelfmark="Test",
                page_url="",
                status="dry_run",
                message="would upload",
                edit_id=None,
                new_revid=None,
                canvas_count=0,
                range_count=0,
                annotation_count=0,
            )],
        )

    monkeypatch.setattr(hmo_pipeline, "upload_manifests_for_run", _fake_upload)

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/upload-manifests",
        json={"dry_run": True},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == JOB_KIND_HMO_MANIFEST_UPLOAD
    assert body["params"]["dry_run"] is True

    await run_hmo_manifest_upload_job(uuid.UUID(body["id"]))
    db_session.expire_all()
    job = await db_session.get(RunJob, uuid.UUID(body["id"]))
    assert job is not None
    assert job.status == JOB_STATUS_SUCCEEDED
    assert job.result["uploaded"] == 1

    for f in manifest_dir.glob("MS_*.json"):
        f.unlink()
