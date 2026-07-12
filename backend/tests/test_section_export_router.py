"""Tests for the section-level export endpoints.

URL surface:
    GET /runs/{id}/extraction/export?format=json|csv
    GET /runs/{id}/authority/export?format=json|csv
    GET /runs/{id}/rdf/export?format=ttl|nt      (skipped — needs built file on disk)
    GET /runs/{id}/wikibase/export?format=json|csv|ttl
    GET /runs/{id}/wikidata-studio/export?format=json|csv|ttl
"""

from __future__ import annotations

import csv
import json
import uuid

import pytest
import pytest_asyncio


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def run_ctx(db_session, auth_user):
    """Project + Run + baseline data, owner = auth_user."""
    from app.models.extraction_approval import ExtractionApproval
    from app.models.project import PROJECT_ROLE_OWNER, Membership, Project
    from app.models.run import (
        RUN_STATUS_SUCCEEDED, AuthorityMatch, Run,
    )

    user, client = auth_user
    project = Project(owner_id=user.id, name="Sec-export test", description="")
    db_session.add(project)
    await db_session.flush()
    db_session.add(Membership(project_id=project.id, user_id=user.id, role=PROJECT_ROLE_OWNER))
    run = Run(
        project_id=project.id, created_by=user.id,
        name="export-run", status=RUN_STATUS_SUCCEEDED,
        record_count=1, match_count=1,
    )
    db_session.add(run)
    await db_session.flush()

    approval = ExtractionApproval(
        run_id=run.id, control_number="cn-1",
        source="person_ner", text="Rashi",
        start=0, end=5, type="PERSON", role="author",
        confidence=0.9, model_confidence=0.95,
        approved=True, approved_by=user.id,
    )
    db_session.add(approval)

    match = AuthorityMatch(
        run_id=run.id, control_number="cn-1",
        entity_text="Rashi", entity_kind="person", role="author",
        matched_name="Solomon ben Isaac",
        viaf_id="246763096", wikidata_qid="Q237",
        confidence="high", source="viaf",
        payload={
            "preferred_name_lat": "Solomon ben Isaac",
            "preferred_name_heb": "שלמה בן יצחק",
            "cluster_ids": {"isni": "0000000121394695"},
        },
        approved=True, approved_by=user.id,
    )
    db_session.add(match)
    await db_session.commit()

    return {
        "user": user, "client": client,
        "project_id": project.id,
        "run_id": run.id,
        "approval_id": approval.id,
        "match_id": match.id,
    }


def _parse_csv(raw: bytes) -> list[dict[str, str]]:
    text = raw.decode("utf-8-sig")
    return list(csv.DictReader(text.splitlines()))


def _assert_attachment(r, suffix: str) -> None:
    assert r.status_code == 200, r.text
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd.lower(), cd
    assert suffix in cd, cd


# ── Extraction export ─────────────────────────────────────────────────


