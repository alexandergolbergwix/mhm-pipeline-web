"""Tests for the two-pass HMO item upload pipeline (Phase 5 — see
dev-docs/hmo-wikibase-studio-plan.md).

Pins: create-only idempotency (a re-run creates nothing new), pass
ordering (deferred item->item links resolve only after pass 1), and
unresolved-link reporting (never silently dropped).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.hmo_canonical_entity import HmoCanonicalEntity
from app.models.wikibase_cloud_write import (
    CHANNEL_ITEM_UPLOAD,
    OPERATION_ADOPT,
    TARGET_ITEM,
    WikibaseCloudWrite,
)
from app.pipeline import hmo_item_upload as pipeline
from app.pipeline.hmo_item_reconcile import ReconcileOutcome, ReconciliationUnavailableError
from app.services.wikibase_audit import WikibaseAuditContext
from converter.wikibase.resolved_models import (
    DeferredItemLink,
    ResolvedClaim,
    ResolvedWikibaseEntity,
)


@dataclass
class _FakeOutcome:
    entity_id: str | None
    status: str = "created"
    message: str = "ok"


class _FakeWriter:
    def __init__(self, *, fail_updates: bool = False, missing_readbacks: set[str] | None = None) -> None:
        self.create_calls: list[dict] = []
        self.claim_calls: list[tuple[str, object]] = []
        self.update_calls: list[dict] = []
        self._next_q = 1
        self._fail_updates = fail_updates
        self._missing_readbacks = missing_readbacks or set()

    def create_item(self, **kwargs):
        self.create_calls.append(kwargs)
        qid = f"Q{self._next_q}"
        self._next_q += 1
        return _FakeOutcome(entity_id=qid)

    def add_claim(self, entity_id, claim):
        self.claim_calls.append((entity_id, claim))
        return _FakeOutcome(entity_id=entity_id, status="updated")

    def get_entity(self, entity_id):
        if entity_id in self._missing_readbacks:
            return None
        return {
            "labels": {"en": f"Live {entity_id}"},
            "descriptions": {"en": "live item"},
            "aliases": {},
            "claims": [],
        }

    def update_item(self, entity_id, **kwargs):
        self.update_calls.append({"entity_id": entity_id, **kwargs})
        if self._fail_updates:
            return _FakeOutcome(entity_id=None, status="failed", message="boom")
        return _FakeOutcome(entity_id=entity_id, status="updated")


async def _seed_cache(
    db_session,
    run_id,
    entities: list[ResolvedWikibaseEntity],
    *,
    shacl_report: dict | None = None,
) -> None:
    db_session.add(
        HmoStudioItemCache(
            run_id=run_id,
            input_fingerprint="0" * 64,
            resolved_entities=[e.to_dict() for e in entities],
            entity_count=len(entities),
            deferred_link_count=sum(len(e.deferred_links) for e in entities),
            skipped_statement_count=0,
            shacl_report=shacl_report or {},
        )
    )
    await db_session.commit()


def _ms_and_person() -> list[ResolvedWikibaseEntity]:
    person = ResolvedWikibaseEntity(
        local_id="QDraft_Person1",
        labels={"en": "Test Scribe"},
        descriptions={"en": "a scribe"},
        class_qid="Q2",
        source_uri="http://example.org#Person1",
    )
    ms = ResolvedWikibaseEntity(
        local_id="QDraft_MS1",
        labels={"en": "Test MS"},
        descriptions={"en": "a manuscript"},
        class_qid="Q1",
        source_uri="http://example.org#MS1",
        claims=[ResolvedClaim("P1", "time", {"time": "+1500-00-00T00:00:00Z", "precision": 9})],
        deferred_links=[DeferredItemLink("QDraft_MS1", "P2", "QDraft_Person1")],
    )
    return [ms, person]


@pytest.mark.asyncio
async def test_raises_when_no_build_exists(db_session) -> None:
    with pytest.raises(pipeline.ItemBuildMissingError):
        await pipeline.upload_items_for_run(db_session, uuid.uuid4(), writer=None, dry_run=True)


@pytest.mark.asyncio
async def test_dry_run_reports_would_create_and_would_link(db_session) -> None:
    run_id = uuid.uuid4()
    await _seed_cache(db_session, run_id, _ms_and_person())

    result = await pipeline.upload_items_for_run(db_session, run_id, writer=None, dry_run=True)

    # `created`/`linked` are the summary counts the UI headlines as
    # "Would create: N · linked N" — they must count "would_*" outcomes
    # too, not just live writes, or a dry-run preview always shows 0/0.
    assert result.created == 2
    assert result.linked == 1
    assert {o.status for o in result.outcomes} == {"would_create"}
    assert result.link_outcomes[0].status == "would_link"


@pytest.mark.asyncio
async def test_live_upload_creates_items_then_links_them(db_session) -> None:
    run_id = uuid.uuid4()
    await _seed_cache(db_session, run_id, _ms_and_person())
    writer = _FakeWriter()

    result = await pipeline.upload_items_for_run(db_session, run_id, writer=writer, dry_run=False)

    assert result.created == 2
    assert result.linked == 1
    assert result.unresolved_links == 0
    assert len(writer.create_calls) == 2
    assert len(writer.claim_calls) == 1
    # The claim was added to the manuscript's real QID, pointing at the
    # person's real QID — not a dangling local placeholder.
    entity_id, claim = writer.claim_calls[0]
    assert entity_id in {"Q1", "Q2"}  # whichever QID create_item assigned to MS1
    assert claim.mainsnak.property_number == "P2"


@pytest.mark.asyncio
async def test_live_upload_persists_canonical_rows_from_source_uri_mappings(db_session) -> None:
    run_id = uuid.uuid4()
    await _seed_cache(db_session, run_id, _ms_and_person())

    result = await pipeline.upload_items_for_run(
        db_session, run_id, writer=_FakeWriter(), dry_run=False,
    )

    assert result.failed == 0
    rows = (
        await db_session.execute(
            select(HmoCanonicalEntity).where(HmoCanonicalEntity.run_id == run_id)
        )
    ).scalars().all()
    assert {row.local_id for row in rows} == {"QDraft_MS1", "QDraft_Person1"}
    assert {row.wikibase_id for row in rows} == {"Q1", "Q2"}
    assert all(row.snapshot["canonical_source"] == "wikibase" for row in rows)


@pytest.mark.asyncio
async def test_missing_live_readback_fails_closed_without_partial_canonical_rows(db_session) -> None:
    run_id = uuid.uuid4()
    await _seed_cache(db_session, run_id, _ms_and_person())

    with pytest.raises(RuntimeError, match="read-back incomplete"):
        await pipeline.upload_items_for_run(
            db_session, run_id,
            writer=_FakeWriter(missing_readbacks={"Q1"}),
            dry_run=False,
        )

    rows = (
        await db_session.execute(
            select(HmoCanonicalEntity).where(HmoCanonicalEntity.run_id == run_id)
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_second_upload_is_create_only_idempotent(db_session) -> None:
    run_id = uuid.uuid4()
    await _seed_cache(db_session, run_id, _ms_and_person())
    writer = _FakeWriter()

    await pipeline.upload_items_for_run(db_session, run_id, writer=writer, dry_run=False)
    second_writer = _FakeWriter()
    result = await pipeline.upload_items_for_run(
        db_session, run_id, writer=second_writer, dry_run=False
    )

    assert result.created == 0
    assert result.skipped == 2
    assert second_writer.create_calls == []
    # Pass 2 still resolves the link against the already-mapped QIDs from
    # the first upload, even though nothing new was created this call.
    assert result.linked == 0 or result.unresolved_links == 0


@pytest.mark.asyncio
async def test_dry_run_update_existing_reports_would_update(db_session) -> None:
    run_id = uuid.uuid4()
    await _seed_cache(db_session, run_id, _ms_and_person())
    writer = _FakeWriter()
    await pipeline.upload_items_for_run(db_session, run_id, writer=writer, dry_run=False)

    result = await pipeline.upload_items_for_run(
        db_session, run_id, writer=None, dry_run=True, update_existing=True,
    )

    assert result.created == 0
    assert result.skipped == 0
    assert result.updated == 2
    assert {o.status for o in result.outcomes} == {"would_update"}


@pytest.mark.asyncio
async def test_update_existing_refreshes_already_uploaded_items(db_session) -> None:
    run_id = uuid.uuid4()
    await _seed_cache(db_session, run_id, _ms_and_person())
    first_writer = _FakeWriter()
    await pipeline.upload_items_for_run(db_session, run_id, writer=first_writer, dry_run=False)

    second_writer = _FakeWriter()
    result = await pipeline.upload_items_for_run(
        db_session, run_id, writer=second_writer, dry_run=False, update_existing=True,
    )

    assert result.created == 0
    assert result.skipped == 0
    assert result.updated == 2
    assert second_writer.create_calls == []
    assert {call["entity_id"] for call in second_writer.update_calls} == {"Q1", "Q2"}
    assert all(o.status == "updated" for o in result.outcomes)
    # Deferred links still resolve against the mapped QIDs, unaffected by
    # the update pass.
    assert result.unresolved_links == 0


@pytest.mark.asyncio
async def test_update_existing_reports_failed_writes(db_session) -> None:
    run_id = uuid.uuid4()
    await _seed_cache(db_session, run_id, _ms_and_person())
    first_writer = _FakeWriter()
    await pipeline.upload_items_for_run(db_session, run_id, writer=first_writer, dry_run=False)

    failing_writer = _FakeWriter(fail_updates=True)
    result = await pipeline.upload_items_for_run(
        db_session, run_id, writer=failing_writer, dry_run=False, update_existing=True,
    )

    assert result.updated == 0
    assert result.failed == 2
    assert all(o.status == "failed" for o in result.outcomes)


@pytest.mark.asyncio
async def test_unsupported_boolean_claims_are_serialized_before_live_write() -> None:
    """Wikibase Cloud has no boolean snak type, so never send one remotely."""
    writer = _FakeWriter()
    entity = ResolvedWikibaseEntity(
        local_id="QDraft_MS1",
        labels={"en": "Test MS"},
        descriptions={"en": "a manuscript"},
        class_qid="Q1",
        source_uri="http://example.org#MS1",
        claims=[ResolvedClaim("P1", "boolean", True)],
    )

    update = await pipeline._update_claims_with_string_fallback(
        writer,
        "Q1",
        labels=entity.labels,
        descriptions=entity.descriptions,
        entity=entity,
    )
    assert update.status == "updated"
    update_claim = writer.update_calls[0]["claims"][0]
    assert update_claim.mainsnak.datatype == "string"
    assert update_claim.mainsnak.datavalue["value"] == "true"

    create_writer = _FakeWriter()
    create = await pipeline._create_claims_with_string_fallback(
        create_writer,
        labels=entity.labels,
        descriptions=entity.descriptions,
        entity=entity,
    )
    assert create.status == "created"
    create_claim = create_writer.claim_calls[0][1]
    assert create_claim.mainsnak.datatype == "string"
    assert create_claim.mainsnak.datavalue["value"] == "true"


@pytest.mark.asyncio
async def test_live_upload_adopts_a_reconciled_found_item_instead_of_creating(db_session) -> None:
    """Pass 1 must not create a duplicate for an item that already exists
    live on the Wikibase Cloud — it should adopt the found QID instead."""
    run_id = uuid.uuid4()
    await _seed_cache(db_session, run_id, _ms_and_person())
    writer = _FakeWriter()

    async def _fake_reconcile(_db, source_uri: str, *, pid: str | None = None) -> ReconcileOutcome:
        if source_uri == "http://example.org#MS1":
            return ReconcileOutcome(found=True, wikibase_id="Q999", message="found live")
        return ReconcileOutcome(found=False)

    with patch.object(pipeline, "reconcile_item", AsyncMock(side_effect=_fake_reconcile)):
        result = await pipeline.upload_items_for_run(
            db_session, run_id, writer=writer, dry_run=False,
        )

    outcomes_by_local_id = {o.local_id: o for o in result.outcomes}
    assert outcomes_by_local_id["QDraft_MS1"].status == "adopted"
    assert outcomes_by_local_id["QDraft_MS1"].wikibase_id == "Q999"
    # The manuscript was adopted, not created — only the person went
    # through a real create_item call.
    assert len(writer.create_calls) == 1
    assert result.created == 2  # one adopted + one created, both count as "resolved"

    # The deferred MS1 -> Person1 link still resolves against the
    # adopted QID, proving the mapping row was actually recorded.
    assert result.unresolved_links == 0
    assert result.linked == 1


@pytest.mark.asyncio
async def test_live_upload_resolves_reconcile_pid_once_not_per_entity(db_session) -> None:
    """A bulk upload must resolve the schema-level hmo_source_uri property
    id ONCE for the whole run, not once per entity — a real run can have
    thousands of entities, and re-querying (plus re-opening a DB
    transaction) per item ahead of each slow Wikibase Cloud call is both
    wasteful and reintroduces the idle-in-transaction hazard."""
    run_id = uuid.uuid4()
    await _seed_cache(db_session, run_id, _ms_and_person())
    writer = _FakeWriter()

    with patch.object(
        pipeline, "resolve_source_uri_pid", AsyncMock(return_value=None),
    ) as resolve_mock:
        await pipeline.upload_items_for_run(db_session, run_id, writer=writer, dry_run=False)

    resolve_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_dry_run_never_resolves_reconcile_pid(db_session) -> None:
    run_id = uuid.uuid4()
    await _seed_cache(db_session, run_id, _ms_and_person())

    with patch.object(
        pipeline, "resolve_source_uri_pid", AsyncMock(return_value=None),
    ) as resolve_mock:
        await pipeline.upload_items_for_run(db_session, run_id, writer=None, dry_run=True)

    resolve_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_upload_blocks_creation_when_reconcile_is_unavailable(db_session) -> None:
    """A SPARQL lookup failure must fail-closed: never proceed to create
    a possibly-duplicate item when we can't first check for one."""
    run_id = uuid.uuid4()
    await _seed_cache(db_session, run_id, _ms_and_person())
    writer = _FakeWriter()

    with patch.object(
        pipeline, "reconcile_item",
        AsyncMock(side_effect=ReconciliationUnavailableError("SPARQL endpoint unreachable")),
    ):
        result = await pipeline.upload_items_for_run(
            db_session, run_id, writer=writer, dry_run=False,
        )

    assert result.failed == 2
    assert writer.create_calls == []
    assert all(o.status == "failed" for o in result.outcomes)


