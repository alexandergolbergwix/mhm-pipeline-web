"""Tests for the section-level import endpoints.

URL surface:
    POST /runs/{id}/extraction/import   (JSON | CSV)
    POST /runs/{id}/authority/import    (JSON | CSV)
    POST /runs/{id}/rdf/import          (TTL)
    POST /runs/{id}/wikibase/import     (JSON)
    POST /runs/{id}/wikidata-studio/import  (JSON | CSV)
"""

from __future__ import annotations

import csv
import io
import json
import uuid

import pytest
import pytest_asyncio


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def run_ctx(db_session, auth_user):
    """Project + Run owned by auth_user, no seeded approvals/matches."""
    from app.models.project import PROJECT_ROLE_OWNER, Membership, Project
    from app.models.run import RUN_STATUS_SUCCEEDED, Run

    user, client = auth_user
    project = Project(owner_id=user.id, name="Sec-import test", description="")
    db_session.add(project)
    await db_session.flush()
    db_session.add(Membership(project_id=project.id, user_id=user.id, role=PROJECT_ROLE_OWNER))
    run = Run(
        project_id=project.id, created_by=user.id,
        name="import-run", status=RUN_STATUS_SUCCEEDED,
        record_count=0, match_count=0,
    )
    db_session.add(run)
    await db_session.commit()
    return {"user": user, "client": client, "project_id": project.id, "run_id": run.id}


def _json_file(payload: list | dict, name: str = "upload.json"):
    return (name, io.BytesIO(json.dumps(payload).encode()), "application/json")


def _csv_file(rows: list[dict], fieldnames: list[str], name: str = "upload.csv"):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\r\n")
    w.writeheader()
    for row in rows:
        w.writerow(row)
    return (name, io.BytesIO(buf.getvalue().encode("utf-8-sig")), "text/csv")


async def _post_file(client, url: str, file_tuple):
    name, data, ct = file_tuple
    return await client.post(url, files={"file": (name, data, ct)})


# ── Extraction import ─────────────────────────────────────────────────


