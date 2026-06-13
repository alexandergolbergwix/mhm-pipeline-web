"""Tests for Phase C / Feature 7 — cross-project / corpus-wide SPARQL.

GET /api/research/corpus/sparql   (POST with body {query: str})
  → {columns: [...], rows: [...]}   same shape as project-scoped SPARQL

Rules:
  - Federates over ALL projects the calling user is a member of.
  - A user in projects P1 and P2 sees triples from both.
  - A third project they're NOT a member of is excluded.
  - Same _validate_query (read-only + 1000-row/30s caps) as project SPARQL.
  - A write-query (INSERT/DELETE) → 400.
  - An unauthenticated request → 401.
  - No projects / empty graphs → 200 {columns:[], rows:[]}.
  - Results include a `_source_project` column indicating which project
    each row came from.

Implementation note: the endpoint is at /api/research/corpus/sparql and
is NOT project-scoped (no {project_id} in the path). It uses the user's
Membership rows to discover all accessible project graphs.
"""
from __future__ import annotations

import shutil
import uuid
import pytest
import pytest_asyncio

_HM = "http://www.ontology.org.il/HebrewManuscripts/2025-12-06#"

# ── small TTL for each project ─────────────────────────────────────────────

def _make_ttl(ms_label: str, person_label: str) -> str:
    return f"""\
@prefix hm:   <{_HM}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<urn:hm:{ms_label.replace(' ', '_')}> a hm:Manuscript_Object ; rdfs:label "{ms_label}" ;
    hm:has_author <urn:person:{person_label.replace(' ', '_')}> .
<urn:person:{person_label.replace(' ', '_')}> a hm:Person ; rdfs:label "{person_label}" .
"""


# ── fixtures: two member projects + one outsider project ──────────────────

@pytest_asyncio.fixture
async def corpus_setup(sample_run, async_client, db_session):
    """Create two additional projects/runs that the test user is a member of,
    plus one project the user is NOT in.  Returns the auth client and info."""
    from app.auth.session import COOKIE_NAME, create_session
    from app.crypto import index as idx, kek as kek_mod, pii
    from app.models.project import Membership, Project
    from app.models.run import Run
    from app.models.user import ROLE_EDITOR, User
    from app.auth import password as pw
    from app.pipeline.rdf_build import rdf_output_path_for_run
    from app.pipeline.research_graph import _CACHE
    import base64

    # The 'sample_run' fixture already has a project + run for the default test user.
    # Extract the run from sample_run.
    run_id_1 = str(sample_run["run_id"])
    project_id_1 = sample_run["project_id"]
    client = sample_run["client"]
    user_id = sample_run["user_id"]

    # Seed TTL for project 1
    ttl1 = rdf_output_path_for_run(run_id_1)
    ttl1.parent.mkdir(parents=True, exist_ok=True)
    ttl1.write_text(_make_ttl("MS Alpha", "Author Alpha"), encoding="utf-8")

    # Create a second project for the SAME user
    proj2 = Project(owner_id=user_id, name="Second Corpus Project", description="")
    db_session.add(proj2)
    await db_session.flush()
    run2 = Run(project_id=proj2.id, created_by=user_id, name="Corpus run 2", status="completed")
    db_session.add(run2)
    await db_session.flush()
    from app.models.project import PROJECT_ROLE_OWNER
    mem2 = Membership(project_id=proj2.id, user_id=user_id, role=PROJECT_ROLE_OWNER)
    db_session.add(mem2)
    await db_session.commit()

    run_id_2 = str(run2.id)
    ttl2 = rdf_output_path_for_run(run_id_2)
    ttl2.parent.mkdir(parents=True, exist_ok=True)
    ttl2.write_text(_make_ttl("MS Beta", "Author Beta"), encoding="utf-8")

    # Create an outsider project (no membership for our user) — use a temp owner
    # We need a real user as owner, so reuse user_id (that's fine; the point is no Membership)
    proj_out = Project(owner_id=user_id, name="Outsider Project", description="")
    db_session.add(proj_out)
    await db_session.flush()
    run_out = Run(project_id=proj_out.id, created_by=user_id, name="Outsider run", status="completed")
    db_session.add(run_out)
    await db_session.flush()
    await db_session.commit()

    run_id_out = str(run_out.id)
    ttl_out = rdf_output_path_for_run(run_id_out)
    ttl_out.parent.mkdir(parents=True, exist_ok=True)
    ttl_out.write_text(_make_ttl("MS Gamma", "Author Gamma"), encoding="utf-8")

    _CACHE.clear()

    try:
        yield {
            "client":      client,
            "project_id_1": str(project_id_1),
            "project_id_2": str(proj2.id),
            "project_id_out": str(proj_out.id),
            "user_id":     sample_run["user_id"],
        }
    finally:
        for path in [ttl1, ttl2, ttl_out]:
            shutil.rmtree(path.parent, ignore_errors=True)
        _CACHE.clear()


