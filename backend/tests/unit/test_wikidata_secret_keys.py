"""Target-scoped Wikidata Settings secrets (live vs test.wikidata.org)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.run_job import JOB_KIND_WIKIDATA_UPLOAD
from app.pipeline.run_job_params import prepare_job_params
from app.pipeline.wikidata_upload import (
    UPLOAD_TARGET_DRY_RUN,
    UPLOAD_TARGET_LIVE,
    UPLOAD_TARGET_TEST,
    WIKIDATA_SECRET_LIVE,
    WIKIDATA_SECRET_TEST,
    wikidata_secret_key_for_target,
)
from app.routers.api_keys import _KEY_ORDER, _VALID_KEYS


def test_secret_key_for_test_target() -> None:
    assert wikidata_secret_key_for_target(UPLOAD_TARGET_TEST) == WIKIDATA_SECRET_TEST


def test_secret_key_for_live_and_dry_run() -> None:
    assert wikidata_secret_key_for_target(UPLOAD_TARGET_LIVE) == WIKIDATA_SECRET_LIVE
    assert wikidata_secret_key_for_target(UPLOAD_TARGET_DRY_RUN) == WIKIDATA_SECRET_LIVE
    assert wikidata_secret_key_for_target(None) == WIKIDATA_SECRET_LIVE


def test_api_keys_allowlist_includes_wikidata_test() -> None:
    assert "wikidata_test" in _VALID_KEYS
    assert _KEY_ORDER.index("wikidata") < _KEY_ORDER.index("wikidata_test")


@pytest.mark.asyncio
async def test_prepare_job_params_uses_test_secret_for_test_upload(monkeypatch) -> None:
    seen: list[str] = []

    async def _unwrap(_db, _auth, name: str) -> str | None:
        seen.append(name)
        if name == WIKIDATA_SECRET_TEST:
            return "Alexander Goldberg IL@MHMPipelineTest:secret"
        return None

    monkeypatch.setattr(
        "app.routers.wikidata_studio._unwrap_user_secret",
        _unwrap,
    )
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4()), kek=b"x" * 32)
    merged = await prepare_job_params(
        object(),
        auth,
        run_id=uuid.uuid4(),
        kind=JOB_KIND_WIKIDATA_UPLOAD,
        params={"upload_target": "test", "approved_only": True, "source": "canonical"},
    )
    assert seen == [WIKIDATA_SECRET_TEST]
    assert merged["_wikidata_token"].startswith("Alexander Goldberg IL@MHMPipelineTest:")


@pytest.mark.asyncio
async def test_prepare_job_params_rejects_test_upload_without_test_secret(
    monkeypatch,
) -> None:
    async def _unwrap(_db, _auth, name: str) -> str | None:
        if name == WIKIDATA_SECRET_LIVE:
            return "Alexander Goldberg IL@MHMPipeline:live-only"
        return None

    monkeypatch.setattr(
        "app.routers.wikidata_studio._unwrap_user_secret",
        _unwrap,
    )
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4()), kek=b"x" * 32)
    with pytest.raises(HTTPException) as exc:
        await prepare_job_params(
            object(),
            auth,
            run_id=uuid.uuid4(),
            kind=JOB_KIND_WIKIDATA_UPLOAD,
            params={"upload_target": "test"},
        )
    assert exc.value.status_code == 400
    assert "test" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_prepare_job_params_retires_legacy_live_upload() -> None:
    with pytest.raises(HTTPException) as exc:
        await prepare_job_params(
            object(),
            SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4()), kek=b"x" * 32),
            run_id=uuid.uuid4(),
            kind=JOB_KIND_WIKIDATA_UPLOAD,
            params={"upload_target": "live"},
        )
    assert exc.value.status_code == 410
    assert "versioned" in str(exc.value.detail).lower()