class TestExtractionExport:
    @pytest.mark.asyncio
    async def test_json_returns_entities(self, run_ctx):
        client = run_ctx["client"]
        run_id = run_ctx["run_id"]
        r = await client.get(f"/api/runs/{run_id}/extraction/export?format=json")
        _assert_attachment(r, ".json")
        body = json.loads(r.content)
        assert "entities" in body, body.keys()
        assert len(body["entities"]) == 1
        e = body["entities"][0]
        assert e["text"] == "Rashi"
        assert e["approved"] is True

    @pytest.mark.asyncio
    async def test_csv_returns_rows(self, run_ctx):
        client = run_ctx["client"]
        run_id = run_ctx["run_id"]
        r = await client.get(f"/api/runs/{run_id}/extraction/export?format=csv")
        _assert_attachment(r, ".csv")
        rows = _parse_csv(r.content)
        assert len(rows) == 1
        assert rows[0]["text"] == "Rashi"

    @pytest.mark.asyncio
    async def test_approved_only_filters(self, run_ctx, db_session):
        from app.models.extraction_approval import ExtractionApproval
        run_id = run_ctx["run_id"]
        # Add an unapproved entity
        db_session.add(ExtractionApproval(
            run_id=run_id, control_number="cn-1",
            source="person_ner", text="Nahmanides",
            start=6, end=16, type="PERSON", role="author",
            confidence=0.5, model_confidence=0.55,
            approved=False,
        ))
        await db_session.commit()

        client = run_ctx["client"]
        r = await client.get(
            f"/api/runs/{run_id}/extraction/export?format=json&approved_only=true",
        )
        assert r.status_code == 200
        body = json.loads(r.content)
        assert all(e["approved"] for e in body["entities"]), body["entities"]

    @pytest.mark.asyncio
    async def test_requires_auth(self, run_ctx):
        from httpx import ASGITransport, AsyncClient
        from app.main import app
        run_id = run_ctx["run_id"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
            r = await anon.get(f"/api/runs/{run_id}/extraction/export?format=json")
        assert r.status_code == 401, r.text


# ── Authority export ──────────────────────────────────────────────────


class TestAuthorityExport:
    @pytest.mark.asyncio
    async def test_json_returns_matches(self, run_ctx):
        client = run_ctx["client"]
        run_id = run_ctx["run_id"]
        r = await client.get(f"/api/runs/{run_id}/authority/export?format=json")
        _assert_attachment(r, ".json")
        body = json.loads(r.content)
        assert "matches" in body
        assert len(body["matches"]) == 1
        m = body["matches"][0]
        assert m["entity_text"] == "Rashi"
        assert m["wikidata_qid"] == "Q237"
        assert m["preferred_name_heb"] == "שלמה בן יצחק"

    @pytest.mark.asyncio
    async def test_csv_includes_cluster_ids(self, run_ctx):
        client = run_ctx["client"]
        run_id = run_ctx["run_id"]
        r = await client.get(f"/api/runs/{run_id}/authority/export?format=csv")
        _assert_attachment(r, ".csv")
        rows = _parse_csv(r.content)
        assert len(rows) == 1
        # cluster_ids is JSON-encoded in CSV
        assert "isni" in rows[0].get("cluster_ids", ""), rows[0]

    @pytest.mark.asyncio
    async def test_approved_only_filters(self, run_ctx, db_session):
        from app.models.run import AuthorityMatch
        run_id = run_ctx["run_id"]
        db_session.add(AuthorityMatch(
            run_id=run_id, control_number="cn-2",
            entity_text="Maimonides", entity_kind="person", role="",
            matched_name="Moses Maimonides",
            confidence="low", source="manual",
            payload={}, approved=False,
        ))
        await db_session.commit()

        client = run_ctx["client"]
        r = await client.get(
            f"/api/runs/{run_id}/authority/export?format=json&approved_only=true",
        )
        body = json.loads(r.content)
        assert all(m["approved"] for m in body["matches"]), body["matches"]


# ── Wikibase export ───────────────────────────────────────────────────


class TestWikibaseExport:
    @pytest_asyncio.fixture
    async def run_with_wikibase_events(self, run_ctx, db_session):
        from app.models.event import ENTITY_TYPE_WIKIBASE_ITEM, ProjectEvent
        project_id = run_ctx["project_id"]
        user = run_ctx["user"]

        ev = ProjectEvent(
            project_id=project_id,
            actor_id=user.id,
            type="wikibase_item.create",
            payload={},
            entity_type=ENTITY_TYPE_WIKIBASE_ITEM,
            entity_id="Manuscript:cn-1",
            rev_no=1,
            op="create",
            state={
                "labels": {"en": "Sefer Rashi", "he": "ספר רש\"י"},
                "descriptions": {"en": "A manuscript by Rashi"},
                "claims": [{"property": "P31", "value": "Q4006226"}],
            },
        )
        db_session.add(ev)
        await db_session.commit()
        return run_ctx

    @pytest.mark.asyncio
    async def test_json_returns_items(self, run_with_wikibase_events):
        ctx = run_with_wikibase_events
        r = await ctx["client"].get(f"/api/runs/{ctx['run_id']}/wikibase/export?format=json")
        _assert_attachment(r, ".json")
        body = json.loads(r.content)
        assert len(body.get("items", [])) >= 1

    @pytest.mark.asyncio
    async def test_ttl_returns_turtle(self, run_with_wikibase_events):
        ctx = run_with_wikibase_events
        r = await ctx["client"].get(f"/api/runs/{ctx['run_id']}/wikibase/export?format=ttl")
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "turtle" in ct or "ttl" in ct.lower(), ct
        assert b"@prefix" in r.content or len(r.content) > 0

    @pytest.mark.asyncio
    async def test_csv_returns_rows(self, run_with_wikibase_events):
        ctx = run_with_wikibase_events
        r = await ctx["client"].get(f"/api/runs/{ctx['run_id']}/wikibase/export?format=csv")
        _assert_attachment(r, ".csv")
        rows = _parse_csv(r.content)
        assert len(rows) >= 1


def test_wikidata_section_csv_row_preserves_review_and_source_evidence() -> None:
    from app.routers.section_export import _wikidata_csv_row

    row = _wikidata_csv_row({
        "local_id": "work:1",
        "entity_type": "work",
        "existing_qid": "Q123",
        "approved": None,
        "ai_verdict": {"overall": "partial", "reasoning": "needs author"},
        "labels": {"he": "ספר"},
        "descriptions": {"en": "Hebrew work"},
        "records": ["9901"],
        "statements": [{"property_id": "P2093", "value": "מחבר"}],
        "validation_issues": [{"code": "LABEL_QUOTE_NOISE", "severity": "warning"}],
        "authority_evidence": [],
        "work_candidate_evidence": [{"source_field": "505", "reason": "named_work_in_505"}],
    })

    assert row["approved"] is None
    assert row["ai_verdict_overall"] == "partial"
    assert "needs author" in row["ai_verdict_json"]
    assert '"P2093"' in row["statements_json"]
    assert '"named_work_in_505"' in row["work_candidate_evidence_json"]
