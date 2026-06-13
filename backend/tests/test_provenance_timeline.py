"""Tests for the Provenance-chain timeline endpoint (Feature 4 — Phase B).

GET /api/projects/{project_id}/research/provenance?ms=<manuscript_uri>

Returns ordered provenance events for a manuscript:
  {
    ms: str,
    ms_label: str | None,
    events: [
      {
        type: "production" | "ownership" | "current_holder",
        label: str,
        uri: str | None,
        year: int | None,
        year_earliest: int | None,
        year_latest: int | None,
        place: str | None,
      }
    ]
  }

Optional query param: ?overlay=lifespans
  When set, each person event includes owner_birth / owner_death from the
  authority_matches payload.

Rules:
  - Unknown manuscript URI → 404 (URI not in graph, or no provenance)
  - No ms param → 422
  - Non-member → 403
  - Manuscript with no production date and no owners → 200, events=[]
"""
from __future__ import annotations

import shutil
import pytest
import pytest_asyncio

_HM = "http://www.ontology.org.il/HebrewManuscripts/2025-12-06#"

_PROVENANCE_TTL = f"""\
@prefix hm:    <{_HM}> .
@prefix rdfs:  <http://www.w3.org/2000/01/rdf-schema#> .

# Manuscript with a production event (date + place) and two owners
<urn:hm:MS_990000000000000001> a hm:Manuscript_Object ;
    rdfs:label "Mishneh Torah" ;
    hm:has_production_date_certain "1200" ;
    hm:has_production_place <urn:place:cairo> ;
    hm:has_owner <urn:person:maimonides> ;
    hm:has_owner <urn:person:patron1> .

<urn:place:cairo>       a hm:Place      ; rdfs:label "Cairo" .
<urn:person:maimonides> a hm:Person     ; rdfs:label "Moses Maimonides" .
<urn:person:patron1>    a hm:Person     ; rdfs:label "Anonymous patron" .

# Manuscript with only a date range (no production event node)
<urn:hm:MS_990000000000000002> a hm:Manuscript_Object ;
    rdfs:label "Another MS" ;
    hm:earliest_possible_date "1100" ;
    hm:latest_possible_date   "1200" .

# Manuscript with no dates or owners at all
<urn:hm:MS_990000000000000003> a hm:Manuscript_Object ;
    rdfs:label "Empty MS" .
"""


@pytest_asyncio.fixture
async def project_with_provenance(sample_run, db_session):
    """Seed a TTL with provenance data; also seed a second RunRecord for MS-2."""
    from app.models.run import RunRecord
    from app.pipeline.rdf_build import rdf_output_path_for_run
    from app.pipeline.research_graph import _CACHE

    run_id = str(sample_run["run_id"])
    ttl_path = rdf_output_path_for_run(run_id)
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_PROVENANCE_TTL, encoding="utf-8")
    _CACHE.clear()

    # Seed a RunRecord for MS-2 so the endpoint can look it up
    rec2 = RunRecord(
        run_id=sample_run["run_id"],
        control_number="990000000000000002",
        marc={"_control_number": "990000000000000002"},
    )
    db_session.add(rec2)
    await db_session.commit()

    try:
        yield sample_run
    finally:
        shutil.rmtree(ttl_path.parent, ignore_errors=True)
        _CACHE.clear()


def _url(project_id, ms_uri: str, overlay: str | None = None) -> str:
    from urllib.parse import quote
    base = f"/api/projects/{project_id}/research/provenance?ms={quote(ms_uri, safe='')}"
    if overlay:
        base += f"&overlay={overlay}"
    return base


# ── basic shape ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provenance_returns_expected_shape(project_with_provenance):
    """Response has ms, ms_label, and events list."""
    client = project_with_provenance["client"]
    resp = await client.get(_url(
        project_with_provenance["project_id"],
        "urn:hm:MS_990000000000000001",
    ))
    assert resp.status_code == 200
    body = resp.json()
    assert "ms" in body
    assert "ms_label" in body
    assert "events" in body
    assert isinstance(body["events"], list)


@pytest.mark.asyncio
async def test_provenance_ms_uri_and_label(project_with_provenance):
    """ms and ms_label are correctly populated."""
    client = project_with_provenance["client"]
    resp = await client.get(_url(
        project_with_provenance["project_id"],
        "urn:hm:MS_990000000000000001",
    ))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ms"] == "urn:hm:MS_990000000000000001"
    assert body["ms_label"] == "Mishneh Torah"


