"""Tests for the entity-scoped versioning core.

The versioning core (``app.versioning.core``) is the canonical mutation
entry point for every entity-scoped curator decision. It writes
append-only ``project_events`` rows; the current state of any entity is
the fold of its event stream. These tests pin the public contract:

* ``apply_event`` validates the closed sets of ``entity_type`` / ``op``,
  auto-computes the JSON-Patch when ``op=patch``, increments ``rev_no``
  monotonically, threads ``parent_event_id`` correctly, and writes an
  auto-snapshot every 50 non-snapshot events.
* ``current_state`` / ``state_at_rev`` fold create + patches correctly
  and prefer the latest snapshot as the replay base.
* ``diff_revs`` returns a forward JSON-Patch plus the folded before/after
  states.
* ``revert_to_rev`` writes an ``OP_REVERT`` event whose ``patch`` (the
  inverse) re-applies the target revision's state.
* ``event_timeline`` returns newest-first and paginates via ``before_rev``.

The versioning core is read-model-agnostic — these tests do NOT seed
``ExtractionApproval`` / ``AuthorityMatch`` rows. ``project_id`` comes
from the shared ``sample_run`` fixture; ``actor_id`` is a random UUID
(``apply_event`` does not validate it against a real user).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.event import (
    ENTITY_TYPE_EXTRACTION_ENTITY,
    OP_CREATE,
    OP_PATCH,
    OP_REVERT,
    OP_SNAPSHOT,
    ProjectEvent,
)
from app.versioning.core import (
    apply_event,
    current_state,
    diff_revs,
    event_timeline,
    revert_to_rev,
    state_at_rev,
)


def _new_entity_id() -> str:
    return str(uuid.uuid4())


class TestApplyEventCreate:
    @pytest.mark.asyncio
    async def test_apply_event_create_sets_rev_no_1_and_state_only(
        self, db_session, sample_run,
    ) -> None:
        entity_id = _new_entity_id()
        actor_id = uuid.uuid4()
        initial_state = {"text": "Maimonides", "role": "author", "approved": False}

        event = await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_CREATE,
            new_state=initial_state,
            actor_id=actor_id,
        )
        await db_session.commit()

        assert event.rev_no == 1
        assert event.op == OP_CREATE
        assert event.parent_event_id is None
        assert event.state == initial_state
        assert event.patch is None
        assert event.entity_type == ENTITY_TYPE_EXTRACTION_ENTITY
        assert event.entity_id == entity_id
        assert event.type == f"{ENTITY_TYPE_EXTRACTION_ENTITY}.{OP_CREATE}"


class TestApplyEventPatch:
    @pytest.mark.asyncio
    async def test_apply_event_patch_auto_computes_jsonpatch(
        self, db_session, sample_run,
    ) -> None:
        entity_id = _new_entity_id()
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_CREATE,
            new_state={"text": "Maimonides", "approved": False},
        )

        patch_event = await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_PATCH,
            new_state={"text": "Maimonides", "approved": True},
        )
        await db_session.commit()

        assert patch_event.op == OP_PATCH
        assert patch_event.state is None
        assert patch_event.patch is not None
        # The auto-computed patch must flip ``approved`` False → True.
        ops = patch_event.patch
        assert any(
            op.get("op") == "replace"
            and op.get("path") == "/approved"
            and op.get("value") is True
            for op in ops
        )

    @pytest.mark.asyncio
    async def test_apply_event_patch_increments_rev_no(
        self, db_session, sample_run,
    ) -> None:
        entity_id = _new_entity_id()
        create_ev = await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_CREATE,
            new_state={"v": 1},
        )
        patch1 = await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_PATCH,
            new_state={"v": 2},
        )
        patch2 = await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_PATCH,
            new_state={"v": 3},
        )
        await db_session.commit()

        assert create_ev.rev_no == 1
        assert patch1.rev_no == 2
        assert patch2.rev_no == 3


class TestApplyEventSnapshotAuto:
    @pytest.mark.asyncio
    async def test_apply_event_50th_event_writes_followup_snapshot(
        self, db_session, sample_run,
    ) -> None:
        entity_id = _new_entity_id()
        # First event: create at rev 1.
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_CREATE,
            new_state={"counter": 0},
        )
        # Now 49 patches → rev_no climbs 2 … 50. The 50th event triggers
        # the auto-snapshot, which is written at rev_no 51.
        last_patch = None
        for i in range(1, 50):
            last_patch = await apply_event(
                db_session,
                project_id=sample_run["project_id"],
                entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
                entity_id=entity_id,
                op=OP_PATCH,
                new_state={"counter": i},
            )
        await db_session.commit()

        assert last_patch is not None
        assert last_patch.rev_no == 50

        # The auto-snapshot row sits at rev_no = 51 with op=snapshot.
        stmt = (
            select(ProjectEvent)
            .where(
                ProjectEvent.entity_type == ENTITY_TYPE_EXTRACTION_ENTITY,
                ProjectEvent.entity_id == entity_id,
                ProjectEvent.op == OP_SNAPSHOT,
            )
            .order_by(ProjectEvent.rev_no.asc())
        )
        snapshots = (await db_session.execute(stmt)).scalars().all()
        assert len(snapshots) == 1
        snap = snapshots[0]
        assert snap.rev_no == 51
        # Snapshot state must match the 50th patch (counter=49).
        assert snap.state == {"counter": 49}


class TestApplyEventValidation:
    @pytest.mark.asyncio
    async def test_apply_event_validates_entity_type_closed_set(
        self, db_session, sample_run,
    ) -> None:
        with pytest.raises(ValueError, match="entity_type"):
            await apply_event(
                db_session,
                project_id=sample_run["project_id"],
                entity_type="not_a_real_type",
                entity_id=_new_entity_id(),
                op=OP_CREATE,
                new_state={"v": 1},
            )

    @pytest.mark.asyncio
    async def test_apply_event_validates_op_closed_set(
        self, db_session, sample_run,
    ) -> None:
        with pytest.raises(ValueError, match="op"):
            await apply_event(
                db_session,
                project_id=sample_run["project_id"],
                entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
                entity_id=_new_entity_id(),
                op="merge",  # not in ALL_OPS
                new_state={"v": 1},
            )


class TestApplyEventParentChain:
    @pytest.mark.asyncio
    async def test_apply_event_create_then_patch_chains_parent_event_id(
        self, db_session, sample_run,
    ) -> None:
        entity_id = _new_entity_id()
        create_ev = await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_CREATE,
            new_state={"approved": False},
        )
        patch_ev = await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_PATCH,
            new_state={"approved": True},
        )
        await db_session.commit()

        assert create_ev.parent_event_id is None
        assert patch_ev.parent_event_id == create_ev.id


class TestCurrentState:
    @pytest.mark.asyncio
    async def test_current_state_replays_from_create_through_patches(
        self, db_session, sample_run,
    ) -> None:
        entity_id = _new_entity_id()
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_CREATE,
            new_state={"text": "Maimonides", "approved": False, "role": "author"},
        )
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_PATCH,
            new_state={"text": "Maimonides", "approved": True, "role": "author"},
        )
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_PATCH,
            new_state={"text": "Moses Maimonides", "approved": True, "role": "author"},
        )
        await db_session.commit()

        state = await current_state(
            db_session, ENTITY_TYPE_EXTRACTION_ENTITY, entity_id,
        )
        assert state == {
            "text":     "Moses Maimonides",
            "approved": True,
            "role":     "author",
        }

    @pytest.mark.asyncio
    async def test_current_state_uses_latest_snapshot_when_present(
        self, db_session, sample_run,
    ) -> None:
        entity_id = _new_entity_id()
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_CREATE,
            new_state={"v": 1},
        )
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_PATCH,
            new_state={"v": 2},
        )
        # Explicit snapshot anchors the replay base at rev 3.
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_SNAPSHOT,
            new_state={"v": 2, "snapshot_marker": True},
        )
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_PATCH,
            new_state={"v": 3, "snapshot_marker": True},
        )
        await db_session.commit()

        state = await current_state(
            db_session, ENTITY_TYPE_EXTRACTION_ENTITY, entity_id,
        )
        # The "snapshot_marker" key only existed FROM the snapshot
        # onwards. If the replay correctly anchors on the snapshot
        # rather than re-folding from the create, both keys survive.
        assert state == {"v": 3, "snapshot_marker": True}


class TestStateAtRev:
    @pytest.mark.asyncio
    async def test_state_at_rev_returns_intermediate_state(
        self, db_session, sample_run,
    ) -> None:
        entity_id = _new_entity_id()
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_CREATE,
            new_state={"v": 1},
        )
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_PATCH,
            new_state={"v": 2},
        )
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_PATCH,
            new_state={"v": 3},
        )
        await db_session.commit()

        state_at_2 = await state_at_rev(
            db_session, ENTITY_TYPE_EXTRACTION_ENTITY, entity_id, 2,
        )
        assert state_at_2 == {"v": 2}
        state_at_1 = await state_at_rev(
            db_session, ENTITY_TYPE_EXTRACTION_ENTITY, entity_id, 1,
        )
        assert state_at_1 == {"v": 1}

    @pytest.mark.asyncio
    async def test_state_at_rev_returns_none_for_unknown_entity(
        self, db_session,
    ) -> None:
        state = await state_at_rev(
            db_session, ENTITY_TYPE_EXTRACTION_ENTITY, _new_entity_id(), 1,
        )
        assert state is None


class TestDiffRevs:
    @pytest.mark.asyncio
    async def test_diff_revs_returns_patch_before_after(
        self, db_session, sample_run,
    ) -> None:
        entity_id = _new_entity_id()
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_CREATE,
            new_state={"a": 1},
        )
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_PATCH,
            new_state={"a": 2},
        )
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_PATCH,
            new_state={"a": 3, "b": "x"},
        )
        await db_session.commit()

        result = await diff_revs(
            db_session, ENTITY_TYPE_EXTRACTION_ENTITY, entity_id, 1, 3,
        )

        assert result["before"] == {"a": 1}
        assert result["after"] == {"a": 3, "b": "x"}
        assert isinstance(result["patch"], list)

        # Apply the returned patch to before and verify we reach after.
        import jsonpatch
        applied = jsonpatch.JsonPatch(result["patch"]).apply({"a": 1})
        assert applied == {"a": 3, "b": "x"}


class TestRevertToRev:
    @pytest.mark.asyncio
    async def test_revert_to_rev_writes_revert_op_with_inverse_patch(
        self, db_session, sample_run,
    ) -> None:
        entity_id = _new_entity_id()
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_CREATE,
            new_state={"v": 1, "approved": False},
        )
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_PATCH,
            new_state={"v": 2, "approved": True},
        )
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_PATCH,
            new_state={"v": 3, "approved": True},
        )

        revert_ev = await revert_to_rev(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            target_rev=2,
        )
        await db_session.commit()

        assert revert_ev.op == OP_REVERT
        assert revert_ev.rev_no == 4
        # Revert events carry both the full target state AND the
        # inverse patch (current → target).
        assert revert_ev.state == {"v": 2, "approved": True}
        assert revert_ev.patch is not None

        # Re-applying the inverse patch to the pre-revert state must
        # yield the target state.
        import jsonpatch
        applied = jsonpatch.JsonPatch(revert_ev.patch).apply(
            {"v": 3, "approved": True}
        )
        assert applied == {"v": 2, "approved": True}

        # And the folded current state reflects the revert.
        state = await current_state(
            db_session, ENTITY_TYPE_EXTRACTION_ENTITY, entity_id,
        )
        assert state == {"v": 2, "approved": True}

    @pytest.mark.asyncio
    async def test_revert_to_rev_404s_on_unknown_target(
        self, db_session, sample_run,
    ) -> None:
        with pytest.raises(ValueError, match="rev not found"):
            await revert_to_rev(
                db_session,
                project_id=sample_run["project_id"],
                entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
                entity_id=_new_entity_id(),
                target_rev=1,
            )


class TestEventTimeline:
    @pytest.mark.asyncio
    async def test_event_timeline_sorts_newest_first_and_paginates_via_before_rev(
        self, db_session, sample_run,
    ) -> None:
        entity_id = _new_entity_id()
        await apply_event(
            db_session,
            project_id=sample_run["project_id"],
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_CREATE,
            new_state={"v": 1},
        )
        for i in range(2, 6):
            await apply_event(
                db_session,
                project_id=sample_run["project_id"],
                entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
                entity_id=entity_id,
                op=OP_PATCH,
                new_state={"v": i},
            )
        await db_session.commit()

        # Newest first — full timeline.
        full = await event_timeline(
            db_session,
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
        )
        assert [ev.rev_no for ev in full] == [5, 4, 3, 2, 1]

        # Paginate: rows with rev_no < 3 → 2, 1 (newest first).
        page = await event_timeline(
            db_session,
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            before_rev=3,
        )
        assert [ev.rev_no for ev in page] == [2, 1]
