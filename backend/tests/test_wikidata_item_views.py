"""Tests for fetch_merged_wikidata_items."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models.item_override import WikidataItemOverride
from app.models.wikibase_cloud_write import (
    CHANNEL_WIKIDATA_UPLOAD,
    OPERATION_CREATE,
    TARGET_ITEM,
    WikibaseCloudWrite,
)
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.models.wikibase_entity_mapping import ENTITY_KIND_INSTANCE, WikibaseEntityMapping
from app.pipeline.wikidata_item_views import fetch_merged_wikidata_items, fetch_merged_wikidata_item
from app.pipeline.wikidata_verdict_cache import wikidata_verdict_stable_input_fingerprint


@pytest.mark.asyncio
async def test_merged_view_joins_upload_audit_and_ledger(db_session) -> None:
    run_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    db_session.add(
        WikidataStudioCache(
            run_id=run_id,
            approved_only=True,
            input_fingerprint="f" * 64,
            result_items=[
                {
                    "local_id": "990001234",
                    "entity_type": "manuscript",
                    "labels": {"en": "MS 1234"},
                    "descriptions": {"en": "A manuscript"},
                    "statements": [],
                    "validation_issues": [],
                },
            ],
            quickstatements="",
            summary={
                "total_items": 1,
                "manuscripts": 1,
                "persons": 0,
                "works": 0,
                "statements": 0,
            },
            approved_match_count=1,
            pending_match_count=0,
            used_match_count=1,
            record_count=1,
        )
    )
    db_session.add(
        WikibaseCloudWrite(
            actor_user_id=actor_id,
            run_id=run_id,
            channel=CHANNEL_WIKIDATA_UPLOAD,
            operation=OPERATION_CREATE,
            target_kind=TARGET_ITEM,
            target_key="990001234",
            wikibase_id="Q99",
            outcome_message="created",
            created_at=datetime.now(timezone.utc),
        )
    )
    db_session.add(
        WikibaseEntityMapping(
            ontology_uri="wikidata:marc:990001234",
            entity_kind=ENTITY_KIND_INSTANCE,
            wikibase_id="Q88",
            run_id=None,
            label="ledger hit",
        )
    )
    override = WikidataItemOverride(
        run_id=run_id,
        local_id="990001234",
        labels={"en": "Curator label"},
    )
    override.ai_verdict = {
        "overall": "pass",
        "model": "gemini-3.5-flash",
        "evaluator": "wikidata_item",
        "stable_cache_key": wikidata_verdict_stable_input_fingerprint({
            "local_id": "990001234",
            "entity_type": "manuscript",
            "labels": {"en": "Curator label"},
            "descriptions": {"en": "A manuscript"},
            "statements": [],
            "validation_issues": [],
        }),
    }
    db_session.add(override)
    await db_session.commit()

    items = await fetch_merged_wikidata_items(db_session, run_id)
    assert len(items) == 1
    row = items[0]
    assert row["labels"]["en"] == "Curator label"
    assert row["upload_outcome"] == OPERATION_CREATE
    assert row["upload_message"] == "created"
    assert row["upload_at"] is not None
    assert row["existing_qid"] == "Q88"
    assert row["on_wikidata"] is True
    assert row["ai_verdict"]["overall"] == "pass"


@pytest.mark.asyncio
async def test_merged_view_excludes_non_public_entity_types(db_session) -> None:
    run_id = uuid.uuid4()
    db_session.add(
        WikidataStudioCache(
            run_id=run_id,
            approved_only=True,
            source="canonical",
            input_fingerprint="f" * 64,
            result_items=[
                {
                    "local_id": "990001234",
                    "entity_type": "manuscript",
                    "labels": {"en": "MS 1234"},
                    "statements": [],
                    "validation_issues": [],
                },
                {
                    "local_id": "QDraft_CU_1",
                    "entity_type": "Codicological_Unit",
                    "labels": {"en": "CU"},
                    "statements": [],
                    "validation_issues": [],
                },
                {
                    "local_id": "QDraft_Person_1",
                    "entity_type": "E21_Person",
                    "labels": {"en": "Person"},
                    "statements": [],
                    "validation_issues": [],
                },
            ],
            quickstatements="",
            summary={"total_items": 3},
            approved_match_count=0,
            pending_match_count=0,
            used_match_count=0,
            record_count=3,
        )
    )
    await db_session.commit()

    items = await fetch_merged_wikidata_items(db_session, run_id, source="canonical")
    assert [row["local_id"] for row in items] == ["990001234"]


@pytest.mark.asyncio
async def test_fetch_merged_wikidata_item_returns_one_full_row(db_session) -> None:
    run_id = uuid.uuid4()
    db_session.add(
        WikidataStudioCache(
            run_id=run_id,
            approved_only=True,
            source="canonical",
            input_fingerprint="f" * 64,
            result_items=[
                {
                    "local_id": "990001234",
                    "entity_type": "manuscript",
                    "labels": {"en": "MS 1234"},
                    "statements": [{"property": "P31", "value": "Q87167"}],
                    "validation_issues": [],
                },
            ],
            quickstatements="",
            summary={"total_items": 1},
            approved_match_count=0,
            pending_match_count=0,
            used_match_count=0,
            record_count=1,
        )
    )
    await db_session.commit()

    row = await fetch_merged_wikidata_item(
        db_session, run_id, "990001234", source="canonical",
    )
    assert row is not None
    assert row["local_id"] == "990001234"
    assert len(row["statements"]) == 1
    assert await fetch_merged_wikidata_item(
        db_session, run_id, "missing", source="canonical",
    ) is None