# ── production event ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provenance_contains_production_event(project_with_provenance):
    """A manuscript with hm:has_production_date_certain includes a 'production' event."""
    client = project_with_provenance["client"]
    resp = await client.get(_url(
        project_with_provenance["project_id"],
        "urn:hm:MS_990000000000000001",
    ))
    assert resp.status_code == 200
    events = resp.json()["events"]
    types = [e["type"] for e in events]
    assert "production" in types


@pytest.mark.asyncio
async def test_provenance_production_event_has_year(project_with_provenance):
    """The production event carries the year from hm:has_production_date_certain."""
    client = project_with_provenance["client"]
    resp = await client.get(_url(
        project_with_provenance["project_id"],
        "urn:hm:MS_990000000000000001",
    ))
    prod = next(e for e in resp.json()["events"] if e["type"] == "production")
    assert prod["year"] == 1200


@pytest.mark.asyncio
async def test_provenance_date_range_events(project_with_provenance):
    """A manuscript with only earliest/latest dates returns a production event with bounds."""
    client = project_with_provenance["client"]
    resp = await client.get(_url(
        project_with_provenance["project_id"],
        "urn:hm:MS_990000000000000002",
    ))
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert len(events) > 0
    prod = next((e for e in events if e["type"] == "production"), None)
    assert prod is not None
    assert prod["year_earliest"] == 1100
    assert prod["year_latest"] == 1200


# ── ownership events ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provenance_includes_ownership_events(project_with_provenance):
    """Each hm:has_owner becomes an 'ownership' event."""
    client = project_with_provenance["client"]
    resp = await client.get(_url(
        project_with_provenance["project_id"],
        "urn:hm:MS_990000000000000001",
    ))
    events = resp.json()["events"]
    ownership = [e for e in events if e["type"] == "ownership"]
    assert len(ownership) == 2


@pytest.mark.asyncio
async def test_provenance_ownership_event_has_label_and_uri(project_with_provenance):
    """Ownership events carry a human-readable label and the person URI."""
    client = project_with_provenance["client"]
    resp = await client.get(_url(
        project_with_provenance["project_id"],
        "urn:hm:MS_990000000000000001",
    ))
    owners = {e["uri"]: e for e in resp.json()["events"] if e["type"] == "ownership"}
    assert "urn:person:maimonides" in owners
    assert owners["urn:person:maimonides"]["label"] == "Moses Maimonides"


# ── lifespan overlay ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provenance_overlay_lifespans_adds_dates(project_with_provenance):
    """?overlay=lifespans adds owner_birth / owner_death from authority_matches payload."""
    client = project_with_provenance["client"]
    resp = await client.get(_url(
        project_with_provenance["project_id"],
        "urn:hm:MS_990000000000000001",
        overlay="lifespans",
    ))
    assert resp.status_code == 200
    events = resp.json()["events"]
    maimonides_event = next(
        (e for e in events if e.get("uri") == "urn:person:maimonides"),
        None,
    )
    assert maimonides_event is not None
    # sample_run fixture has payload with birth_year=1138, death_year=1204
    assert maimonides_event.get("owner_birth") == 1138
    assert maimonides_event.get("owner_death") == 1204


# ── empty manuscript ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provenance_empty_manuscript_returns_empty_events(project_with_provenance):
    """A manuscript with no dates and no owners returns 200 with events=[]."""
    client = project_with_provenance["client"]
    resp = await client.get(_url(
        project_with_provenance["project_id"],
        "urn:hm:MS_990000000000000003",
    ))
    assert resp.status_code == 200
    assert resp.json()["events"] == []


# ── error cases ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provenance_unknown_ms_returns_404(project_with_provenance):
    """An MS URI not in the graph → 404."""
    client = project_with_provenance["client"]
    resp = await client.get(_url(
        project_with_provenance["project_id"],
        "urn:hm:MS_does_not_exist",
    ))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_provenance_non_member_returns_403(project_with_provenance, db_session, async_client):
    """Non-member cannot access the endpoint."""
    from app.auth import password as pw
    from app.auth.session import COOKIE_NAME, create_session
    from app.crypto import index as idx, kek as kek_mod, pii
    from app.models.user import ROLE_EDITOR, User
    import base64

    email = "outsider_prov@example.com"
    password = "Strong-Pass-789!"
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

    resp = await async_client.get(_url(
        project_with_provenance["project_id"],
        "urn:hm:MS_990000000000000001",
    ))
    assert resp.status_code == 403