class TestExtractionImport:
    @pytest.mark.asyncio
    async def test_json_import_creates_entities(self, run_ctx, db_session):
        from app.models.extraction_approval import ExtractionApproval
        from sqlalchemy import select

        run_id = run_ctx["run_id"]
        client = run_ctx["client"]

        payload = [
            {
                "control_number": "cn-imp-1",
                "source": "person_ner",
                "text": "Saadia Gaon",
                "start": 0, "end": 11,
                "type": "PERSON", "role": "author",
                "confidence": 0.88, "model_confidence": 0.91,
                "approved": True,
            }
        ]
        r = await _post_file(client, f"/api/runs/{run_id}/extraction/import", _json_file(payload))
        assert r.status_code == 200, r.text
        result = r.json()
        assert result["imported"] == 1
        assert result["skipped"] == 0
        assert result["errors"] == []

        rows = (await db_session.execute(
            select(ExtractionApproval).where(ExtractionApproval.run_id == run_id)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].text == "Saadia Gaon"
        assert rows[0].approved is True

    @pytest.mark.asyncio
    async def test_csv_import(self, run_ctx):
        run_id = run_ctx["run_id"]
        client = run_ctx["client"]
        rows = [{"control_number": "cn-csv", "source": "person_ner", "text": "Ibn Ezra",
                 "start": "0", "end": "8", "approved": "false"}]
        fields = ["control_number", "source", "text", "start", "end", "approved"]
        r = await _post_file(client, f"/api/runs/{run_id}/extraction/import",
                             _csv_file(rows, fields))
        assert r.status_code == 200
        assert r.json()["imported"] == 1

    @pytest.mark.asyncio
    async def test_invalid_row_accumulates_in_errors(self, run_ctx):
        run_id = run_ctx["run_id"]
        client = run_ctx["client"]
        payload = [
            {"control_number": "", "source": "x", "text": "bad row"},  # empty CN → error
            {"control_number": "cn-ok", "source": "person_ner", "text": "Good", "start": 0, "end": 4},
        ]
        r = await _post_file(client, f"/api/runs/{run_id}/extraction/import", _json_file(payload))
        result = r.json()
        assert result["imported"] == 1
        assert result["skipped"] == 1
        assert len(result["errors"]) == 1
        assert result["errors"][0]["row"] == 0

    @pytest.mark.asyncio
    async def test_idempotent_reimport_skips(self, run_ctx):
        run_id = run_ctx["run_id"]
        client = run_ctx["client"]
        payload = [{"control_number": "cn-idem", "source": "person_ner", "text": "Abarbanel",
                    "start": 0, "end": 9}]
        r1 = await _post_file(client, f"/api/runs/{run_id}/extraction/import", _json_file(payload))
        assert r1.json()["imported"] == 1
        # Re-import identical row — expect skipped=1 and no new DB row
        r2 = await _post_file(client, f"/api/runs/{run_id}/extraction/import", _json_file(payload))
        assert r2.json()["imported"] == 0
        assert r2.json()["skipped"] == 1

    @pytest.mark.asyncio
    async def test_requires_editor_role(self, run_ctx, db_session):
        from httpx import ASGITransport, AsyncClient
        from app.main import app
        run_id = run_ctx["run_id"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
            r = await anon.post(
                f"/api/runs/{run_id}/extraction/import",
                files={"file": ("f.json", io.BytesIO(b"[]"), "application/json")},
            )
        assert r.status_code == 401, r.text


# ── Authority import ──────────────────────────────────────────────────


class TestAuthorityImport:
    @pytest.mark.asyncio
    async def test_json_import_creates_matches(self, run_ctx, db_session):
        from app.models.run import AuthorityMatch
        from sqlalchemy import select

        run_id = run_ctx["run_id"]
        client = run_ctx["client"]
        payload = [
            {
                "control_number": "cn-auth",
                "entity_text": "Nachmanides",
                "entity_kind": "person",
                "role": "author",
                "wikidata_qid": "Q311953",
                "confidence": "high",
                "source": "wikidata",
                "approved": True,
            }
        ]
        r = await _post_file(client, f"/api/runs/{run_id}/authority/import", _json_file(payload))
        assert r.status_code == 200
        result = r.json()
        assert result["imported"] == 1

        rows = (await db_session.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].entity_text == "Nachmanides"
        assert rows[0].wikidata_qid == "Q311953"

    @pytest.mark.asyncio
    async def test_idempotent_reimport_skips(self, run_ctx):
        run_id = run_ctx["run_id"]
        client = run_ctx["client"]
        payload = [{"control_number": "cn-idem", "entity_text": "Ibn Rushd",
                    "entity_kind": "person", "role": "", "source": "viaf",
                    "confidence": "medium"}]
        r1 = await _post_file(client, f"/api/runs/{run_id}/authority/import", _json_file(payload))
        assert r1.json()["imported"] == 1
        r2 = await _post_file(client, f"/api/runs/{run_id}/authority/import", _json_file(payload))
        assert r2.json()["skipped"] == 1


# ── RDF import ────────────────────────────────────────────────────────


_MINIMAL_TTL = b"""
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<http://example.org/ms1> rdfs:label "Test Manuscript"@en .
"""

_INVALID_TTL = b"this is not valid turtle !!!"


class TestRdfImport:
    @pytest.mark.asyncio
    async def test_valid_ttl_is_accepted(self, run_ctx):
        run_id = run_ctx["run_id"]
        client = run_ctx["client"]
        r = await _post_file(
            client, f"/api/runs/{run_id}/rdf/import",
            ("graph.ttl", io.BytesIO(_MINIMAL_TTL), "text/turtle"),
        )
        assert r.status_code == 200, r.text
        result = r.json()
        assert result["imported"] == 1
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_invalid_ttl_is_rejected(self, run_ctx):
        run_id = run_ctx["run_id"]
        client = run_ctx["client"]
        r = await _post_file(
            client, f"/api/runs/{run_id}/rdf/import",
            ("bad.ttl", io.BytesIO(_INVALID_TTL), "text/turtle"),
        )
        assert r.status_code == 422, r.text

    @pytest.mark.asyncio
    async def test_file_written_to_disk(self, run_ctx):
        from app.pipeline.rdf_build import rdf_output_path_for_run
        run_id = run_ctx["run_id"]
        client = run_ctx["client"]
        r = await _post_file(
            client, f"/api/runs/{run_id}/rdf/import",
            ("g.ttl", io.BytesIO(_MINIMAL_TTL), "text/turtle"),
        )
        assert r.status_code == 200
        ttl_path = rdf_output_path_for_run(str(run_id))
        assert ttl_path.exists(), f"TTL not written to {ttl_path}"
        assert ttl_path.read_bytes() == _MINIMAL_TTL


# ── Wikidata Studio import ────────────────────────────────────────────


class TestWikidataStudioImport:
    @pytest.mark.asyncio
    async def test_json_import_creates_overrides(self, run_ctx, db_session):
        from app.models.item_override import WikidataItemOverride
        from sqlalchemy import select

        run_id = run_ctx["run_id"]
        client = run_ctx["client"]
        payload = [
            {
                "local_id": "cn-override",
                "labels": {"en": "Custom label"},
                "descriptions": {"en": "A note"},
                "add_statements": [{"property": "P31", "value": "Q571"}],
            }
        ]
        r = await _post_file(
            client, f"/api/runs/{run_id}/wikidata-studio/import", _json_file(payload),
        )
        assert r.status_code == 200, r.text
        assert r.json()["imported"] == 1

        rows = (await db_session.execute(
            select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].labels["en"] == "Custom label"

    @pytest.mark.asyncio
    async def test_idempotent_reimport(self, run_ctx):
        run_id = run_ctx["run_id"]
        client = run_ctx["client"]
        payload = [{"local_id": "cn-idem", "labels": {"en": "Label"}}]
        r1 = await _post_file(
            client, f"/api/runs/{run_id}/wikidata-studio/import", _json_file(payload),
        )
        assert r1.json()["imported"] == 1
        # Second import of identical data updates (not skip, because state changed to
        # the same value — test that it doesn't raise 500)
        r2 = await _post_file(
            client, f"/api/runs/{run_id}/wikidata-studio/import", _json_file(payload),
        )
        assert r2.status_code == 200
