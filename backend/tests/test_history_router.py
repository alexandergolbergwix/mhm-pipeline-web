"""Tests for the entity-versioned ``/projects/{id}/history`` endpoints.

The legacy ``/events`` + ``/snapshots`` + ``/restore`` endpoints already
have coverage elsewhere; this module targets the per-entity surface added
on top of the versioning core (``GET /history``, ``GET /history/diff``,
``GET /history/at``, ``POST /history/revert``, ``GET /history/snapshots``).

Seeds go straight into the DB via ``db_session.add(ProjectEvent(...))``
rather than through ``apply_event`` — the controlled rev_no /
parent_event_id chain matters more than going through the real writer
for these tests.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def project_ctx(db_session, auth_user):
    """Create one project + owner membership for the auth_user and return
    the (user, client, project_id) triple.

    ``auth_user`` is global-role ``editor`` but here gets ``owner`` of
    the project — which is what ``require_viewer`` /
    ``require_editor`` short-circuit on.
    """
    from app.models.project import PROJECT_ROLE_OWNER, Membership, Project

    user, client = auth_user

    project = Project(
        owner_id=user.id, name="History test project", description="",
    )
    db_session.add(project)
    await db_session.flush()
    db_session.add(
        Membership(project_id=project.id, user_id=user.id, role=PROJECT_ROLE_OWNER),
    )
    await db_session.commit()

    return {"user": user, "client": client, "project_id": project.id}


def _seed_event(
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    entity_type: str,
    entity_id: str,
    rev_no: int,
    op: str,
    state: dict | None = None,
    patch: list | None = None,
    parent_event_id: uuid.UUID | None = None,
    message: str | None = None,
    created_at: datetime | None = None,
):
    """Build (don't commit) a ProjectEvent row with controlled rev_no.

    Important: state/patch are only set on the row when they're non-None.
    On SQLite (test backend), explicitly passing ``state=None`` to a
    JSON-typed column serializes as JSON ``null`` rather than SQL NULL,
    which breaks ``state.isnot(None)`` queries inside the versioning
    replay machinery. Omitting the kwarg leaves the column at SQL NULL
    where it belongs.
    """
    from app.models.event import ProjectEvent

    kwargs: dict = {
        "project_id":      project_id,
        "actor_id":        actor_id,
        "type":            f"{entity_type}.{op}",
        "payload":         {},
        "entity_type":     entity_type,
        "entity_id":       entity_id,
        "rev_no":          rev_no,
        "parent_event_id": parent_event_id,
        "op":              op,
        "created_at":      created_at or datetime.now(timezone.utc),
    }
    if state is not None:
        kwargs["state"] = state
    if patch is not None:
        kwargs["patch"] = patch
    if message is not None:
        kwargs["message"] = message
    return ProjectEvent(**kwargs)


# ── /history (list) ────────────────────────────────────────────────────


class TestListHistory:
    @pytest.mark.asyncio
    async def test_list_history_returns_events_newest_first(
        self, db_session, project_ctx,
    ) -> None:
        project_id = project_ctx["project_id"]
        user = project_ctx["user"]
        client = project_ctx["client"]

        entity_id = str(uuid.uuid4())
        e1 = _seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=entity_id,
            rev_no=1, op="create", state={"approved": False},
        )
        db_session.add(e1)
        await db_session.flush()

        e2 = _seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=entity_id,
            rev_no=2, op="patch",
            patch=[{"op": "replace", "path": "/approved", "value": True}],
            parent_event_id=e1.id,
        )
        db_session.add(e2)
        await db_session.flush()

        e3 = _seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=entity_id,
            rev_no=3, op="patch",
            patch=[{"op": "replace", "path": "/approved", "value": False}],
            parent_event_id=e2.id,
        )
        db_session.add(e3)
        await db_session.commit()

        r = await client.get(
            f"/api/projects/{project_id}/history",
            params={
                "entity_type": "extraction_entity",
                "entity_id": entity_id,
            },
        )
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 3
        assert [row["rev_no"] for row in rows] == [3, 2, 1]

    @pytest.mark.asyncio
    async def test_list_history_filters_by_entity_type_and_id(
        self, db_session, project_ctx,
    ) -> None:
        project_id = project_ctx["project_id"]
        user = project_ctx["user"]
        client = project_ctx["client"]

        target_id = str(uuid.uuid4())
        other_id = str(uuid.uuid4())

        db_session.add(_seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=target_id,
            rev_no=1, op="create", state={"approved": True},
        ))
        db_session.add(_seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=other_id,
            rev_no=1, op="create", state={"approved": False},
        ))
        db_session.add(_seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="authority_match", entity_id=target_id,
            rev_no=1, op="create", state={"approved": False},
        ))
        await db_session.commit()

        r = await client.get(
            f"/api/projects/{project_id}/history",
            params={
                "entity_type": "extraction_entity",
                "entity_id": target_id,
            },
        )
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["rev_no"] == 1

    @pytest.mark.asyncio
    async def test_list_history_paginates_via_before_rev_query(
        self, db_session, project_ctx,
    ) -> None:
        project_id = project_ctx["project_id"]
        user = project_ctx["user"]
        client = project_ctx["client"]

        entity_id = str(uuid.uuid4())
        prior_id: uuid.UUID | None = None
        for rev in (1, 2, 3, 4, 5):
            ev = _seed_event(
                project_id=project_id, actor_id=user.id,
                entity_type="extraction_entity", entity_id=entity_id,
                rev_no=rev,
                op="create" if rev == 1 else "patch",
                state={"approved": False} if rev == 1 else None,
                patch=(
                    None if rev == 1
                    else [{"op": "replace", "path": "/approved", "value": rev % 2 == 0}]
                ),
                parent_event_id=prior_id,
            )
            db_session.add(ev)
            await db_session.flush()
            prior_id = ev.id
        await db_session.commit()

        r = await client.get(
            f"/api/projects/{project_id}/history",
            params={
                "entity_type": "extraction_entity",
                "entity_id": entity_id,
                "before_rev": 3,
            },
        )
        assert r.status_code == 200
        rows = r.json()
        # rev_no < 3, newest first.
        assert [row["rev_no"] for row in rows] == [2, 1]

    @pytest.mark.asyncio
    async def test_list_history_requires_viewer_role(
        self, db_session, project_ctx, async_client,
    ) -> None:
        """Unauthenticated client (no cookie) gets 401."""
        from httpx import ASGITransport, AsyncClient

        # Drop the auth cookie by spinning up an anonymous client against
        # the same app. The `async_client` fixture's jar carries the
        # session cookie set by `auth_user`; here we want NO cookie.
        from app.main import app as fastapi_app

        project_id = project_ctx["project_id"]
        entity_id = str(uuid.uuid4())

        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as anon:
            r = await anon.get(
                f"/api/projects/{project_id}/history",
                params={
                    "entity_type": "extraction_entity",
                    "entity_id": entity_id,
                },
            )
        assert r.status_code == 401


# ── /history/diff ──────────────────────────────────────────────────────


class TestDiff:
    @pytest.mark.asyncio
    async def test_diff_returns_patch_before_and_after(
        self, db_session, project_ctx,
    ) -> None:
        project_id = project_ctx["project_id"]
        user = project_ctx["user"]
        client = project_ctx["client"]

        entity_id = str(uuid.uuid4())

        e1 = _seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=entity_id,
            rev_no=1, op="create", state={"approved": False},
        )
        db_session.add(e1)
        await db_session.flush()

        e2 = _seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=entity_id,
            rev_no=2, op="patch",
            patch=[{"op": "replace", "path": "/approved", "value": True}],
            parent_event_id=e1.id,
        )
        db_session.add(e2)
        await db_session.commit()

        r = await client.get(
            f"/api/projects/{project_id}/history/diff",
            params={
                "entity_type": "extraction_entity",
                "entity_id": entity_id,
                "from": 1,
                "to": 2,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body["patch"], list), body
        assert body["before"] == {"approved": False}, body
        assert body["after"] == {"approved": True}, body
        # Patch should carry a replace op on /approved → True.
        assert any(
            op.get("op") == "replace"
            and op.get("path") == "/approved"
            and op.get("value") is True
            for op in body["patch"]
        ), body["patch"]

    @pytest.mark.asyncio
    async def test_diff_with_inverted_rev_range_returns_inverse_patch(
        self, db_session, project_ctx,
    ) -> None:
        """``from > to`` doesn't validate strict ordering — diff_revs
        returns the inverse patch + before/after swapped. Pin current
        behaviour so a regression that DOES start 400'ing is visible.
        """
        project_id = project_ctx["project_id"]
        user = project_ctx["user"]
        client = project_ctx["client"]

        entity_id = str(uuid.uuid4())
        e1 = _seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=entity_id,
            rev_no=1, op="create", state={"approved": False},
        )
        db_session.add(e1)
        await db_session.flush()
        e2 = _seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=entity_id,
            rev_no=2, op="patch",
            patch=[{"op": "replace", "path": "/approved", "value": True}],
            parent_event_id=e1.id,
        )
        db_session.add(e2)
        await db_session.commit()

        r = await client.get(
            f"/api/projects/{project_id}/history/diff",
            params={
                "entity_type": "extraction_entity",
                "entity_id": entity_id,
                "from": 2,
                "to": 1,
            },
        )
        # Implementation does not enforce from < to; document current
        # behaviour rather than guess at a 400.
        assert r.status_code == 200
        body = r.json()
        # before = state at rev 2 (True); after = state at rev 1 (False).
        assert body["before"] == {"approved": True}
        assert body["after"] == {"approved": False}


# ── /history/at ────────────────────────────────────────────────────────


class TestStateAt:
    @pytest.mark.asyncio
    async def test_at_returns_state_at_revision(
        self, db_session, project_ctx,
    ) -> None:
        project_id = project_ctx["project_id"]
        user = project_ctx["user"]
        client = project_ctx["client"]

        entity_id = str(uuid.uuid4())
        e1 = _seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=entity_id,
            rev_no=1, op="create", state={"approved": False, "note": "v1"},
        )
        db_session.add(e1)
        await db_session.flush()
        e2 = _seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=entity_id,
            rev_no=2, op="patch",
            patch=[{"op": "replace", "path": "/approved", "value": True}],
            parent_event_id=e1.id,
        )
        db_session.add(e2)
        await db_session.commit()

        r = await client.get(
            f"/api/projects/{project_id}/history/at",
            params={
                "entity_type": "extraction_entity",
                "entity_id": entity_id,
                "rev": 2,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["approved"] is True
        assert body["note"] == "v1"

    @pytest.mark.asyncio
    async def test_at_404s_on_unknown_rev(
        self, db_session, project_ctx,
    ) -> None:
        """Asking for a state at a rev *before* any event for the entity
        triggers the 404 path (``_latest_state_event`` returns None
        because no state-bearing event satisfies ``rev_no <= max_rev``).
        """
        project_id = project_ctx["project_id"]
        user = project_ctx["user"]
        client = project_ctx["client"]

        entity_id = str(uuid.uuid4())
        # Seed the entity starting at rev 5 — so asking for rev 1
        # (below the entity's existence window) returns None from
        # ``state_at_rev`` and the router 404s.
        db_session.add(_seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=entity_id,
            rev_no=5, op="create", state={"approved": False},
        ))
        await db_session.commit()

        r = await client.get(
            f"/api/projects/{project_id}/history/at",
            params={
                "entity_type": "extraction_entity",
                "entity_id": entity_id,
                "rev": 1,
            },
        )
        assert r.status_code == 404
        assert "state not found" in r.json().get("detail", "").lower()


# ── /history/revert ────────────────────────────────────────────────────


class TestRevert:
    @pytest.mark.asyncio
    async def test_revert_writes_new_event_and_updates_read_model(
        self, db_session, project_ctx,
    ) -> None:
        """Reverting an extraction_entity must:

        1. append a new event with op=revert at rev_no = prior_max + 1
        2. push the target_state values onto the ExtractionApproval row
        """
        from app.models.extraction_approval import ExtractionApproval
        from app.models.event import ProjectEvent
        from app.models.run import Run, RUN_STATUS_SUCCEEDED

        project_id = project_ctx["project_id"]
        user = project_ctx["user"]
        client = project_ctx["client"]

        # Build the read-model row first so the revert handler has
        # something to push back into.
        run = Run(
            project_id=project_id, created_by=user.id, name="r",
            status=RUN_STATUS_SUCCEEDED, record_count=0, match_count=0,
        )
        db_session.add(run)
        await db_session.flush()

        approval = ExtractionApproval(
            run_id=run.id, control_number="cn-revert",
            source="person_ner", text="Maimonides",
            start=0, end=10, type="PERSON", role="author",
            confidence=0.85, model_confidence=0.92,
            approved=True,  # current state — revert should flip this back
        )
        db_session.add(approval)
        await db_session.flush()

        entity_id = str(approval.id)

        e1 = _seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=entity_id,
            rev_no=1, op="create", state={"approved": False},
        )
        db_session.add(e1)
        await db_session.flush()
        e2 = _seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=entity_id,
            rev_no=2, op="patch",
            patch=[{"op": "replace", "path": "/approved", "value": True}],
            parent_event_id=e1.id,
        )
        db_session.add(e2)
        await db_session.commit()

        r = await client.post(
            f"/api/projects/{project_id}/history/revert",
            json={
                "entity_type": "extraction_entity",
                "entity_id": entity_id,
                "target_rev": 1,
                "message": "wrong direction",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["new_rev_no"] == 3

        # A new event row with op=revert exists at rev_no=3.
        events = (
            await db_session.execute(
                select(ProjectEvent).where(
                    ProjectEvent.entity_type == "extraction_entity",
                    ProjectEvent.entity_id == entity_id,
                ).order_by(ProjectEvent.rev_no.asc())
            )
        ).scalars().all()
        assert len(events) == 3
        assert events[-1].op == "revert"
        assert events[-1].rev_no == 3

        # The ExtractionApproval projection now reflects rev 1's state
        # (approved=False).
        await db_session.refresh(approval)
        assert approval.approved is False

    @pytest.mark.asyncio
    async def test_revert_requires_editor_role(
        self, db_session, project_ctx,
    ) -> None:
        """auth_user (project owner) is allowed; a no-viewer-fixture
        TODO covers the negative path."""
        project_id = project_ctx["project_id"]
        user = project_ctx["user"]
        client = project_ctx["client"]

        entity_id = str(uuid.uuid4())
        db_session.add(_seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=entity_id,
            rev_no=1, op="create", state={"approved": False},
        ))
        await db_session.commit()

        r = await client.post(
            f"/api/projects/{project_id}/history/revert",
            json={
                "entity_type": "extraction_entity",
                "entity_id": entity_id,
                "target_rev": 1,
                "message": "noop revert",
            },
        )
        # Owner has editor privileges → 200.
        assert r.status_code == 200, r.text
        # TODO: when a viewer-only fixture lands, re-run with that
        # cookie and assert 403 here.


# ── /events (legacy project-event log) ────────────────────────────────


class TestListEvents:
    @pytest.mark.asyncio
    async def test_events_ordered_newest_first(
        self, db_session, project_ctx,
    ) -> None:
        """GET /projects/{id}/events returns newest-first (created_at DESC)."""
        from app.models.event import ProjectEvent

        project_id = project_ctx["project_id"]
        user = project_ctx["user"]
        client = project_ctx["client"]

        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        for i in range(3):
            db_session.add(ProjectEvent(
                project_id=project_id,
                actor_id=user.id,
                type="match.approved",
                payload={"i": i},
                created_at=t0 + timedelta(hours=i),
            ))
        await db_session.commit()

        r = await client.get(f"/api/projects/{project_id}/events")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) >= 3
        # created_at should be non-ascending (newest first).
        timestamps = [row["created_at"] for row in rows[:3]]
        assert timestamps == sorted(timestamps, reverse=True), timestamps

    @pytest.mark.asyncio
    async def test_events_before_cursor_paginates(
        self, db_session, project_ctx,
    ) -> None:
        """?before= returns only events created before that timestamp."""
        from app.models.event import ProjectEvent

        project_id = project_ctx["project_id"]
        user = project_ctx["user"]
        client = project_ctx["client"]

        t0 = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
        events = []
        for i in range(5):
            ev = ProjectEvent(
                project_id=project_id,
                actor_id=user.id,
                type="match.approved",
                payload={"i": i},
                created_at=t0 + timedelta(hours=i),
            )
            db_session.add(ev)
            events.append(ev)
        await db_session.commit()

        # Use the 3rd event (index 2, hour=2) as the cursor.
        cursor = (t0 + timedelta(hours=2)).isoformat()
        r = await client.get(
            f"/api/projects/{project_id}/events",
            params={"before": cursor, "limit": 50},
        )
        assert r.status_code == 200, r.text
        rows = r.json()
        # Should return only events at hour=0 and hour=1 (before hour=2).
        returned_types = [row["created_at"] for row in rows]
        assert all(ts < cursor for ts in returned_types), returned_types

    @pytest.mark.asyncio
    async def test_events_limit_respected(
        self, db_session, project_ctx,
    ) -> None:
        from app.models.event import ProjectEvent

        project_id = project_ctx["project_id"]
        user = project_ctx["user"]
        client = project_ctx["client"]

        t0 = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
        for i in range(10):
            db_session.add(ProjectEvent(
                project_id=project_id,
                actor_id=user.id,
                type="match.approved",
                payload={"i": i},
                created_at=t0 + timedelta(hours=i),
            ))
        await db_session.commit()

        r = await client.get(
            f"/api/projects/{project_id}/events",
            params={"limit": 3},
        )
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 3


# ── /history/snapshots ─────────────────────────────────────────────────


class TestEntitySnapshots:
    @pytest.mark.asyncio
    async def test_snapshots_returns_archive_tier_rows(
        self, db_session, project_ctx,
    ) -> None:
        from app.models.entity_snapshot import EntitySnapshot
        from app.models.event import ProjectEvent

        project_id = project_ctx["project_id"]
        user = project_ctx["user"]
        client = project_ctx["client"]

        entity_id = str(uuid.uuid4())

        # The router's project-scope guard (`_assert_entity_in_project`)
        # needs at least one ProjectEvent for this (type, id) — seed a
        # create event so the snapshot probe gets past the guard.
        db_session.add(_seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=entity_id,
            rev_no=1, op="create", state={"approved": False},
        ))

        today = date.today()
        yesterday = today - timedelta(days=1)

        db_session.add(EntitySnapshot(
            project_id=project_id,
            entity_type="extraction_entity", entity_id=entity_id,
            bucket=yesterday, slot=0, rev_no=10,
            state={"approved": False},
        ))
        db_session.add(EntitySnapshot(
            project_id=project_id,
            entity_type="extraction_entity", entity_id=entity_id,
            bucket=today, slot=2, rev_no=42,
            state={"approved": True},
        ))
        await db_session.commit()

        r = await client.get(
            f"/api/projects/{project_id}/history/snapshots",
            params={
                "entity_type": "extraction_entity",
                "entity_id": entity_id,
            },
        )
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 2
        # Sorted bucket DESC, then slot DESC.
        assert rows[0]["bucket"] == today.isoformat()
        assert rows[0]["slot"] == 2
        assert rows[1]["bucket"] == yesterday.isoformat()
        assert rows[1]["slot"] == 0
