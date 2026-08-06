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
from app.models.wikibase_entity_mapping import ENTITY_KIND_INSTANCE, WikibaseEntityMapping
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.pipeline.wikidata_item_views import fetch_merged_wikidata_item, fetch_merged_wikidata_items
from app.pipeline.wikidata_verdict_cache import (
    attach_local_reference_targets,
    wikidata_verdict_stable_input_fingerprint,
)
from app.pipeline.wikidata_verify_fixture import slim_item_for_verdict_persist


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
async def test_merged_view_keeps_subset_verdict_with_local_target(db_session) -> None:
    run_id = uuid.uuid4()
    item = {
        "local_id": "QDraft_MS_1",
        "entity_type": "manuscript",
        "labels": {"en": "Source manuscript"},
        "statements": [
            {
                "property_id": "P1574",
                "value": "__LOCAL:QDraft_MS_2",
                "value_type": "local",
            },
        ],
        "validation_issues": [],
    }
    target = {
        "local_id": "QDraft_MS_2",
        "entity_type": "manuscript",
        "labels": {"en": "Target manuscript"},
        "statements": [],
        "validation_issues": [],
    }
    db_session.add(
        WikidataStudioCache(
            run_id=run_id,
            approved_only=True,
            source="canonical",
            input_fingerprint="f" * 64,
            result_items=[item, target],
            quickstatements="",
            summary={"total_items": 2},
            approved_match_count=2,
            pending_match_count=0,
            used_match_count=2,
            record_count=2,
        )
    )
    subset_item = dict(item)
    subset_item["statements"] = [dict(item["statements"][0])]
    attach_local_reference_targets([subset_item])
    stable_item = slim_item_for_verdict_persist(subset_item)
    override = WikidataItemOverride(
        run_id=run_id,
        local_id="QDraft_MS_1",
    )
    override.ai_verdict = {
        "overall": "partial",
        "model": "deepseek-ai/DeepSeek-V4-Flash",
        "evaluator": "wikidata_item",
        "cache_key": "old-scope-key",
        "cache_key_version": "records_marc_v6",
        "stable_cache_key": wikidata_verdict_stable_input_fingerprint(
            stable_item,
            "deepseek-ai/DeepSeek-V4-Flash",
        ),
    }
    db_session.add(override)
    await db_session.commit()

    items = await fetch_merged_wikidata_items(
        db_session,
        run_id,
        approved_only=True,
        source="canonical",
    )

    row = next(item for item in items if item["local_id"] == "QDraft_MS_1")
    assert row["ai_verdict"]["overall"] == "partial"


@pytest.mark.asyncio
async def test_merged_view_keeps_verdict_after_probe_qid_adoption(
    db_session,
    monkeypatch,
) -> None:
    """Verify adopts an existing_qid; the Studio cache may still be CREATE.

    The review table must adopt from the probe cache before sanitising, or the
    verdict persists and then reads as unknown (Rule W-169 / run 48ba6c13).
    """
    run_id = uuid.uuid4()
    item = {
        "local_id": "QDraft_Person_110",
        "entity_type": "person",
        "labels": {"he": "מאוריציו"},
        "descriptions": {},
        "statements": [
            {
                "property": "P8189",
                "property_id": "P8189",
                "value": "987007263446605171",
                "value_type": "string",
            },
        ],
        "validation_issues": [],
    }
    db_session.add(
        WikidataStudioCache(
            run_id=run_id,
            approved_only=True,
            source="canonical",
            input_fingerprint="f" * 64,
            result_items=[item],
            quickstatements="",
            summary={"total_items": 1},
            approved_match_count=1,
            pending_match_count=0,
            used_match_count=1,
            record_count=1,
        )
    )
    adopted = {
        **item,
        "existing_qid": "Q178293",
    }
    stable_item = slim_item_for_verdict_persist(adopted)
    override = WikidataItemOverride(run_id=run_id, local_id="QDraft_Person_110")
    override.ai_verdict = {
        "overall": "fail",
        "model": "deepseek-ai/DeepSeek-V4-Flash",
        "evaluator": "wikidata_item",
        "cache_key": "verify-adopted-key",
        "cache_key_version": "records_marc_v6",
        "stable_cache_key": wikidata_verdict_stable_input_fingerprint(
            stable_item,
            "deepseek-ai/DeepSeek-V4-Flash",
        ),
    }
    db_session.add(override)
    await db_session.commit()

    async def fake_attach(_db_factory, rows):
        for row in rows:
            row["_wikidata_existence"] = {
                "status": "candidates_found",
                "candidates": [{
                    "qid": "Q178293",
                    "matched_on": "P8189=987007263446605171",
                    "label": "Maurizio",
                }],
            }

    monkeypatch.setattr(
        "app.pipeline.wikidata_duplicate_probe.attach_cached_duplicate_evidence",
        fake_attach,
    )

    items = await fetch_merged_wikidata_items(
        db_session,
        run_id,
        approved_only=True,
        source="canonical",
    )
    row = items[0]
    assert row["existing_qid"] == "Q178293"
    assert row["ai_verdict"]["overall"] == "fail"


