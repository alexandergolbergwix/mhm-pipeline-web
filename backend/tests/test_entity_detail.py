"""Tests for the Entity detail endpoint (Feature 3 — Phase B).

GET /api/projects/{project_id}/research/entity?uri=<entity_uri>

Returns {uri, label, type, roles[], manuscripts[], dates?, geo?, identifiers{viaf,wikidata,…}}

Rules:
  - A person who is author in MS-A and owner in MS-B lists both roles and both manuscripts.
  - A place returns lat/lon coords from the graph.
  - A manuscript URI returns type="manuscript", its own label, no roles.
  - Identifiers (viaf/wikidata/...) are joined from the authority_matches table by
    matching the entity's label against matched_name.
  - Birth/death dates come from the authority_matches payload (VIAF/Wikidata P569/P570).
  - Unknown URI → 404 (not present in the merged graph).
  - Non-member → 403.
"""
from __future__ import annotations

import shutil

import pytest
import pytest_asyncio

_HM = "https://w3id.org/mhm/ontology#"
_WGS = "http://www.w3.org/2003/01/geo/wgs84_pos#"

# Seeded TTL for entity detail tests:
#   - Person <urn:person:maimonides>: author of MS-A, owner of MS-B
#   - Place  <urn:place:cairo>: production place of MS-A, has coords
#   - MS-A / MS-B: two manuscripts
_ENTITY_TTL = f"""\
@prefix hm:    <{_HM}> .
@prefix rdfs:  <http://www.w3.org/2000/01/rdf-schema#> .
@prefix wgs84: <{_WGS}> .

<urn:hm:MS_990000000000000001> a hm:Manuscript_Object ;
    rdfs:label "Manuscript A" ;
    hm:has_author <urn:person:maimonides> ;
    hm:has_production_place <urn:place:cairo> .

<urn:hm:MS_990000000000000002> a hm:Manuscript_Object ;
    rdfs:label "Manuscript B" ;
    hm:has_owner <urn:person:maimonides> .

<urn:person:maimonides> a hm:Person ;
    rdfs:label "Moses Maimonides" .

<urn:place:cairo> a hm:Place ;
    rdfs:label "Cairo" ;
    wgs84:lat "30.0444" ;
    wgs84:long "31.2357" .
"""


@pytest_asyncio.fixture
async def project_with_entities(sample_run):
    """Seed a TTL with persons, places, and manuscripts for entity-detail tests.

    Also seeds a second RunRecord + AuthorityMatch for MS-B so both
    manuscripts are resolvable via the DB.
    """
    from app.models.run import RUN_STATUS_SUCCEEDED, AuthorityMatch, Run, RunRecord
    from app.pipeline.rdf_build import rdf_output_path_for_run
    from app.pipeline.research_graph import _CACHE

    # Write the combined TTL
    run_id = str(sample_run["run_id"])
    ttl_path = rdf_output_path_for_run(run_id)
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_ENTITY_TTL, encoding="utf-8")
    _CACHE.clear()

    # sample_run already has RunRecord + AuthorityMatch for control_number
    # "990000000000000001" (MS-A with Maimonides as author, matched_name=
    # "Moses Maimonides", viaf_id="100185956", wikidata_qid="Q127398").
    # Nothing extra to seed — the conftest fixture already covers MS-A.

    try:
        yield sample_run
    finally:
        shutil.rmtree(ttl_path.parent, ignore_errors=True)
        _CACHE.clear()


def _url(project_id, uri: str) -> str:
    from urllib.parse import quote
    return f"/api/projects/{project_id}/research/entity?uri={quote(uri, safe='')}"


# ── basic shape ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_entity_detail_returns_expected_shape(project_with_entities):
    """Response always has uri, label, type, roles, manuscripts, identifiers."""
    client = project_with_entities["client"]
    resp = await client.get(_url(project_with_entities["project_id"], "urn:person:maimonides"))
    assert resp.status_code == 200
    body = resp.json()
    for key in ("uri", "label", "type", "roles", "manuscripts", "identifiers"):
        assert key in body, f"missing key: {key}"