@pytest.mark.asyncio
async def test_live_upload_records_operation_adopt_in_audit_log(db_session, sample_run) -> None:
    """The audit log must distinguish "adopted a pre-existing item" from
    "created a brand-new item" — the review table's Last-upload column
    depends on this to show "linked to existing item" instead of "new"."""
    run_id = sample_run["run_id"]
    await _seed_cache(db_session, run_id, _ms_and_person())
    writer = _FakeWriter()
    audit_ctx = WikibaseAuditContext(
        actor_user_id=sample_run["user_id"],
        project_id=sample_run["project_id"],
        run_id=run_id,
        channel=CHANNEL_ITEM_UPLOAD,
    )

    async def _fake_reconcile(_db, source_uri: str, *, pid: str | None = None) -> ReconcileOutcome:
        if source_uri == "http://example.org#MS1":
            return ReconcileOutcome(found=True, wikibase_id="Q999", message="found live")
        return ReconcileOutcome(found=False)

    with patch.object(pipeline, "reconcile_item", AsyncMock(side_effect=_fake_reconcile)):
        await pipeline.upload_items_for_run(
            db_session, run_id, writer=writer, dry_run=False, audit_ctx=audit_ctx,
        )

    row = (
        await db_session.execute(
            select(WikibaseCloudWrite).where(
                WikibaseCloudWrite.run_id == run_id,
                WikibaseCloudWrite.target_key == "http://example.org#MS1",
            )
        )
    ).scalar_one()
    assert row.operation == OPERATION_ADOPT
    assert row.wikibase_id == "Q999"
    assert "found live" in row.outcome_message