@pytest.mark.asyncio
async def test_merged_view_keeps_verdict_when_live_labels_match_verify(
    db_session,
    monkeypatch,
) -> None:
    """Verify glosses statement QIDs; list reads must gloss before sanitise."""
    run_id = uuid.uuid4()
    item = {
        "local_id": "QDraft_MS_1",
        "entity_type": "manuscript",
        "labels": {"en": "MS"},
        "descriptions": {},
        "statements": [
            {
                "property": "P31",
                "property_id": "P31",
                "value": "Q87167",
                "value_type": "wikibase-item",
                "value_label": "manuscript",
            },
            {
                "property": "P1071",
                "property_id": "P1071",
                "value": "Q2626422",
                "value_type": "wikibase-item",
            },
        ],
        "validation_issues": [],
    }
    labeled = {
        **item,
        "statements": [
            dict(item["statements"][0]),
            {
                **item["statements"][1],
                "value_label": "Akka",
            },
        ],
    }
    db_session.add(
        WikidataStudioCache(
            run_id=run_id,
            approved_only=True,
            source="canonical",
            input_fingerprint="f" * 64,
            result_items=[item],
            quickstatements="",
            summary={"total_items": 1},
            approved_match_count=1,
            pending_match_count=0,
            used_match_count=1,
            record_count=1,
        )
    )
    stable_item = slim_item_for_verdict_persist(labeled)
    override = WikidataItemOverride(run_id=run_id, local_id="QDraft_MS_1")
    override.ai_verdict = {
        "overall": "full",
        "model": "deepseek-ai/DeepSeek-V4-Flash",
        "evaluator": "wikidata_item",
        "cache_key": "verify-labeled-key",
        "cache_key_version": "records_marc_v6",
        "stable_cache_key": wikidata_verdict_stable_input_fingerprint(
            stable_item,
            "deepseek-ai/DeepSeek-V4-Flash",
        ),
    }
    db_session.add(override)
    await db_session.commit()

    async def fake_attach(_db_or_factory, rows):
        for row in rows:
            for statement in row.get("statements") or []:
                if (
                    isinstance(statement, dict)
                    and statement.get("value") == "Q2626422"
                    and not statement.get("value_label")
                ):
                    statement["value_label"] = "Akka"
        return 1

    async def fake_adopt(_items):
        return None

    monkeypatch.setattr(
        "app.pipeline.wikidata_verify_evidence.attach_live_value_labels",
        fake_attach,
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_item_views._adopt_cached_duplicate_qids",
        fake_adopt,
    )

    items = await fetch_merged_wikidata_items(
        db_session,
        run_id,
        approved_only=True,
        source="canonical",
    )
    assert items[0]["ai_verdict"]["overall"] == "full"


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
