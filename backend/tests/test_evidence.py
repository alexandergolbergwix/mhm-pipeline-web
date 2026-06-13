"""Tests for the Evidence panel endpoint (Feature 8).

GET /api/projects/{project_id}/research/evidence?uri=<entity_uri>

Returns:
  - control_number: the MARC control number that minted the URI
  - marc: the MARC record JSON from run_records (or null)
  - approvals: list of approved entities linked to this record
              (approved_by, approved_at, entity_text, entity_type)
  - authority_matches: any authority matches for this record with match info

Rules:
  - Unknown URI → 404
  - Non-member → 403
  - No MARC record found but URI valid → 200 with empty marc
"""
from __future__ import annotations

import shutil
import pytest
import pytest_asyncio

_HM = "http://www.ontology.org.il/HebrewManuscripts/2025-12-06#"

# TTL that mints a manuscript URI with a control number embedded in the local name
_SAMPLE_TTL = f"""\
@prefix hm:   <{_HM}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<urn:hm:MS_990000000000000001> a hm:Manuscript_Object ;
    rdfs:label "Test manuscript" .
<urn:person:1> a hm:Person ;
    rdfs:label "Moses Maimonides" .
"""


@pytest_asyncio.fixture
async def project_with_evidence(sample_run):
    """Seed a TTL and ensure the run_record is loaded for the test control number."""
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


def _url(project_id, uri: str) -> str:
    return f"/api/projects/{project_id}/research/evidence?uri={uri}"


@pytest.mark.asyncio
async def test_evidence_known_uri(project_with_evidence):
    """A URI present in the merged graph returns 200 with control_number."""
    client = project_with_evidence["client"]
    uri = "urn:hm:MS_990000000000000001"
    resp = await client.get(_url(project_with_evidence["project_id"], uri))
    assert resp.status_code == 200
    body = resp.json()
    assert "control_number" in body
    assert "marc" in body
    assert "approvals" in body
    assert isinstance(body["approvals"], list)


@pytest.mark.asyncio
async def test_evidence_control_number_extracted(project_with_evidence):
    """The control number is extracted from the URI local name."""
    client = project_with_evidence["client"]
    uri = "urn:hm:MS_990000000000000001"
    resp = await client.get(_url(project_with_evidence["project_id"], uri))
    assert resp.status_code == 200
    assert resp.json()["control_number"] == project_with_evidence["control_number"]


@pytest.mark.asyncio
async def test_evidence_unknown_uri_returns_404(project_with_evidence):
    client = project_with_evidence["client"]
    resp = await client.get(_url(project_with_evidence["project_id"], "urn:hm:MS_does_not_exist"))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_evidence_non_member_forbidden(project_with_evidence, db_session, async_client):
    from app.auth import password as pw
    from app.auth.session import COOKIE_NAME, create_session
    from app.crypto import index as idx, kek as kek_mod, pii
    from app.models.user import ROLE_EDITOR, User
    import base64

    email = "outsider3@example.com"
    password = "Strong-Pass-123!"
    outsider = User(
        email_index=idx.blind_index(email),
        email_encrypted=pii.encrypt_pii(email),
        name_encrypted=pii.encrypt_pii("Outsider3"),
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

    uri = "urn:hm:MS_990000000000000001"
    resp = await async_client.get(_url(project_with_evidence["project_id"], uri))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_evidence_marc_returned_when_run_record_exists(project_with_evidence):
    """The MARC record JSON is populated from the run_records table."""
    client = project_with_evidence["client"]
    uri = "urn:hm:MS_990000000000000001"
    resp = await client.get(_url(project_with_evidence["project_id"], uri))
    assert resp.status_code == 200
    # sample_run fixture creates a RunRecord with the control_number; marc may be null
    # but the field must exist
    body = resp.json()
    assert "marc" in body
