"""Tests for single-item Wikidata push."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from sqlalchemy import select

from app.models.wikibase_cloud_write import CHANNEL_WIKIDATA_UPLOAD, WikibaseCloudWrite
from app.pipeline import wikidata_upload as wu
from app.services.wikibase_audit import WikibaseAuditContext


@dataclass
class _FakeResult:
    qid: str
    status: str
    message: str
    added_properties: list = field(default_factory=list)


@dataclass
class _Item:
    entity_type: str = "manuscript"
    labels: dict = field(default_factory=lambda: {"en": "MS"})
    statements: list = field(default_factory=list)
    existing_qid: str = ""
    local_id: str = "990001234"


@dataclass
class _Stmt:
    property_id: str
    value: str
    value_type: str = "external-id"


@pytest.mark.asyncio
async def test_push_single_item_records_audit(db_session, sample_run, monkeypatch) -> None:
    monkeypatch.setenv("WIKIDATA_TEST_MODE", "true")

    class _FakeUploader:
        def __init__(self, token, is_test, batch_mode, **_kwargs):
            self._is_our_item_cache = {}

        def _is_our_item(self, qid: str) -> bool:
            return True

        def upload_item(self, item):
            return _FakeResult(qid="Q1", status="success", message="created")

    class _Rec:
        def reconcile_manuscript_by_identifiers(self, nnl_id, shelfmark):
            return None

    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", _FakeUploader)
    monkeypatch.setattr(wu, "_make_reconciler", lambda: _Rec())

    item = _Item(statements=[_Stmt("P3959", "990001234")])
    outcome = await wu.push_single_item(
        db_session, item,
        token="User@Bot:deadbeef",
        audit_ctx=WikibaseAuditContext(
            actor_user_id=sample_run["user_id"],
            channel=CHANNEL_WIKIDATA_UPLOAD,
            run_id=sample_run["run_id"],
        ),
    )
    assert outcome.status == "created"
    row = (
        await db_session.execute(
            select(WikibaseCloudWrite).where(
                WikibaseCloudWrite.run_id == sample_run["run_id"],
            )
        )
    ).scalar_one_or_none()
    assert row is not None
    assert row.target_key == "990001234"