@pytest.mark.asyncio
async def test_entity_detail_person_label_and_type(project_with_entities):
    """Person entity returns correct label and type='person'."""
    client = project_with_entities["client"]
    resp = await client.get(_url(project_with_entities["project_id"], "urn:person:maimonides"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["uri"] == "urn:person:maimonides"
    assert body["label"] == "Moses Maimonides"
    assert body["type"] == "person"


# ── roles & manuscripts ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_person_with_two_roles_lists_both(project_with_entities):
    """Maimonides is author in MS-A and owner in MS-B → both roles present."""
    client = project_with_entities["client"]
    resp = await client.get(_url(project_with_entities["project_id"], "urn:person:maimonides"))
    assert resp.status_code == 200
    body = resp.json()
    roles = body["roles"]
    assert "author" in roles
    assert "owner" in roles


@pytest.mark.asyncio
async def test_person_manuscripts_include_both(project_with_entities):
    """Manuscripts list contains both MS-A and MS-B."""
    client = project_with_entities["client"]
    resp = await client.get(_url(project_with_entities["project_id"], "urn:person:maimonides"))
    assert resp.status_code == 200
    ms_uris = {m["uri"] for m in resp.json()["manuscripts"]}
    assert "urn:hm:MS_990000000000000001" in ms_uris
    assert "urn:hm:MS_990000000000000002" in ms_uris


@pytest.mark.asyncio
async def test_manuscript_entries_carry_role(project_with_entities):
    """Each manuscript entry in the list includes the role for that entity."""
    client = project_with_entities["client"]
    resp = await client.get(_url(project_with_entities["project_id"], "urn:person:maimonides"))
    assert resp.status_code == 200
    by_uri = {m["uri"]: m for m in resp.json()["manuscripts"]}
    assert by_uri["urn:hm:MS_990000000000000001"]["role"] == "author"
    assert by_uri["urn:hm:MS_990000000000000002"]["role"] == "owner"


# ── place ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_place_entity_returns_coords(project_with_entities):
    """A place entity returns type='place' and geo with lat/lon."""
    client = project_with_entities["client"]
    resp = await client.get(_url(project_with_entities["project_id"], "urn:place:cairo"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "place"
    assert body["label"] == "Cairo"
    geo = body.get("geo")
    assert geo is not None
    assert abs(float(geo["lat"]) - 30.0444) < 0.001
    assert abs(float(geo["lon"]) - 31.2357) < 0.001


@pytest.mark.asyncio
async def test_place_entity_lists_manuscripts(project_with_entities):
    """Place returns the manuscripts it is a production place for."""
    client = project_with_entities["client"]
    resp = await client.get(_url(project_with_entities["project_id"], "urn:place:cairo"))
    assert resp.status_code == 200
    ms_uris = {m["uri"] for m in resp.json()["manuscripts"]}
    assert "urn:hm:MS_990000000000000001" in ms_uris


# ── identifiers from authority_matches ───────────────────────────────────

@pytest.mark.asyncio
async def test_person_identifiers_from_authority_matches(project_with_entities):
    """VIAF + Wikidata identifiers are resolved from the authority_matches table."""
    client = project_with_entities["client"]
    resp = await client.get(_url(project_with_entities["project_id"], "urn:person:maimonides"))
    assert resp.status_code == 200
    ids = resp.json()["identifiers"]
    # sample_run fixture seeds viaf_id="100185956", wikidata_qid="Q127398"
    # for entity_text="Maimonides" / matched_name="Moses Maimonides"
    assert ids.get("viaf") == "100185956"
    assert ids.get("wikidata") == "Q127398"


@pytest.mark.asyncio
async def test_person_dates_from_authority_payload(project_with_entities):
    """Birth/death years surface from the authority_matches payload."""
    client = project_with_entities["client"]
    resp = await client.get(_url(project_with_entities["project_id"], "urn:person:maimonides"))
    assert resp.status_code == 200
    dates = resp.json().get("dates")
    assert dates is not None
    assert dates.get("birth") == 1138
    assert dates.get("death") == 1204


# ── error cases ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_uri_returns_404(project_with_entities):
    """A URI not present in the merged graph → 404."""
    client = project_with_entities["client"]
    resp = await client.get(_url(project_with_entities["project_id"], "urn:person:nobody"))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_non_member_returns_403(project_with_entities, db_session, async_client):
    """A user who is not a project member cannot call the endpoint."""
    from app.auth import password as pw
    from app.auth.session import COOKIE_NAME, create_session
    from app.crypto import index as idx, kek as kek_mod, pii
    from app.models.user import ROLE_EDITOR, User
    import base64

    email = "outsider_entity@example.com"
    password = "Strong-Pass-456!"
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

    resp = await async_client.get(
        _url(project_with_entities["project_id"], "urn:person:maimonides")
    )
    assert resp.status_code == 403


# ── manuscript entity ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_manuscript_entity_type(project_with_entities):
    """A manuscript URI returns type='manuscript' and an empty roles list."""
    client = project_with_entities["client"]
    resp = await client.get(
        _url(project_with_entities["project_id"], "urn:hm:MS_990000000000000001")
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "manuscript"
    assert body["roles"] == []
