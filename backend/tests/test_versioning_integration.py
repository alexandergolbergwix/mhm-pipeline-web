"""End-to-end versioning tests through the live mutation surfaces.

These tests pin the contract that every meaningful curator mutation
(MARC upload, NER approval, authority approval, revert, backfill)
emits the right :class:`app.models.event.ProjectEvent` rows. Where
``test_versioning_core.py`` exercises the versioning helper directly,
this file drives the real FastAPI routers and asserts what landed in
the event log afterwards.

Five contracts pinned here:

1. ``POST /api/projects/{id}/runs`` with a 3-record JSON payload
   emits three ``marc_record`` create-events keyed by
   ``"{run_id}:{control_number}"``.
2. ``PATCH /api/runs/{id}/extraction/entities/{eid}`` on a seeded
   ExtractionApproval (no prior events) emits a single ``op=create``
   event whose ``state.approved`` reflects the patched value.
3. ``PATCH /api/runs/{id}/matches/{mid}`` on a seeded AuthorityMatch
   emits at least one event with ``state.approved == True``.
4. The revert round-trip — two PATCHes on an extraction entity
   followed by ``POST /api/projects/{id}/history/revert`` to rev 1 —
   ends with ``new_rev_no == 3`` and the read-model row matching
   rev 1 state (approved=True, override_role=None).
5. ``scripts.backfill_versioning.backfill_extraction_approvals`` is
   idempotent: running it twice on the same pre-seeded rows produces
   the same set of events (no duplicates).
"""

from __future__ import annotations

import io
import json
import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select


# ── Helpers ────────────────────────────────────────────────────────────