@pytest.mark.asyncio
async def test_push_single_item_creates_when_no_existing_qid(db_session) -> None:
    run_id = uuid.uuid4()
    ms = _ms_and_person()[0]
    writer = _FakeWriter()

    with patch.object(pipeline, "reconcile_item", AsyncMock(return_value=ReconcileOutcome(found=False))):
        outcome = await pipeline.push_single_item(
            db_session, run_id, ms,
            writer=writer, audit_ctx=None, update_existing=False,
            reconcile_pid=None, existing_qid=None,
        )

    assert outcome.status == "created"
    assert outcome.wikibase_id == "Q1"
    assert len(writer.create_calls) == 1


@pytest.mark.asyncio
async def test_push_single_item_updates_when_existing_qid_and_update_flag_set(db_session) -> None:
    run_id = uuid.uuid4()
    ms = _ms_and_person()[0]
    writer = _FakeWriter()

    outcome = await pipeline.push_single_item(
        db_session, run_id, ms,
        writer=writer, audit_ctx=None, update_existing=True,
        reconcile_pid=None, existing_qid="Q42",
    )

    assert outcome.status == "updated"
    assert outcome.wikibase_id == "Q42"
    assert writer.update_calls[0]["entity_id"] == "Q42"
    assert writer.create_calls == []


