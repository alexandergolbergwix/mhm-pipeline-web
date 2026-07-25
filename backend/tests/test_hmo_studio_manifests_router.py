"""GET /hmo-studio/manifests list + single-JSON preview."""

from __future__ import annotations

import json

import pytest

from app.pipeline import hmo_studio as hmo_pipeline


@pytest.mark.asyncio
async def test_list_manifests_empty(sample_run) -> None:
    run_id = sample_run["run_id"]
    response = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/manifests",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["manifest_count"] == 0
    assert body["manifests"] == []


@pytest.mark.asyncio
async def test_list_and_get_manifest(sample_run) -> None:
    run_id = sample_run["run_id"]
    manifest_dir = hmo_pipeline.manifest_dir_for_run(str(run_id))
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "type": "Manifest",
        "items": [{"id": "canvas-1"}],
        "structures": [{"id": "range-1"}],
        "annotations": [{"items": [{"id": "a1"}, {"id": "a2"}]}],
        "seeAlso": [{"id": "seealso-1"}],
    }
    (manifest_dir / "MS_Heb.8.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    listed = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/manifests",
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["manifest_count"] == 1
    assert body["manifests"][0]["shelfmark"] == "Heb.8"
    assert body["manifests"][0]["canvas_count"] == 1
    assert body["manifests"][0]["range_count"] == 1
    assert body["manifests"][0]["annotation_count"] == 2
    assert body["manifests"][0]["seealso_count"] == 1

    got = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/manifests/Heb.8",
    )
    assert got.status_code == 200
    assert got.json()["type"] == "Manifest"

    missing = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/manifests/Missing",
    )
    assert missing.status_code == 404

    with pytest.raises(ValueError):
        hmo_pipeline.manifest_path_for_shelfmark(str(run_id), "..")
    with pytest.raises(ValueError):
        hmo_pipeline.manifest_path_for_shelfmark(str(run_id), "a/b")

    for f in manifest_dir.glob("MS_*.json"):
        f.unlink()