_CORPUS_URL = "/api/research/corpus/sparql"
_ALL_MS_QUERY = """
SELECT ?ms ?label WHERE {
  ?ms a <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#Manuscript_Object> .
  ?ms <http://www.w3.org/2000/01/rdf-schema#label> ?label .
}
"""


# ── basic shape ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_corpus_sparql_returns_columns_and_rows(corpus_setup):
    """Corpus SPARQL returns {columns, rows}."""
    client = corpus_setup["client"]
    resp = await client.post(_CORPUS_URL, json={"query": _ALL_MS_QUERY})
    assert resp.status_code == 200
    data = resp.json()
    assert "columns" in data
    assert "rows" in data


@pytest.mark.asyncio
async def test_corpus_includes_both_member_projects(corpus_setup):
    """MS Alpha (project 1) and MS Beta (project 2) are both returned."""
    client = corpus_setup["client"]
    resp = await client.post(_CORPUS_URL, json={"query": _ALL_MS_QUERY})
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    labels = [r.get("label") for r in rows]
    assert any("Alpha" in str(l) for l in labels), f"MS Alpha not found in {labels}"
    assert any("Beta" in str(l) for l in labels),  f"MS Beta not found in {labels}"


@pytest.mark.asyncio
async def test_corpus_excludes_non_member_project(corpus_setup):
    """MS Gamma belongs to a project the user is NOT a member of → excluded."""
    client = corpus_setup["client"]
    resp = await client.post(_CORPUS_URL, json={"query": _ALL_MS_QUERY})
    labels = [r.get("label") for r in resp.json()["rows"]]
    assert not any("Gamma" in str(l) for l in labels), f"MS Gamma should be excluded but found in {labels}"


@pytest.mark.asyncio
async def test_corpus_source_project_column(corpus_setup):
    """Each row includes _source_project indicating which project it came from."""
    client = corpus_setup["client"]
    resp = await client.post(_CORPUS_URL, json={"query": _ALL_MS_QUERY})
    data = resp.json()
    assert "_source_project" in data["columns"]
    for row in data["rows"]:
        assert "_source_project" in row
        assert row["_source_project"]  # non-empty


@pytest.mark.asyncio
async def test_corpus_source_projects_are_correct(corpus_setup):
    """MS Alpha rows have project_id_1, MS Beta rows have project_id_2."""
    client = corpus_setup["client"]
    resp = await client.post(_CORPUS_URL, json={"query": _ALL_MS_QUERY})
    rows = resp.json()["rows"]
    alpha_row = next((r for r in rows if "Alpha" in str(r.get("label", ""))), None)
    beta_row  = next((r for r in rows if "Beta"  in str(r.get("label", ""))), None)
    assert alpha_row is not None
    assert beta_row  is not None
    assert alpha_row["_source_project"] == corpus_setup["project_id_1"]
    assert beta_row["_source_project"]  == corpus_setup["project_id_2"]


# ── safety: same caps as project SPARQL ───────────────────────────────────

@pytest.mark.asyncio
async def test_corpus_rejects_write_query(corpus_setup):
    """INSERT/DELETE queries are rejected with 400."""
    client = corpus_setup["client"]
    resp = await client.post(
        _CORPUS_URL,
        json={"query": "INSERT DATA { <urn:x> <urn:p> <urn:o> }"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_corpus_rejects_unauthenticated(async_client):
    """Unauthenticated request → 401."""
    resp = await async_client.post(_CORPUS_URL, json={"query": _ALL_MS_QUERY})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_corpus_empty_membership_returns_empty(sample_run, db_session, async_client):
    """A user with no member projects sees an empty result, not an error."""
    from app.auth import password as pw
    from app.auth.session import COOKIE_NAME, create_session
    from app.crypto import index as idx, kek as kek_mod, pii
    from app.models.user import ROLE_EDITOR, User
    import base64

    email = "loner_corpus@example.com"
    password = "Strong-Pass-Loner-1!"
    loner = User(
        email_index=idx.blind_index(email),
        email_encrypted=pii.encrypt_pii(email),
        name_encrypted=pii.encrypt_pii("Loner"),
        password_hash=pw.hash_password(password),
        kek_salt=pii.random_bytes(16),
        role=ROLE_EDITOR,
    )
    db_session.add(loner)
    await db_session.commit()
    kek = kek_mod.derive_kek(password, salt=loner.kek_salt)
    row, secret = await create_session(db_session, user=loner, kek=kek)
    await db_session.commit()
    cookie = f"{row.id}.{base64.urlsafe_b64encode(secret).decode().rstrip('=')}"
    async_client.cookies.set(COOKIE_NAME, cookie)

    resp = await async_client.post(_CORPUS_URL, json={"query": _ALL_MS_QUERY})
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows"] == []
