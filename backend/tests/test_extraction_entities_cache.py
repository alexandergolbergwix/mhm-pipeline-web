"""Run-scoped cache + ETag for GET /extraction/entities."""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.cache.scoped_cache import clear_memory_cache_for_tests
from app.models.extraction_approval import ExtractionApproval


@pytest.fixture(autouse=True)
def _wipe_scoped_memory() -> None:
    clear_memory_cache_for_tests()
    yield
    clear_memory_cache_for_tests()


@pytest_asyncio.fixture
async def extraction_run_with_entities(db_session, sample_run):
    """Seed ner_results.json + one approval row for list_entities."""
    from app.routers.extraction import _entity_id, _results_path, _run_output_dir

    run_id = sample_run["run_id"]
    cn = sample_run["control_number"]
    out_dir = _run_output_dir(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    ner = [{
        "control_number": cn,
        "entities": [{
            "text": "Maimonides",
            "type": "PERSON",
            "role": "AUTHOR",
            "source": "person_ner",
            "start": 0,
            "end": 10,
            "confidence": 0.9,
            "model_confidence": 0.95,
        }],
    }]
    _results_path(run_id).write_text(json.dumps(ner), encoding="utf-8")

    ext = ExtractionApproval(
        run_id=run_id,
        control_number=cn,
        source="person_ner",
        text="Maimonides",
        start=0,
        end=10,
        type="PERSON",
        role="AUTHOR",
        confidence=0.9,
        model_confidence=0.95,
        approved=False,
        exists_in={"status": "grounded", "fields": ["contributors"], "note": "ok"},
    )
    db_session.add(ext)
    await db_session.commit()

    entity_id = _entity_id(
        control_number=cn, source="person_ner",
        text="Maimonides", start=0, end=10,
    )
    return {**sample_run, "entity_id": entity_id, "approval": ext}


class TestListEntitiesCacheHeaders:
    @pytest.mark.asyncio
    async def test_returns_etag_and_cache_miss_first(self, extraction_run_with_entities) -> None:
        run_id = extraction_run_with_entities["run_id"]
        client = extraction_run_with_entities["client"]

        r = await client.get(f"/api/runs/{run_id}/extraction/entities")
        assert r.status_code == 200
        assert r.headers.get("etag")
        assert r.headers.get("x-cache") == "MISS"
        body = r.json()
        assert body["total"] >= 1

    @pytest.mark.asyncio
    async def test_second_request_is_cache_hit(self, extraction_run_with_entities) -> None:
        run_id = extraction_run_with_entities["run_id"]
        client = extraction_run_with_entities["client"]
        url = f"/api/runs/{run_id}/extraction/entities"

        await client.get(url)
        r2 = await client.get(url)
        assert r2.status_code == 200
        assert r2.headers.get("x-cache") == "HIT"

    @pytest.mark.asyncio
    async def test_if_none_match_returns_304(self, extraction_run_with_entities) -> None:
        run_id = extraction_run_with_entities["run_id"]
        client = extraction_run_with_entities["client"]
        url = f"/api/runs/{run_id}/extraction/entities"

        r1 = await client.get(url)
        etag = r1.headers["etag"]
        r2 = await client.get(url, headers={"If-None-Match": etag})
        assert r2.status_code == 304

    @pytest.mark.asyncio
    async def test_no_cache_bypasses_hit(self, extraction_run_with_entities) -> None:
        run_id = extraction_run_with_entities["run_id"]
        client = extraction_run_with_entities["client"]
        url = f"/api/runs/{run_id}/extraction/entities"

        await client.get(url)
        r = await client.get(f"{url}?no_cache=true")
        assert r.status_code == 200
        assert r.headers.get("x-cache") == "BYPASS"

    @pytest.mark.asyncio
    async def test_patch_invalidates_entities_cache(
        self, db_session, extraction_run_with_entities,
    ) -> None:
        run_id = extraction_run_with_entities["run_id"]
        entity_id = extraction_run_with_entities["entity_id"]
        client = extraction_run_with_entities["client"]
        url = f"/api/runs/{run_id}/extraction/entities"

        r1 = await client.get(url)
        etag_before = r1.headers["etag"]

        await client.patch(
            f"/api/runs/{run_id}/extraction/entities/{entity_id}",
            json={"override_text": "Rambam"},
        )

        r2 = await client.get(url)
        assert r2.headers.get("x-cache") == "MISS"
        assert r2.headers["etag"] != etag_before
        assert r2.json()["entities"][0]["text"] == "Rambam"
