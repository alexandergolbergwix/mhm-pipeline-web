"""Tests for RDF artifact persistence (migration 0022).

Two behaviours under test:

1. POST /rdf/build persists the compiled TTL to the rdf_artifacts table
   so the graph survives dyno restarts.

2. GET /rdf/graph restores the TTL from Postgres to the local cache path
   when the file is absent (simulating a post-deploy cold dyno).

``build_rdf_graph`` is patched to avoid running the full rdflib pipeline
against the SQLite test DB — we only care about the persistence layer.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def rdf_run(db_session, auth_user):
    """A project + run + one RunRecord, similar to sample_run but with
    no authority match (the RDF build stub doesn't need it)."""
    from app.models.project import PROJECT_ROLE_OWNER, Membership, Project
    from app.models.run import RUN_STATUS_SUCCEEDED, Run, RunRecord

    user, client = auth_user

    project = Project(owner_id=user.id, name="RDF test project", description="")
    db_session.add(project)
    await db_session.flush()
    db_session.add(
        Membership(project_id=project.id, user_id=user.id, role=PROJECT_ROLE_OWNER),
    )

    run = Run(
        project_id=project.id,
        created_by=user.id,
        name="RDF test run",
        status=RUN_STATUS_SUCCEEDED,
        record_count=1,
        match_count=0,
    )
    db_session.add(run)
    await db_session.flush()

    record = db_session.add(RunRecord(
        run_id=run.id,
        control_number="CN001",
        marc={"_control_number": "CN001", "contributors": []},
    ))
    await db_session.commit()

    return {"run_id": run.id, "client": client}


# ── Helpers ────────────────────────────────────────────────────────────


def _fake_build_result(out_path: Path, ttl_text: str = "@prefix ex: <urn:ex:> .\n") -> MagicMock:
    """Simulate build_rdf_graph: write a minimal TTL and return a result."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(ttl_text, encoding="utf-8")
    result = MagicMock()
    result.triples_count = 3
    result.manuscripts_count = 1
    result.to_dict.return_value = {
        "triples_count": 3,
        "manuscripts_count": 1,
        "output_path": str(out_path),
        "started_at": "2026-01-01T00:00:00",
        "finished_at": "2026-01-01T00:00:01",
        "mapping_errors": [],
    }
    return result


# ── Tests ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_persists_ttl_to_db(rdf_run, db_session, tmp_path):
    from app.models.rdf_artifact import RdfArtifact

    run_id = rdf_run["run_id"]
    client = rdf_run["client"]
    fake_ttl = "@prefix ex: <urn:ex:> .\nex:a ex:b ex:c .\n"
    ttl_path = tmp_path / str(run_id) / "manuscripts.ttl"

    with (
        patch("app.routers.rdf.rdf_output_path_for_run", return_value=ttl_path),
        patch(
            "app.routers.rdf.build_rdf_graph",
            new_callable=AsyncMock,
            side_effect=lambda **_kw: _fake_build_result(ttl_path, fake_ttl),
        ),
    ):
        resp = await client.post(f"/api/runs/{run_id}/rdf/build")

    assert resp.status_code == 200, resp.text

    row = await db_session.get(RdfArtifact, run_id)
    assert row is not None, "RdfArtifact row should be created after build"
    assert row.ttl_content == fake_ttl
    assert row.triples_count == 3
    assert row.manuscripts_count == 1


@pytest.mark.asyncio
async def test_rebuild_updates_existing_artifact(rdf_run, db_session, tmp_path):
    from app.models.rdf_artifact import RdfArtifact

    run_id = rdf_run["run_id"]
    client = rdf_run["client"]
    ttl_path = tmp_path / str(run_id) / "manuscripts.ttl"

    for ttl_text in ["@prefix ex: <urn:ex:> .\nex:a ex:b ex:c .\n",
                     "@prefix ex: <urn:ex:> .\nex:x ex:y ex:z .\n"]:
        with (
            patch("app.routers.rdf.rdf_output_path_for_run", return_value=ttl_path),
            patch(
                "app.routers.rdf.build_rdf_graph",
                new_callable=AsyncMock,
                side_effect=lambda **_kw: _fake_build_result(ttl_path, ttl_text),
            ),
        ):
            resp = await client.post(f"/api/runs/{run_id}/rdf/build")
        assert resp.status_code == 200

    row = await db_session.get(RdfArtifact, run_id)
    assert row is not None
    assert "ex:z" in row.ttl_content, "Second build should overwrite the first"


@pytest.mark.asyncio
async def test_graph_endpoint_restores_ttl_from_db(rdf_run, db_session, tmp_path):
    """Simulate a cold dyno: local file absent, DB row present.
    GET /rdf/graph should restore the file and return 200."""
    from app.models.rdf_artifact import RdfArtifact

    run_id = rdf_run["run_id"]
    client = rdf_run["client"]

    # Seed the DB row directly (no build endpoint call).
    minimal_ttl = "@prefix ex: <urn:ex:> .\n"
    db_session.add(RdfArtifact(
        run_id=run_id,
        ttl_content=minimal_ttl,
        triples_count=0,
        manuscripts_count=0,
    ))
    await db_session.commit()

    ttl_path = tmp_path / str(run_id) / "manuscripts.ttl"
    assert not ttl_path.exists(), "Pre-condition: no local file"

    # load_graph and graph_to_cytoscape_json are also mocked so rdflib
    # doesn't need a valid TTL to parse.
    mock_graph = MagicMock()
    mock_graph.__iter__ = MagicMock(return_value=iter([]))
    cytoscape_payload = {"nodes": [], "edges": []}

    with (
        patch("app.routers.rdf.rdf_output_path_for_run", return_value=ttl_path),
        patch("app.routers.rdf.load_graph", return_value=mock_graph),
        patch("app.routers.rdf.graph_to_cytoscape_json", return_value=cytoscape_payload),
        patch("app.routers.rdf.compute_layout", return_value=cytoscape_payload),
    ):
        resp = await client.get(f"/api/runs/{run_id}/rdf/graph")

    assert resp.status_code == 200, resp.text
    assert ttl_path.exists(), "_ensure_ttl_on_disk should have written the file"
    assert ttl_path.read_text() == minimal_ttl


@pytest.mark.asyncio
async def test_graph_endpoint_returns_404_when_no_artifact(rdf_run, tmp_path):
    """No local file and no DB row → 404 with clear message."""
    run_id = rdf_run["run_id"]
    client = rdf_run["client"]
    ttl_path = tmp_path / str(run_id) / "manuscripts.ttl"

    with patch("app.routers.rdf.rdf_output_path_for_run", return_value=ttl_path):
        resp = await client.get(f"/api/runs/{run_id}/rdf/graph")

    assert resp.status_code == 404
    assert "build" in resp.json()["detail"].lower()
