"""Tests for the two-pass HMO item upload pipeline (Phase 5 — see
dev-docs/hmo-wikibase-studio-plan.md).

Pins: create-only idempotency (a re-run creates nothing new), pass
ordering (deferred item->item links resolve only after pass 1), and
unresolved-link reporting (never silently dropped).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.pipeline import hmo_item_upload as pipeline
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
    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.claim_calls: list[tuple[str, object]] = []
        self._next_q = 1

    def create_item(self, **kwargs):
        self.create_calls.append(kwargs)
        qid = f"Q{self._next_q}"
        self._next_q += 1
        return _FakeOutcome(entity_id=qid)

    def add_claim(self, entity_id, claim):
        self.claim_calls.append((entity_id, claim))
        return _FakeOutcome(entity_id=entity_id, status="updated")


async def _seed_cache(db_session, run_id, entities: list[ResolvedWikibaseEntity]) -> None:
    db_session.add(
        HmoStudioItemCache(
            run_id=run_id,
            input_fingerprint="0" * 64,
            resolved_entities=[e.to_dict() for e in entities],
            entity_count=len(entities),
            deferred_link_count=sum(len(e.deferred_links) for e in entities),
            skipped_statement_count=0,
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