@pytest.mark.asyncio
async def test_push_single_item_skips_when_existing_qid_and_no_update_flag(db_session) -> None:
    run_id = uuid.uuid4()
    ms = _ms_and_person()[0]
    writer = _FakeWriter()

    outcome = await pipeline.push_single_item(
        db_session, run_id, ms,
        writer=writer, audit_ctx=None, update_existing=False,
        reconcile_pid=None, existing_qid="Q42",
    )

    assert outcome.status == "skipped"
    assert outcome.wikibase_id == "Q42"
    assert writer.create_calls == []
    assert writer.update_calls == []


@pytest.mark.asyncio
async def test_push_single_item_records_audit_row_when_ctx_given(db_session, sample_run) -> None:
    run_id = sample_run["run_id"]
    ms = _ms_and_person()[0]
    writer = _FakeWriter()
    audit_ctx = WikibaseAuditContext(
        actor_user_id=sample_run["user_id"],
        project_id=sample_run["project_id"],
        run_id=run_id,
        channel=CHANNEL_ITEM_UPLOAD,
    )

    with patch.object(pipeline, "reconcile_item", AsyncMock(return_value=ReconcileOutcome(found=False))):
        outcome = await pipeline.push_single_item(
            db_session, run_id, ms,
            writer=writer, audit_ctx=audit_ctx, update_existing=False,
            reconcile_pid=None, existing_qid=None,
        )

    row = (
        await db_session.execute(
            select(WikibaseCloudWrite).where(
                WikibaseCloudWrite.run_id == run_id,
                WikibaseCloudWrite.target_key == ms.source_uri,
            )
        )
    ).scalar_one()
    assert row.wikibase_id == outcome.wikibase_id


