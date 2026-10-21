"""Tests for validation-error item listing."""

from __future__ import annotations

import uuid

import pytest

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.wikibase_entity_mapping import WikibaseEntityMapping
from app.pipeline.hmo_item_views import (
    fetch_merged_hmo_items,
    fetch_validation_error_items,
)


@pytest.mark.asyncio
async def test_status_is_on_wiki_when_another_run_uploaded_the_entity(db_session) -> None:
    """Rule W-278: the data-status badge reads the cross-run ledger.

    Run A uploaded the shared entity; run B rebuilt it and must not show
    "new (not uploaded)" for an item that is already live.
    """
    source_uri = "http://ex.org#Aleppo"
    run_a, run_b = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        WikibaseEntityMapping(
            ontology_uri=source_uri,
            entity_kind="instance",
            wikibase_id="Q22",
            run_id=run_a,
            label="Aleppo",
        )
    )
    db_session.add(
        HmoStudioItemCache(
            run_id=run_b,
            input_fingerprint="0" * 64,
            resolved_entities=[
                {
                    "local_id": "QDraft_Aleppo",
                    "labels": {"en": "Aleppo (Syria)"},
                    "descriptions": {"en": "place"},
                    "class_qid": "Q22",
                    "source_uri": source_uri,
                    "claims": [],
                    "deferred_links": [],
                    "skipped_statements": [],
                },
            ],
            entity_count=1,
            deferred_link_count=0,
            skipped_statement_count=0,
        )
    )
    await db_session.commit()

    items = await fetch_merged_hmo_items(db_session, run_b)

    assert len(items) == 1
    assert items[0]["wikibase_id"] == "Q22"
    assert items[0]["status"] == "created"
    assert items[0]["status"] != "would_create"


@pytest.mark.asyncio
async def test_status_stays_new_when_no_run_uploaded_the_entity(db_session) -> None:
    """The other half of the rule: a genuinely new item must still read new."""
    run_id = uuid.uuid4()
    db_session.add(
        HmoStudioItemCache(
            run_id=run_id,
            input_fingerprint="0" * 64,
            resolved_entities=[
                {
                    "local_id": "QDraft_New",
                    "labels": {"en": "Never uploaded"},
                    "descriptions": {"en": "place"},
                    "class_qid": "Q22",
                    "source_uri": "http://ex.org#New",
                    "claims": [],
                    "deferred_links": [],
                    "skipped_statements": [],
                },
            ],
            entity_count=1,
            deferred_link_count=0,
            skipped_statement_count=0,
        )
    )
    await db_session.commit()

    items = await fetch_merged_hmo_items(db_session, run_id)

    assert items[0]["wikibase_id"] is None
    assert items[0]["status"] == "would_create"


@pytest.mark.asyncio
async def test_fetch_validation_error_items(db_session) -> None:
    run_id = uuid.uuid4()
    db_session.add(
        HmoStudioItemCache(
            run_id=run_id,
            input_fingerprint="0" * 64,
            resolved_entities=[
                {
                    "local_id": "QDraft_A",
                    "labels": {"en": "A"},
                    "descriptions": {"en": "a"},
                    "class_qid": "Q1",
                    "source_uri": "http://example.org#A",
                    "claims": [],
                    "deferred_links": [],
                    "skipped_statements": [],
                },
                {
                    "local_id": "QDraft_B",
                    "labels": {"en": "B"},
                    "descriptions": {"en": "b"},
                    "class_qid": "Q2",
                    "source_uri": "http://example.org#B",
                    "claims": [],
                    "deferred_links": [],
                    "skipped_statements": [],
                },
            ],
            entity_count=2,
            deferred_link_count=0,
            skipped_statement_count=0,
            shacl_report={
                "QDraft_A": [{"severity": "Violation", "message": "bad"}],
            },
        )
    )
    await db_session.commit()

    items = await fetch_validation_error_items(db_session, run_id)
    assert len(items) == 1
    assert items[0]["local_id"] == "QDraft_A"
    assert items[0]["has_blocking_shacl"] is True