def _encode_entity_id(
    *, control_number: str, source: str, text: str, start: int, end: int,
) -> str:
    """Mirror ``app.routers.extraction._entity_id`` so we can build a
    valid route param for the seeded ExtractionApproval row without
    importing the private helper."""
    import base64

    payload = json.dumps(
        [control_number, source, text, int(start), int(end)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


@pytest_asyncio.fixture
async def seeded_extraction_approval(db_session, sample_run):
    """Drop one ExtractionApproval row into the DB directly — no
    events emitted because nothing routes through ``apply_event``."""
    from app.models.extraction_approval import ExtractionApproval

    ext = ExtractionApproval(
        run_id=sample_run["run_id"],
        control_number=sample_run["control_number"],
        source="person_ner",
        text="Maimonides",
        start=0,
        end=10,
        type="PERSON",
        role="author",
        confidence=0.85,
        model_confidence=0.92,
        approved=False,
    )
    db_session.add(ext)
    await db_session.commit()
    return {**sample_run, "approval_id": ext.id, "approval": ext}


# ── 1. MARC upload emits one create-event per record ──────────────────


class TestMarcUploadEmitsCreatePerRecord:
    @pytest.mark.asyncio
    async def test_marc_upload_emits_create_event_per_record(
        self, db_session, auth_user,
    ) -> None:
        from app.models.event import (
            ENTITY_TYPE_MARC_RECORD,
            OP_CREATE,
            ProjectEvent,
        )
        from app.models.project import PROJECT_ROLE_OWNER, Membership, Project

        user, client = auth_user

        # Make a project the user owns so ``require_editor`` resolves.
        project = Project(owner_id=user.id, name="Versioning project", description="")
        db_session.add(project)
        await db_session.flush()
        db_session.add(
            Membership(project_id=project.id, user_id=user.id, role=PROJECT_ROLE_OWNER),
        )
        await db_session.commit()

        # Synthetic 3-record JSON. No authors/contributors/subjects so
        # ``execute_run``'s entity-extraction + authority-matcher path
        # produces zero matches — keeps the test purely about MARC
        # ingestion + versioning.
        records = [
            {"_control_number": "MS_1", "title": "Codex Aleph"},
            {"_control_number": "MS_2", "title": "Codex Bet"},
            {"_control_number": "MS_3", "title": "Codex Gimel"},
        ]
        payload = json.dumps(records).encode("utf-8")
        files = {"file": ("synthetic.json", io.BytesIO(payload), "application/json")}

        r = await client.post(
            f"/api/projects/{project.id}/runs",
            files=files,
        )
        assert r.status_code == 201, r.text
        run_id = r.json()["id"]

        # Pull every marc_record event for this project and verify the
        # shape: three events, all op=create, entity_id of the form
        # "{run_id}:MS_N", distinct.
        events = (
            await db_session.execute(
                select(ProjectEvent).where(
                    ProjectEvent.project_id == project.id,
                    ProjectEvent.entity_type == ENTITY_TYPE_MARC_RECORD,
                )
            )
        ).scalars().all()

        assert len(events) == 3, (
            f"expected 3 marc_record events, got {len(events)}: "
            f"{[(e.entity_id, e.op) for e in events]}"
        )
        for ev in events:
            assert ev.op == OP_CREATE
            assert ev.entity_id is not None
            assert ev.entity_id.startswith(f"{run_id}:")

        entity_ids = {ev.entity_id for ev in events}
        assert entity_ids == {
            f"{run_id}:MS_1", f"{run_id}:MS_2", f"{run_id}:MS_3",
        }


# ── 2. NER PATCH emits extraction_entity event ────────────────────────


class TestNerPatchEmitsExtractionEvent:
    @pytest.mark.asyncio
    async def test_ner_patch_emits_extraction_entity_patch_event(
        self, db_session, seeded_extraction_approval,
    ) -> None:
        from app.models.event import (
            ENTITY_TYPE_EXTRACTION_ENTITY,
            OP_CREATE,
            ProjectEvent,
        )

        client = seeded_extraction_approval["client"]
        run_id = seeded_extraction_approval["run_id"]
        approval = seeded_extraction_approval["approval"]
        approval_id = seeded_extraction_approval["approval_id"]

        entity_route_id = _encode_entity_id(
            control_number=approval.control_number,
            source=approval.source,
            text=approval.text,
            start=approval.start,
            end=approval.end,
        )

        r = await client.patch(
            f"/api/runs/{run_id}/extraction/entities/{entity_route_id}",
            json={"approved": True},
        )
        assert r.status_code == 200, r.text

        # The seeded row had no prior events, so first PATCH must
        # surface as op=create with the full state snapshot.
        events = (
            await db_session.execute(
                select(ProjectEvent).where(
                    ProjectEvent.entity_type == ENTITY_TYPE_EXTRACTION_ENTITY,
                    ProjectEvent.entity_id == str(approval_id),
                )
            )
        ).scalars().all()

        assert len(events) == 1, (
            f"expected exactly one extraction_entity event for the seeded row, "
            f"got {len(events)}: {[(e.op, e.rev_no) for e in events]}"
        )
        ev = events[0]
        assert ev.op == OP_CREATE
        assert ev.rev_no == 1
        assert ev.state is not None
        assert ev.state.get("approved") is True


# ── 3. Authority PATCH emits authority_match event ────────────────────


class TestAuthorityPatchEmitsAuthorityEvent:
    @pytest.mark.asyncio
    async def test_authority_patch_emits_authority_match_event(
        self, db_session, sample_run,
    ) -> None:
        from app.models.event import (
            ENTITY_TYPE_AUTHORITY_MATCH,
            OP_CREATE,
            OP_PATCH,
            ProjectEvent,
        )

        client = sample_run["client"]
        run_id = sample_run["run_id"]
        match_id = sample_run["match_id"]

        r = await client.patch(
            f"/api/runs/{run_id}/matches/{match_id}",
            json={"approved": True},
        )
        assert r.status_code == 200, r.text
        assert r.json()["approved"] is True

        events = (
            await db_session.execute(
                select(ProjectEvent).where(
                    ProjectEvent.entity_type == ENTITY_TYPE_AUTHORITY_MATCH,
                    ProjectEvent.entity_id == str(match_id),
                )
            )
        ).scalars().all()

        assert len(events) >= 1, (
            "expected at least one authority_match event after PATCH"
        )
        ops = {ev.op for ev in events}
        assert ops & {OP_CREATE, OP_PATCH}, (
            f"expected op create or patch, got ops={ops}"
        )

        # The CREATE event carries full state — find it and assert
        # approved propagated. (Subsequent PATCHes carry only a diff.)
        create_evs = [ev for ev in events if ev.op == OP_CREATE]
        assert create_evs, "expected one create event in the history"
        latest_create = create_evs[-1]
        assert latest_create.state is not None
        assert latest_create.state.get("approved") is True


# ── 4. Revert round-trip on an ExtractionApproval ─────────────────────


class TestRevertExtractionEntityRoundTrip:
    @pytest.mark.asyncio
    async def test_revert_extraction_entity_round_trip(
        self, db_session, seeded_extraction_approval,
    ) -> None:
        from app.models.extraction_approval import ExtractionApproval

        client = seeded_extraction_approval["client"]
        run_id = seeded_extraction_approval["run_id"]
        project_id = seeded_extraction_approval["project_id"]
        approval = seeded_extraction_approval["approval"]
        approval_id = seeded_extraction_approval["approval_id"]

        entity_route_id = _encode_entity_id(
            control_number=approval.control_number,
            source=approval.source,
            text=approval.text,
            start=approval.start,
            end=approval.end,
        )

        # Rev 1: first PATCH (approved=True) — emits op=create.
        r1 = await client.patch(
            f"/api/runs/{run_id}/extraction/entities/{entity_route_id}",
            json={"approved": True},
        )
        assert r1.status_code == 200, r1.text

        # Rev 2: second PATCH (override_role="OWNER") — emits op=patch.
        r2 = await client.patch(
            f"/api/runs/{run_id}/extraction/entities/{entity_route_id}",
            json={"override_role": "OWNER"},
        )
        assert r2.status_code == 200, r2.text

        # Revert to rev 1.
        revert_body: dict[str, Any] = {
            "entity_type": "extraction_entity",
            "entity_id":   str(approval_id),
            "target_rev":  1,
            "message":     "wrong",
        }
        rr = await client.post(
            f"/api/projects/{project_id}/history/revert",
            json=revert_body,
        )
        assert rr.status_code == 200, rr.text
        body = rr.json()
        assert body["ok"] is True
        assert body["new_rev_no"] == 3, (
            f"expected the revert event to land at rev 3, got {body}"
        )

        # Re-fetch the read-model row and assert it reflects rev 1's
        # state: approved=True, override_role=None. ``expire_all`` is
        # synchronous on the AsyncSession's underlying sync session;
        # call it on ``.sync_session`` to drop identity-map cache.
        db_session.expire_all()
        row = (
            await db_session.execute(
                select(ExtractionApproval).where(
                    ExtractionApproval.id == approval_id,
                )
            )
        ).scalar_one()
        assert row.approved is True
        assert row.override_role is None


# ── 5. Backfill is idempotent ─────────────────────────────────────────


class TestBackfillIdempotent:
    @pytest.mark.asyncio
    async def test_backfill_idempotent(
        self, db_session, sample_run,
    ) -> None:
        from app.models.event import (
            ENTITY_TYPE_EXTRACTION_ENTITY,
            ProjectEvent,
        )
        from app.models.extraction_approval import ExtractionApproval
        from scripts.backfill_versioning import backfill_extraction_approvals

        # Pre-seed two ExtractionApproval rows directly — no events yet.
        ext_a = ExtractionApproval(
            run_id=sample_run["run_id"],
            control_number=sample_run["control_number"],
            source="person_ner",
            text="Maimonides",
            start=0, end=10,
            type="PERSON", role="author",
            approved=False,
        )
        ext_b = ExtractionApproval(
            run_id=sample_run["run_id"],
            control_number=sample_run["control_number"],
            source="provenance_ner",
            text="Cairo Geniza",
            start=11, end=23,
            type="COLLECTION", role=None,
            approved=False,
        )
        db_session.add_all([ext_a, ext_b])
        await db_session.commit()

        seeded_ids = {str(ext_a.id), str(ext_b.id)}

        # First call — two OP_CREATE events.
        created_first = await backfill_extraction_approvals(db_session)
        assert created_first == 2

        events_first = (
            await db_session.execute(
                select(ProjectEvent).where(
                    ProjectEvent.entity_type == ENTITY_TYPE_EXTRACTION_ENTITY,
                    ProjectEvent.entity_id.in_(seeded_ids),
                )
            )
        ).scalars().all()
        assert len(events_first) == 2, (
            f"first backfill should create exactly two events, got {len(events_first)}"
        )

        # Second call — must be a no-op (idempotent).
        created_second = await backfill_extraction_approvals(db_session)
        assert created_second == 0

        events_second = (
            await db_session.execute(
                select(ProjectEvent).where(
                    ProjectEvent.entity_type == ENTITY_TYPE_EXTRACTION_ENTITY,
                    ProjectEvent.entity_id.in_(seeded_ids),
                )
            )
        ).scalars().all()
        assert len(events_second) == 2, (
            f"second backfill must not duplicate events, got {len(events_second)}"
        )