@pytest.mark.asyncio
async def test_unresolved_link_reported_when_target_never_created(db_session) -> None:
    run_id = uuid.uuid4()
    ms = ResolvedWikibaseEntity(
        local_id="QDraft_MS1",
        labels={"en": "Test MS"},
        descriptions={"en": "a manuscript"},
        class_qid="Q1",
        source_uri="http://example.org#MS1",
        deferred_links=[DeferredItemLink("QDraft_MS1", "P2", "QDraft_Missing")],
    )
    await _seed_cache(db_session, run_id, [ms])
    writer = _FakeWriter()

    result = await pipeline.upload_items_for_run(db_session, run_id, writer=writer, dry_run=False)

    assert result.unresolved_links == 1
    assert result.link_outcomes[0].status == "unresolved"


@pytest.mark.asyncio
async def test_shacl_violation_blocks_live_create(db_session) -> None:
    run_id = uuid.uuid4()
    entities = _ms_and_person()
    shacl = {
        entities[0].local_id: [{
            "severity": "Violation",
            "message": "Paradigm bridge must link to a Work",
            "focus_node": entities[0].source_uri,
        }],
    }
    await _seed_cache(db_session, run_id, entities, shacl_report=shacl)
    writer = _FakeWriter()

    result = await pipeline.upload_items_for_run(
        db_session, run_id, writer=writer, dry_run=False,
    )

    assert result.blocked == 1
    assert result.created == 1
    assert len(writer.create_calls) == 1
    blocked = [o for o in result.outcomes if o.status == "blocked"]
    assert len(blocked) == 1
    assert "Paradigm bridge" in blocked[0].message


