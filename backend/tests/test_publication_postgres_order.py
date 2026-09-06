"""Publication preparation against PostgreSQL's locale-sensitive sort order."""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.wikidata_studio_cache import WikidataStudioCache
from app.publication import (
    PrepareRequest, ProfileRef, PublicationModule, SummaryQuery, TargetRef,
)
from app.publication.gateway import FakeWikidataGateway
from app.publication.repository import InMemoryPublicationRepository
from app.publication.runtime import StudioCacheProjectionSource


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate", [False, True])
async def test_postgres_release_accepts_unique_keys_and_rejects_duplicates(duplicate: bool) -> None:
    url = os.environ.get("PUBLICATION_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set PUBLICATION_TEST_POSTGRES_URL to a disposable PostgreSQL database")
    engine = create_async_engine(url)
    schema = f"publication_test_{uuid.uuid4().hex}"
    keys = ["mazal:987007313624205171", "QDraft_MS_990000403370205171",
            "QDraft_Work_____by", "QDraft_Work_______by"]
    if duplicate:
        keys.append(keys[0])
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await connection.run_sync(WikidataStudioCache.__table__.create)
                async with AsyncSession(bind=connection) as session:
                    run_id = uuid.uuid4()
                    session.add(WikidataStudioCache(
                        run_id=run_id, approved_only=True, source="canonical",
                        input_fingerprint="a" * 64,
                        result_items=[{"local_id": key, "entity_type": "work",
                                       "labels": {"en": key}, "statements": []} for key in keys],
                    ))
                    await session.flush()
                    source = StudioCacheProjectionSource(session)
                    snapshot = await source.current_snapshot(run_id=run_id, source="canonical", approved_only=True)
                    module = PublicationModule(projection_source=source,
                        repository=InMemoryPublicationRepository(), gateway=FakeWikidataGateway())
                    request = PrepareRequest(run_id=str(run_id), source_snapshot=snapshot,
                        profile=ProfileRef(name="mhm-wikidata", version="1"),
                        target=TargetRef(site="www.wikidata.org", environment="production"),
                        actor_id="curator", idempotency_key="order-test")
                    if duplicate:
                        with pytest.raises(ValueError, match="unique canonical keys"):
                            await module.prepare(request)
                    else:
                        operation = await module.prepare(request)
                        summary = await module.read(SummaryQuery(operation.publication_id))
                        assert summary.entity_count == 4
                        assert summary.state == "prepared"
                await connection.rollback()
    finally:
        await engine.dispose()
