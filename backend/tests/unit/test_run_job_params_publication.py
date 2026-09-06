"""Publication workers cannot be started through the generic job endpoint."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.pipeline.run_job_params import prepare_job_params


@pytest.mark.asyncio
async def test_publication_job_kinds_require_the_publication_api() -> None:
    auth = SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4()), kek=b"x" * 32)
    for kind in ("wikidata_publication_prepare", "wikidata_publication_execution", "wikidata_publication_dry_run", "wikidata_publication_ai_review"):
        with pytest.raises(HTTPException) as error:
            await prepare_job_params(
                object(), auth, run_id=uuid.uuid4(), kind=kind, params={}
            )
        assert error.value.status_code == 403