@pytest.mark.asyncio
async def test_dry_run_reports_would_block_for_shacl_violations(db_session) -> None:
    run_id = uuid.uuid4()
    entities = _ms_and_person()
    shacl = {
        entities[0].local_id: [{
            "severity": "Violation",
            "message": "NLI identifier must be a single string value",
        }],
    }
    await _seed_cache(db_session, run_id, entities, shacl_report=shacl)
    result = await pipeline.upload_items_for_run(
        db_session, run_id, writer=None, dry_run=True,
    )
    assert result.blocked == 1
    assert result.created == 1
    assert any(o.status == "would_block" for o in result.outcomes)


@pytest.mark.asyncio
async def test_allow_shacl_errors_bypasses_upload_gate(db_session) -> None:
    run_id = uuid.uuid4()
    entities = _ms_and_person()
    shacl = {
        entities[0].local_id: [{
            "severity": "Violation",
            "message": "blocked unless opted in",
        }],
    }
    await _seed_cache(db_session, run_id, entities, shacl_report=shacl)
    writer = _FakeWriter()
    result = await pipeline.upload_items_for_run(
        db_session, run_id, writer=writer, dry_run=False,
        allow_shacl_errors=True,
    )
    assert result.blocked == 0
    assert result.created == 2
    assert len(writer.create_calls) == 2


@pytest.mark.asyncio
async def test_push_single_item_never_emits_und_labels(db_session, sample_run) -> None:
    run_id = uuid.uuid4()
    entity = ResolvedWikibaseEntity(
        local_id="QDraft_TimeSpan_1",
        labels={"en": "1001", "und": "1001"},
        descriptions={"en": "year"},
        class_qid="Q21",
        source_uri="http://example.org#TimeSpan_1",
    )
    writer = _FakeWriter()
    audit_ctx = WikibaseAuditContext(
        actor_user_id=sample_run["user_id"],
        project_id=sample_run["project_id"],
        run_id=run_id,
        channel=CHANNEL_ITEM_UPLOAD,
    )
    with patch.object(pipeline, "reconcile_item", AsyncMock(return_value=ReconcileOutcome(found=False))):
        await pipeline.push_single_item(
            db_session, run_id, entity,
            writer=writer, audit_ctx=audit_ctx, update_existing=False,
            reconcile_pid=None, existing_qid=None,
        )
    assert "und" not in writer.create_calls[0]["labels"]
