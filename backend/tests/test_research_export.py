"""Tests for the SPARQL result export endpoint (Feature 1).

``POST /api/projects/{project_id}/research/sparql/export`` runs a read-only
SPARQL query against the project's merged HMO graph and streams the result
rows in one of four scholarly formats: csv, json, bibtex, ris.

The export reuses the same query validation + graph-loading path as the
SPARQL console, so the read-only guard (no INSERT/DELETE) and the project
membership guard are exercised here too.

RDF is seeded by writing a tiny TTL to the canonical per-run path; the
``_run_dir`` cleanup fixture removes it after each test so the repo state
dir is left clean.
"""
from __future__ import annotations

import shutil

import pytest
import pytest_asyncio

_HM = "http://www.ontology.org.il/HebrewManuscripts/2025-12-06#"

_SAMPLE_TTL = f"""\
@prefix hm:   <{_HM}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<urn:ms:1> a hm:Manuscript_Object ;
    rdfs:label "Mishneh Torah copy" .
<urn:person:1> a hm:Person ;
    rdfs:label "Moses Maimonides" .
"""

_SELECT = (
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
    "SELECT ?s ?label WHERE { ?s rdfs:label ?label } ORDER BY ?label"
)


@pytest_asyncio.fixture
async def project_with_rdf(sample_run):
    """Seed a TTL for the sample_run's run so the merged graph is non-empty."""
    from app.pipeline.rdf_build import rdf_output_path_for_run
    from app.pipeline.research_graph import _CACHE

    run_id = str(sample_run["run_id"])
    ttl_path = rdf_output_path_for_run(run_id)
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_SAMPLE_TTL, encoding="utf-8")
    _CACHE.clear()
    try:
        yield sample_run
    finally:
        shutil.rmtree(ttl_path.parent, ignore_errors=True)
        _CACHE.clear()


def _url(project_id) -> str:
    return f"/api/projects/{project_id}/research/sparql/export"


@pytest.mark.asyncio
async def test_export_json(project_with_rdf):
    client = project_with_rdf["client"]
    resp = await client.post(
        _url(project_with_rdf["project_id"]),
        json={"query": _SELECT, "format": "json"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert "attachment" in resp.headers.get("content-disposition", "")
    body = resp.json()
    assert body["columns"] == ["s", "label"]
    assert any("Maimonides" in (cell or "") for row in body["rows"] for cell in row)


@pytest.mark.asyncio
async def test_export_csv(project_with_rdf):
    client = project_with_rdf["client"]
    resp = await client.post(
        _url(project_with_rdf["project_id"]),
        json={"query": _SELECT, "format": "csv"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    text = resp.text
    assert text.splitlines()[0] == "s,label"
    assert "Moses Maimonides" in text


@pytest.mark.asyncio
async def test_export_bibtex(project_with_rdf):
    client = project_with_rdf["client"]
    resp = await client.post(
        _url(project_with_rdf["project_id"]),
        json={"query": _SELECT, "format": "bibtex"},
    )
    assert resp.status_code == 200
    assert "application/x-bibtex" in resp.headers["content-type"]
    assert "@misc{" in resp.text


@pytest.mark.asyncio
async def test_export_ris(project_with_rdf):
    client = project_with_rdf["client"]
    resp = await client.post(
        _url(project_with_rdf["project_id"]),
        json={"query": _SELECT, "format": "ris"},
    )
    assert resp.status_code == 200
    assert "application/x-research-info-systems" in resp.headers["content-type"]
    assert "TY  - " in resp.text
    assert "ER  - " in resp.text


@pytest.mark.asyncio
async def test_bad_format_rejected(project_with_rdf):
    client = project_with_rdf["client"]
    resp = await client.post(
        _url(project_with_rdf["project_id"]),
        json={"query": _SELECT, "format": "xml"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_write_query_rejected(project_with_rdf):
    client = project_with_rdf["client"]
    resp = await client.post(
        _url(project_with_rdf["project_id"]),
        json={"query": "INSERT DATA { <urn:a> <urn:b> <urn:c> }", "format": "json"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_non_member_forbidden(project_with_rdf, db_session, async_client):
    """A logged-in user who is not a member of the project gets 403."""
    from app.auth import password as pw
    from app.auth.session import COOKIE_NAME, create_session
    from app.crypto import index as idx
    from app.crypto import kek as kek_mod
    from app.crypto import pii
    from app.models.user import ROLE_EDITOR, User
    import base64

    email = "outsider@example.com"
    password = "Another-Strong-Pass-9!"
    outsider = User(
        email_index=idx.blind_index(email),
        email_encrypted=pii.encrypt_pii(email),
        name_encrypted=pii.encrypt_pii("Outsider"),
        password_hash=pw.hash_password(password),
        kek_salt=pii.random_bytes(16),
        role=ROLE_EDITOR,
    )
    db_session.add(outsider)
    await db_session.commit()
    kek = kek_mod.derive_kek(password, salt=outsider.kek_salt)
    row, secret = await create_session(db_session, user=outsider, kek=kek)
    await db_session.commit()
    cookie = f"{row.id}.{base64.urlsafe_b64encode(secret).decode().rstrip('=')}"
    async_client.cookies.set(COOKIE_NAME, cookie)

    resp = await async_client.post(
        _url(project_with_rdf["project_id"]),
        json={"query": _SELECT, "format": "json"},
    )
    assert resp.status_code == 403
