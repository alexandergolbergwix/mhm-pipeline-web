"""Tests for the two Heroku-Scheduler jobs in ``app.jobs``.

* :func:`app.jobs.snapshot_entities.snapshot_touched_entities` — the
  3x/day archive snapshot that writes one ``EntitySnapshot`` row per
  entity touched in the current UTC slot, idempotent on
  ``(entity_type, entity_id, bucket, slot)``.
* :func:`app.jobs.prune_events.prune_events` — the daily 03:05 UTC
  rolling 1000-event window prune that preserves every ``op="create"``
  and ``op="snapshot"`` anchor row.

Time control follows the same pattern as ``test_email_throttle.py``:
the job reads wall time via ``datetime.now(timezone.utc)``, so we swap
the module's ``datetime`` symbol for a small fake whose ``now`` returns
whatever the parametrised hour set on it last.

Direct ``db_session.add(ProjectEvent(...))`` is faster than going
through ``apply_event`` and lets us hand-craft adversarial fixtures
(1010 patches for a single entity, parent-chain unset) that
``apply_event`` would refuse to build.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from app.jobs import snapshot_entities as snap_mod
from app.jobs.prune_events import prune_events
from app.jobs.snapshot_entities import snapshot_touched_entities
from app.models.entity_snapshot import EntitySnapshot
from app.models.event import (
    OP_CREATE,
    OP_PATCH,
    OP_SNAPSHOT,
    ProjectEvent,
)


# ── Frozen-clock helper (mirrors test_email_throttle.py) ──────────────


class _FrozenDatetime:
    """Drop-in for the ``datetime`` symbol inside ``snapshot_entities``.

    Only ``.now(tz)`` is overridden; every other attribute access falls
    through to the real ``datetime`` class so calls like
    ``datetime.now(timezone.utc).replace(hour=…)`` keep working.
    """

    current: datetime = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz: timezone | None = None) -> datetime:
        if tz is None:
            return cls.current.replace(tzinfo=None)
        return cls.current.astimezone(tz)

    def __getattr__(self, name: str) -> Any:  # pragma: no cover — defensive
        return getattr(datetime, name)


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> type[_FrozenDatetime]:
    """Replace ``snapshot_entities.datetime`` with the frozen fake."""
    _FrozenDatetime.current = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(snap_mod, "datetime", _FrozenDatetime)
    return _FrozenDatetime


# ── Schema fixup: the production migration adds a unique index on
# ``(entity_type, entity_id, bucket, slot)`` that the ORM model does
# NOT declare. ``snapshot_touched_entities`` upserts via
# ``ON CONFLICT (entity_type, entity_id, bucket, slot)``, which SQLite
# rejects with ``OperationalError`` if no such unique index exists.
# Add it once per test so the upsert finds a constraint to honour.


@pytest_asyncio.fixture
async def ensure_entity_snapshot_index(db_session) -> None:
    """Create the ``ux_entity_snapshot_slot`` unique index on demand.

    The ``CREATE UNIQUE INDEX IF NOT EXISTS`` form means the index
    survives across the conftest's per-test truncation (the schema is
    session-scoped) but is harmless if it has already been created by
    a previous test in the same session.
    """
    await db_session.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_entity_snapshot_slot "
            "ON entity_snapshots (entity_type, entity_id, bucket, slot)"
        )
    )
    await db_session.commit()


# ── Direct-insert helpers ─────────────────────────────────────────────


def _make_event(
    *,
    project_id: uuid.UUID,
    entity_id: str,
    rev_no: int,
    op: str,
    created_at: datetime,
    state: dict[str, Any] | None = None,
    patch: list[Any] | None = None,
    entity_type: str = "authority_match",
) -> ProjectEvent:
    """Build a ``ProjectEvent`` row with the per-task fixture shape.

    Sets ``parent_event_id=None`` and ``payload={}`` so the caller can
    seed an arbitrary rev_no chain without walking it via
    ``apply_event`` — exactly what the prune tests need (1010-row
    fixtures that ``apply_event`` would never construct).

    The ``state``/``patch`` columns are omitted from the constructor
    call when ``None`` so SQLAlchemy issues a SQL ``NULL`` rather than
    serialising Python ``None`` to the JSON string ``"null"`` (which
    SQLite's plain ``JSON`` impl does — making ``state IS NOT NULL``
    incorrectly true and breaking the versioning replay).
    """
    kwargs: dict[str, Any] = dict(
        project_id=project_id,
        type=f"{entity_type}.{op}",
        payload={},
        entity_type=entity_type,
        entity_id=entity_id,
        rev_no=rev_no,
        parent_event_id=None,
        op=op,
        created_at=created_at,
    )
    if state is not None:
        kwargs["state"] = state
    if patch is not None:
        kwargs["patch"] = patch
    return ProjectEvent(**kwargs)


# ── snapshot_touched_entities ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_creates_entity_snapshot_row_per_touched_entity(
    db_session,
    frozen_clock,
    ensure_entity_snapshot_index,
) -> None:
    """One create + one patch in the current slot → one snapshot row.

    The snapshot's ``state`` must match the folded state after the
    rev_no=2 patch (``{"k": "v2"}``), and the summary counters must
    both report exactly one.
    """
    project_id = uuid.uuid4()
    now = frozen_clock.current  # inside slot 1 (12:00 UTC → hour//8 = 1)

    create = _make_event(
        project_id=project_id,
        entity_id="auth-1",
        rev_no=1,
        op=OP_CREATE,
        created_at=now,
        state={"k": "v1"},
    )
    patch = _make_event(
        project_id=project_id,
        entity_id="auth-1",
        rev_no=2,
        op=OP_PATCH,
        created_at=now,
        patch=[{"op": "replace", "path": "/k", "value": "v2"}],
    )
    db_session.add_all([create, patch])
    await db_session.commit()

    result = await snapshot_touched_entities(db_session)
    assert result == {"snapshots_written": 1, "entities_touched": 1}

    rows = (await db_session.execute(select(EntitySnapshot))).scalars().all()
    assert len(rows) == 1
    assert rows[0].entity_type == "authority_match"
    assert rows[0].entity_id == "auth-1"
    assert rows[0].rev_no == 2
    # Folded state after the patch applied on the create's seed state.
    assert rows[0].state == {"k": "v2"}


@pytest.mark.asyncio
async def test_snapshot_idempotent_within_same_slot(
    db_session,
    frozen_clock,
    ensure_entity_snapshot_index,
) -> None:
    """Running the snapshot job twice in a row keeps exactly one row.

    The ``ON CONFLICT (entity_type, entity_id, bucket, slot)`` clause
    upserts rather than insert-duplicates, so Heroku Scheduler retries
    cannot multiply the archive table.
    """
    project_id = uuid.uuid4()
    now = frozen_clock.current

    db_session.add_all(
        [
            _make_event(
                project_id=project_id,
                entity_id="auth-1",
                rev_no=1,
                op=OP_CREATE,
                created_at=now,
                state={"k": "v1"},
            ),
            _make_event(
                project_id=project_id,
                entity_id="auth-1",
                rev_no=2,
                op=OP_PATCH,
                created_at=now,
                patch=[{"op": "replace", "path": "/k", "value": "v2"}],
            ),
        ]
    )
    await db_session.commit()

    await snapshot_touched_entities(db_session)
    await snapshot_touched_entities(db_session)

    count = await db_session.scalar(select(func.count(EntitySnapshot.id)))
    assert count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hour,expected_slot",
    [(0, 0), (5, 0), (8, 1), (13, 1), (16, 2), (23, 2)],
)
async def test_snapshot_picks_correct_slot_for_utc_hour(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    ensure_entity_snapshot_index,
    hour: int,
    expected_slot: int,
) -> None:
    """``slot = hour // 8`` — the three rolling 8-hour windows in UTC.

    Freeze the clock at the parametrised hour, seed one event at the
    same moment, and assert the persisted row carries the expected
    slot number.
    """
    frozen = datetime(2026, 6, 2, hour, 30, 0, tzinfo=timezone.utc)
    _FrozenDatetime.current = frozen
    monkeypatch.setattr(snap_mod, "datetime", _FrozenDatetime)

    project_id = uuid.uuid4()
    db_session.add(
        _make_event(
            project_id=project_id,
            entity_id=f"auth-{hour}",
            rev_no=1,
            op=OP_CREATE,
            created_at=frozen,
            state={"hour": hour},
        )
    )
    await db_session.commit()

    await snapshot_touched_entities(db_session)

    row = (
        await db_session.execute(
            select(EntitySnapshot).where(EntitySnapshot.entity_id == f"auth-{hour}")
        )
    ).scalar_one()
    assert row.slot == expected_slot
    assert row.bucket == frozen.date()


@pytest.mark.asyncio
async def test_snapshot_skips_entities_with_no_events_since_last_slot(
    db_session,
    frozen_clock,
    ensure_entity_snapshot_index,
) -> None:
    """Events older than the current slot window are NOT snapshotted.

    Clock is frozen at 12:00 UTC (slot 1, slot_start = 08:00). An event
    9 hours earlier (03:00 UTC of the same day, slot 0) falls outside
    the snapshot window, so ``entities_touched`` is 0.
    """
    project_id = uuid.uuid4()
    # 9 hours before noon = 03:00 UTC, well inside the previous slot.
    nine_hours_ago = frozen_clock.current.replace(hour=3, minute=0, second=0)

    db_session.add(
        _make_event(
            project_id=project_id,
            entity_id="auth-stale",
            rev_no=1,
            op=OP_CREATE,
            created_at=nine_hours_ago,
            state={"k": "v"},
        )
    )
    await db_session.commit()

    result = await snapshot_touched_entities(db_session)
    assert result["entities_touched"] == 0
    assert result["snapshots_written"] == 0


# ── prune_events ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prune_deletes_oldest_past_1000(db_session) -> None:
    """1010 patches for one entity → prune drops the 10 oldest patches.

    ``HARD_CAP = 1000``; the excess of 10 is computed from the
    non-anchor (patch/revert) rows. Direct INSERT is fine: the prune
    job walks rev_no order, not the parent chain.
    """
    project_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    for rev in range(1, 1011):  # rev_no 1..1010, all op=patch
        db_session.add(
            _make_event(
                project_id=project_id,
                entity_id="auth-bulk",
                rev_no=rev,
                op=OP_PATCH,
                created_at=now,
                patch=[],
            )
        )
    await db_session.commit()

    result = await prune_events(db_session)
    assert result["events_deleted"] == 10
    assert result["entities_pruned"] == 1

    remaining = await db_session.scalar(
        select(func.count(ProjectEvent.id)).where(
            ProjectEvent.entity_id == "auth-bulk"
        )
    )
    assert remaining == 1000

    remaining_revs = (
        await db_session.execute(
            select(ProjectEvent.rev_no)
            .where(ProjectEvent.entity_id == "auth-bulk")
            .order_by(ProjectEvent.rev_no)
        )
    ).scalars().all()
    assert remaining_revs[0] == 11  # rev 1..10 deleted
    assert remaining_revs[-1] == 1010
    assert len(remaining_revs) == 1000


@pytest.mark.asyncio
async def test_prune_preserves_create_anchor(db_session) -> None:
    """The ``op="create"`` row is the entity's anchor — never deleted.

    Seed 1 create + 1010 patches (total 1011) and assert the create
    survives. The 10 oldest patches (rev_no 2..11) are gone, the create
    (rev_no=1) and patches 12..1011 remain — total 1000 rows.
    """
    project_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    db_session.add(
        _make_event(
            project_id=project_id,
            entity_id="auth-anchor",
            rev_no=1,
            op=OP_CREATE,
            created_at=now,
            state={"seed": True},
        )
    )
    for rev in range(2, 1012):  # rev_no 2..1011, all op=patch
        db_session.add(
            _make_event(
                project_id=project_id,
                entity_id="auth-anchor",
                rev_no=rev,
                op=OP_PATCH,
                created_at=now,
                patch=[],
            )
        )
    await db_session.commit()

    await prune_events(db_session)

    create_row = await db_session.scalar(
        select(ProjectEvent).where(
            ProjectEvent.entity_id == "auth-anchor",
            ProjectEvent.op == OP_CREATE,
        )
    )
    assert create_row is not None
    assert create_row.rev_no == 1

    total = await db_session.scalar(
        select(func.count(ProjectEvent.id)).where(
            ProjectEvent.entity_id == "auth-anchor"
        )
    )
    assert total == 1000  # 1 create + 999 patches

    remaining_revs = (
        await db_session.execute(
            select(ProjectEvent.rev_no)
            .where(ProjectEvent.entity_id == "auth-anchor")
            .order_by(ProjectEvent.rev_no)
        )
    ).scalars().all()
    # The oldest patches (rev_no 2..12) were dropped — 11 patches gone
    # so the create stays but rev_no 2..12 are missing.
    assert remaining_revs[0] == 1  # create survives
    assert 2 not in remaining_revs
    assert 12 not in remaining_revs
    assert 13 in remaining_revs


@pytest.mark.asyncio
async def test_prune_preserves_snapshot_events(db_session) -> None:
    """``op="snapshot"`` events survive prune — they're replay keepers.

    Mix: rev 1=create, rev 50/100/150=snapshot, plus patches for the
    other 1006 revs (total 1010). After prune every snapshot row must
    still exist; only the oldest non-anchor patches are gone.
    """
    project_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    snapshot_revs = {50, 100, 150}
    db_session.add(
        _make_event(
            project_id=project_id,
            entity_id="auth-mix",
            rev_no=1,
            op=OP_CREATE,
            created_at=now,
            state={"seed": True},
        )
    )
    for rev in range(2, 1011):  # rev 2..1010
        if rev in snapshot_revs:
            db_session.add(
                _make_event(
                    project_id=project_id,
                    entity_id="auth-mix",
                    rev_no=rev,
                    op=OP_SNAPSHOT,
                    created_at=now,
                    state={"snap_at": rev},
                )
            )
        else:
            db_session.add(
                _make_event(
                    project_id=project_id,
                    entity_id="auth-mix",
                    rev_no=rev,
                    op=OP_PATCH,
                    created_at=now,
                    patch=[],
                )
            )
    await db_session.commit()

    await prune_events(db_session)

    surviving_snaps = (
        await db_session.execute(
            select(ProjectEvent.rev_no).where(
                ProjectEvent.entity_id == "auth-mix",
                ProjectEvent.op == OP_SNAPSHOT,
            )
        )
    ).scalars().all()
    assert set(surviving_snaps) == snapshot_revs

    create_still_there = await db_session.scalar(
        select(func.count(ProjectEvent.id)).where(
            ProjectEvent.entity_id == "auth-mix",
            ProjectEvent.op == OP_CREATE,
        )
    )
    assert create_still_there == 1


@pytest.mark.asyncio
async def test_prune_no_op_under_1000(db_session) -> None:
    """A group with ≤ HARD_CAP rows is left untouched.

    500 patches → ``events_deleted == 0`` and ``entities_pruned == 0``.
    The grouping query's ``HAVING > HARD_CAP`` clause skips the group
    entirely so the inner loop never executes.
    """
    project_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    for rev in range(1, 501):
        db_session.add(
            _make_event(
                project_id=project_id,
                entity_id="auth-small",
                rev_no=rev,
                op=OP_PATCH,
                created_at=now,
                patch=[],
            )
        )
    await db_session.commit()

    result = await prune_events(db_session)
    assert result == {"events_deleted": 0, "entities_pruned": 0}

    remaining = await db_session.scalar(
        select(func.count(ProjectEvent.id)).where(
            ProjectEvent.entity_id == "auth-small"
        )
    )
    assert remaining == 500
