"""Tests for validation-error item listing."""

from __future__ import annotations

import uuid

import pytest

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.pipeline.hmo_item_views import fetch_validation_error_items


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
