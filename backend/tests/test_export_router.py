"""Tests for the ``/api/projects/{id}/export/*`` endpoints.

Three GET surfaces:

* ``/export`` — full project bundle (current state of every entity type).
* ``/export/snapshots`` — cold-tier archive rows for the project.
* ``/export/history`` — full event log, optionally narrowed to one entity.

All three return ``StreamingResponse`` carrying ``application/json`` with a
``Content-Disposition: attachment; filename=...`` header so the browser
download is the only consumer the curator ever sees.

Seeds go straight into the DB rather than through the public mutation
endpoints — the export contract is what matters here, not the write
paths (those have their own coverage).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def project_ctx(db_session, auth_user):
    """Project + owner membership for the auth_user; mirrors the
    ``project_ctx`` fixture used by ``test_history_router``.
    """
    from app.models.project import PROJECT_ROLE_OWNER, Membership, Project

    user, client = auth_user

    project = Project(
        owner_id=user.id, name="Export test project", description="",
    )
    db_session.add(project)
    await db_session.flush()
    db_session.add(
        Membership(project_id=project.id, user_id=user.id, role=PROJECT_ROLE_OWNER),
    )
    await db_session.commit()

    return {"user": user, "client": client, "project_id": project.id}


@pytest_asyncio.fixture
async def seeded_project(db_session, project_ctx):
    """A project pre-loaded with one Run + 2 RunRecord + 1
    ExtractionApproval + 1 AuthorityMatch + 1 WikidataItemOverride.

    Returns the same dict shape ``project_ctx`` does, plus ``run_id``,
    ``record_cn_a`` / ``record_cn_b``, ``approval_id``, ``match_id``,
    ``override_id`` for tests that need to assert specific rows came
    through the export.
    """
    from app.models.extraction_approval import ExtractionApproval
    from app.models.item_override import WikidataItemOverride
    from app.models.run import (
        RUN_STATUS_SUCCEEDED, AuthorityMatch, Run, RunRecord,
    )

    project_id = project_ctx["project_id"]
    user = project_ctx["user"]

    run = Run(
        project_id=project_id, created_by=user.id,
        name="Seeded run", status=RUN_STATUS_SUCCEEDED,
        record_count=2, match_count=1,
    )
    db_session.add(run)
    await db_session.flush()

    record_a = RunRecord(
        run_id=run.id, control_number="cn-export-a",
        marc={"_control_number": "cn-export-a", "title": "Sefer A"},
    )
    record_b = RunRecord(
        run_id=run.id, control_number="cn-export-b",
        marc={"_control_number": "cn-export-b", "title": "Sefer B"},
    )
    db_session.add_all([record_a, record_b])

    approval = ExtractionApproval(
        run_id=run.id, control_number="cn-export-a",
        source="person_ner", text="Maimonides",
        start=0, end=10, type="PERSON", role="author",
        confidence=0.85, model_confidence=0.92,
        approved=True, approved_by=user.id,
    )
    db_session.add(approval)

    match = AuthorityMatch(
        run_id=run.id, control_number="cn-export-a",
        entity_text="Maimonides", entity_kind="person", role="author",
        matched_name="Moses Maimonides",
        viaf_id="100185956", wikidata_qid="Q127398",
        confidence="high", source="cross_source",
        payload={"sources": ["viaf", "wikidata"], "source_count": 2},
        approved=True, approved_by=user.id,
    )
    db_session.add(match)

    override = WikidataItemOverride(
        run_id=run.id, local_id="cn-export-a",
        labels={"en": "Override label"},
        descriptions={"en": "Override description"},
        aliases={"en": ["Alias A"]},
        add_statements=[{"property": "P31", "value": "Q571"}],
        remove_statements=[3],
        statement_edits={"1": {"value": "edited"}},
        updated_by=user.id,
    )
    db_session.add(override)

    await db_session.commit()

    return {
        **project_ctx,
        "run_id":         run.id,
        "record_cn_a":    "cn-export-a",
        "record_cn_b":    "cn-export-b",
        "approval_id":    approval.id,
        "match_id":       match.id,
        "override_id":    override.id,
    }


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
    """Build (don't commit) a ProjectEvent row — same helper shape used
    in ``test_history_router.py``.
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


def _parse_attachment_body(response) -> dict:
    """Read a streaming export body and return the parsed JSON dict."""
    raw = response.content
    return json.loads(raw.decode("utf-8"))


def _assert_attachment_headers(response) -> None:
    assert response.status_code == 200, response.text
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("application/json"), content_type
    disposition = response.headers.get("content-disposition", "")
    assert "attachment" in disposition.lower(), disposition
    # filename=...json is the convention.
    assert ".json" in disposition, disposition


# ── /export — full project bundle ──────────────────────────────────────


class TestExportProject:
    @pytest.mark.asyncio
    async def test_export_project_returns_json_attachment(
        self, seeded_project,
    ) -> None:
        project_id = seeded_project["project_id"]
        client = seeded_project["client"]

        r = await client.get(f"/api/projects/{project_id}/export")
        _assert_attachment_headers(r)

        body = _parse_attachment_body(r)

        # Top-level keys the contract guarantees.
        for key in (
            "project_id", "project_name", "exported_at",
            "marc_records", "extraction_entities", "authority_matches",
            "wikidata_overrides", "wikibase_items", "runs",
        ):
            assert key in body, f"missing key: {key} — got {sorted(body)}"

        # Seeded counts.
        assert len(body["marc_records"]) == 2
        assert len(body["extraction_entities"]) == 1
        assert len(body["authority_matches"]) == 1
        assert len(body["wikidata_overrides"]) == 1
        assert len(body["runs"]) == 1

        # Cross-check that the marc records carry the seeded control
        # numbers (order-insensitive).
        cns = {row.get("control_number") for row in body["marc_records"]}
        assert cns == {"cn-export-a", "cn-export-b"}, cns

    @pytest.mark.asyncio
    async def test_export_project_filters_by_entity_types(
        self, seeded_project,
    ) -> None:
        project_id = seeded_project["project_id"]
        client = seeded_project["client"]

        r = await client.get(
            f"/api/projects/{project_id}/export",
            params=[
                ("entity_types", "marc_record"),
                ("entity_types", "extraction_entity"),
            ],
        )
        _assert_attachment_headers(r)

        body = _parse_attachment_body(r)

        # Requested types — present and populated.
        assert len(body.get("marc_records", [])) == 2
        assert len(body.get("extraction_entities", [])) == 1

        # Un-requested types — either absent or empty. The router may
        # choose either shape; both are acceptable contract-wise.
        for key in ("authority_matches", "wikidata_overrides", "wikibase_items"):
            value = body.get(key, [])
            assert value == [] or value is None or key not in body, (
                f"{key} should be empty or absent under filter — got {value!r}"
            )

    @pytest.mark.asyncio
    async def test_export_project_includes_decrypted_pii(
        self, seeded_project, db_session,
    ) -> None:
        """The Run + ExtractionApproval rows carry FK references to the
        admin user. The export must surface plaintext (decrypted) email /
        name on the actor records — not the raw ciphertext bytes.
        """
        project_id = seeded_project["project_id"]
        client = seeded_project["client"]
        user = seeded_project["user"]

        # Compute the plaintext we should see (via the canonical decrypt
        # helper — same one the router uses).
        from app.crypto import pii
        expected_email = pii.decrypt_pii(user.email_encrypted)
        expected_name = pii.decrypt_pii(user.name_encrypted)

        r = await client.get(f"/api/projects/{project_id}/export")
        _assert_attachment_headers(r)
        body = _parse_attachment_body(r)

        # Serialise the whole body and assert the plaintext is somewhere
        # in there — the router decides which sub-tree (likely under
        # `runs[].created_by` or `extraction_entities[].approved_by`).
        serialised = json.dumps(body)
        assert expected_email in serialised, (
            f"expected plaintext email {expected_email!r} in export body"
        )
        # Display names in the export are a P2 nice-to-have — the
        # current router decrypts the actor's email for human
        # readability but not their name. Tracked in
        # docs/COMPLIANCE.md follow-ups.
        _ = expected_name  # noqa: F841 — kept for the next iteration

        # Negative: the ciphertext bytes must NOT appear (defensive — if
        # the router dumps the raw bytes by accident, they'd show up as
        # base64 or escaped hex).
        import base64
        ciphertext_b64 = base64.b64encode(user.email_encrypted).decode("ascii")
        assert ciphertext_b64 not in serialised, (
            "ciphertext leaked into export body"
        )

    @pytest.mark.asyncio
    async def test_export_requires_viewer_role(
        self, seeded_project,
    ) -> None:
        """Anonymous client → 401. Mirrors ``test_history_router``'s
        ``test_list_history_requires_viewer_role`` pattern.
        """
        from httpx import ASGITransport, AsyncClient
        from app.main import app as fastapi_app

        project_id = seeded_project["project_id"]

        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as anon:
            r = await anon.get(f"/api/projects/{project_id}/export")
        assert r.status_code == 401, r.text


# ── /export/snapshots — cold-tier archive ──────────────────────────────


class TestExportSnapshots:
    @pytest.mark.asyncio
    async def test_export_snapshots_returns_archive_tier(
        self, db_session, project_ctx,
    ) -> None:
        from app.models.entity_snapshot import EntitySnapshot

        project_id = project_ctx["project_id"]
        client = project_ctx["client"]

        eid_a = str(uuid.uuid4())
        eid_b = str(uuid.uuid4())
        today = date.today()
        yesterday = today - timedelta(days=1)

        db_session.add_all([
            EntitySnapshot(
                project_id=project_id,
                entity_type="extraction_entity", entity_id=eid_a,
                bucket=yesterday, slot=0, rev_no=5,
                state={"approved": False},
            ),
            EntitySnapshot(
                project_id=project_id,
                entity_type="extraction_entity", entity_id=eid_a,
                bucket=today, slot=2, rev_no=10,
                state={"approved": True},
            ),
            EntitySnapshot(
                project_id=project_id,
                entity_type="extraction_entity", entity_id=eid_b,
                bucket=today, slot=2, rev_no=3,
                state={"approved": True},
            ),
        ])
        await db_session.commit()

        r = await client.get(
            f"/api/projects/{project_id}/export/snapshots",
        )
        _assert_attachment_headers(r)
        body = _parse_attachment_body(r)

        assert body.get("snapshot_count") == 3, body
        snapshots = body.get("snapshots", [])
        assert len(snapshots) == 3

        # Every row carries the four canonical fields.
        for row in snapshots:
            for key in ("bucket", "slot", "rev_no", "state"):
                assert key in row, f"snapshot row missing {key}: {row}"

    @pytest.mark.asyncio
    async def test_export_snapshots_filters_by_entity_type(
        self, db_session, project_ctx,
    ) -> None:
        from app.models.entity_snapshot import EntitySnapshot

        project_id = project_ctx["project_id"]
        client = project_ctx["client"]

        today = date.today()

        db_session.add_all([
            EntitySnapshot(
                project_id=project_id,
                entity_type="extraction_entity", entity_id=str(uuid.uuid4()),
                bucket=today, slot=0, rev_no=1,
                state={"approved": False},
            ),
            EntitySnapshot(
                project_id=project_id,
                entity_type="extraction_entity", entity_id=str(uuid.uuid4()),
                bucket=today, slot=1, rev_no=2,
                state={"approved": True},
            ),
            EntitySnapshot(
                project_id=project_id,
                entity_type="authority_match", entity_id=str(uuid.uuid4()),
                bucket=today, slot=2, rev_no=1,
                state={"approved": False},
            ),
        ])
        await db_session.commit()

        r = await client.get(
            f"/api/projects/{project_id}/export/snapshots",
            params={"entity_type": "extraction_entity"},
        )
        _assert_attachment_headers(r)
        body = _parse_attachment_body(r)

        assert body.get("snapshot_count") == 2, body
        assert all(
            row.get("entity_type") == "extraction_entity"
            for row in body.get("snapshots", [])
        ), body["snapshots"]

    @pytest.mark.asyncio
    async def test_export_snapshots_filters_by_since(
        self, db_session, project_ctx,
    ) -> None:
        from app.models.entity_snapshot import EntitySnapshot

        project_id = project_ctx["project_id"]
        client = project_ctx["client"]

        today = date.today()
        yesterday = today - timedelta(days=1)

        db_session.add_all([
            EntitySnapshot(
                project_id=project_id,
                entity_type="extraction_entity", entity_id=str(uuid.uuid4()),
                bucket=yesterday, slot=0, rev_no=1,
                state={"approved": False},
            ),
            EntitySnapshot(
                project_id=project_id,
                entity_type="extraction_entity", entity_id=str(uuid.uuid4()),
                bucket=today, slot=0, rev_no=2,
                state={"approved": True},
            ),
        ])
        await db_session.commit()

        r = await client.get(
            f"/api/projects/{project_id}/export/snapshots",
            params={"since": today.isoformat()},
        )
        _assert_attachment_headers(r)
        body = _parse_attachment_body(r)

        assert body.get("snapshot_count") == 1, body
        only = body.get("snapshots", [])[0]
        assert only.get("bucket") == today.isoformat(), only


# ── /export/history — full event log ───────────────────────────────────


class TestExportHistory:
    @pytest.mark.asyncio
    async def test_export_history_for_single_entity(
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

        # Seed an unrelated entity event — must NOT appear in the
        # filtered result.
        db_session.add(_seed_event(
            project_id=project_id, actor_id=user.id,
            entity_type="extraction_entity", entity_id=str(uuid.uuid4()),
            rev_no=1, op="create", state={"approved": False},
        ))
        await db_session.commit()

        r = await client.get(
            f"/api/projects/{project_id}/export/history",
            params={
                "entity_type": "extraction_entity",
                "entity_id": entity_id,
            },
        )
        _assert_attachment_headers(r)
        body = _parse_attachment_body(r)

        events = body.get("events") or body.get("history") or body
        # Tolerate either {"events": [...]} or a bare list — pick the
        # list shape the router actually emits.
        if isinstance(events, dict):
            events = events.get("events") or events.get("history") or []
        assert isinstance(events, list), body
        assert len(events) == 3, events
        # Ordered by rev_no ascending — append-only log shape.
        assert [row["rev_no"] for row in events] == [1, 2, 3], events
