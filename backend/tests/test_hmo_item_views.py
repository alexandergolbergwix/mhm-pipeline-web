"""Tests for the merged HMO Wikibase item review view.

Pins: `fetch_merged_hmo_items` must surface the *durable* outcome of the
last upload attempt (from `wikibase_cloud_writes`), not just the binary
would_create/created status derived from `wikibase_entity_mappings`.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.wikibase_cloud_write import (
    CHANNEL_ITEM_UPLOAD,
    OPERATION_ADOPT,
    OPERATION_FAILED,
    TARGET_ITEM,
    WikibaseCloudWrite,
)
from app.models.wikibase_entity_mapping import ENTITY_KIND_INSTANCE, WikibaseEntityMapping
from app.pipeline.hmo_item_views import ItemBuildMissingError, fetch_merged_hmo_items


async def _seed_cache(db_session, run_id, entities: list[dict]) -> None:
    db_session.add(
        HmoStudioItemCache(
            run_id=run_id,
            input_fingerprint="0" * 64,
            resolved_entities=entities,
            entity_count=len(entities),
            deferred_link_count=0,
            skipped_statement_count=0,
        )
    )
    await db_session.commit()


def _entity(local_id: str, source_uri: str) -> dict:
    return {
        "local_id": local_id,
        "labels": {"en": local_id},
        "descriptions": {},
        "class_qid": "Q1",
        "source_uri": source_uri,
        "claims": [],
        "deferred_links": [],
    }


@pytest.mark.asyncio
async def test_raises_when_no_build_exists(db_session) -> None:
    with pytest.raises(ItemBuildMissingError):
        await fetch_merged_hmo_items(db_session, uuid.uuid4())


@pytest.mark.asyncio
async def test_never_attempted_item_has_null_upload_outcome(db_session) -> None:
    run_id = uuid.uuid4()
    await _seed_cache(db_session, run_id, [_entity("QDraft_A", "http://example.org#A")])

    items = await fetch_merged_hmo_items(db_session, run_id)

    assert len(items) == 1
    assert items[0]["status"] == "would_create"
    assert items[0]["upload_outcome"] is None
    assert items[0]["upload_message"] == ""
    assert items[0]["upload_at"] is None


@pytest.mark.asyncio
async def test_adopted_item_surfaces_adopt_outcome_and_reason(db_session, sample_run) -> None:
    """An item linked to a pre-existing live Wikibase item (via reconcile)
    must show "adopted" with the reconcile reason, not be indistinguishable
    from a brand-new create."""
    run_id = sample_run["run_id"]
    source_uri = "http://example.org#MS1"
    await _seed_cache(db_session, run_id, [_entity("QDraft_MS1", source_uri)])

    db_session.add(
        WikibaseEntityMapping(
            ontology_uri=source_uri,
            entity_kind=ENTITY_KIND_INSTANCE,
            wikibase_id="Q999",
            run_id=run_id,
            label="Test MS",
        )
    )
    db_session.add(
        WikibaseCloudWrite(
            actor_user_id=sample_run["user_id"],
            run_id=run_id,
            channel=CHANNEL_ITEM_UPLOAD,
            operation=OPERATION_ADOPT,
            target_kind=TARGET_ITEM,
            target_key=source_uri,
            wikibase_id="Q999",
            outcome_message="adopted via reconcile: found live",
        )
    )
    await db_session.commit()

    items = await fetch_merged_hmo_items(db_session, run_id)

    assert len(items) == 1
    item = items[0]
    assert item["status"] == "created"  # backward-compatible binary status untouched
    assert item["wikibase_id"] == "Q999"
    assert item["upload_outcome"] == OPERATION_ADOPT
    assert "found live" in item["upload_message"]
    assert item["upload_at"] is not None


@pytest.mark.asyncio
async def test_failed_item_surfaces_failure_reason(db_session, sample_run) -> None:
    run_id = sample_run["run_id"]
    source_uri = "http://example.org#Bad"
    await _seed_cache(db_session, run_id, [_entity("QDraft_Bad", source_uri)])

    db_session.add(
        WikibaseCloudWrite(
            actor_user_id=sample_run["user_id"],
            run_id=run_id,
            channel=CHANNEL_ITEM_UPLOAD,
            operation=OPERATION_FAILED,
            target_kind=TARGET_ITEM,
            target_key=source_uri,
            outcome_message="Wikibase Cloud 500",
        )
    )
    await db_session.commit()

    items = await fetch_merged_hmo_items(db_session, run_id)

    assert items[0]["upload_outcome"] == OPERATION_FAILED
    assert items[0]["upload_message"] == "Wikibase Cloud 500"
    assert items[0]["wikibase_id"] is None
    assert items[0]["status"] == "would_create"


@pytest.mark.asyncio
async def test_outcome_scoped_by_run_id(db_session, sample_run) -> None:
    """A write recorded for a different run must never leak into this
    run's view, even for the same source_uri."""
    run_id = sample_run["run_id"]
    other_run_id = uuid.uuid4()
    source_uri = "http://example.org#Shared"
    await _seed_cache(db_session, run_id, [_entity("QDraft_S", source_uri)])

    db_session.add(
        WikibaseCloudWrite(
            actor_user_id=sample_run["user_id"],
            run_id=other_run_id,
            channel=CHANNEL_ITEM_UPLOAD,
            operation=OPERATION_ADOPT,
            target_kind=TARGET_ITEM,
            target_key=source_uri,
            wikibase_id="Q1",
        )
    )
    await db_session.commit()

    items = await fetch_merged_hmo_items(db_session, run_id)
    assert items[0]["upload_outcome"] is None
