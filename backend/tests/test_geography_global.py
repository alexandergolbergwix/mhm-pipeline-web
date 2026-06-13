"""Tests for the global geography heatmap endpoint (Feature 5 — Phase B).

GET /api/projects/{project_id}/research/geography?mode=heatmap

Returns weighted geographic points:
  [{lat, lon, weight, type, place, place_label}]

where:
  - weight = number of manuscript associations for that place
  - type   = "production" | "mentioned" (or "both" if a place appears in both)
  - Each point has lat/lon from wgs84:lat / wgs84:long

Without ?mode=heatmap the existing endpoint is unchanged (backward compat).

Rules:
  - Production vs mention distinguishable via `type`.
  - Places with no coordinates are omitted from the heatmap result.
  - Non-member → 403 (unchanged from existing geography endpoint).
  - Empty graph → 200 [].
"""
from __future__ import annotations

import shutil
import pytest
import pytest_asyncio

_HM = "http://www.ontology.org.il/HebrewManuscripts/2025-12-06#"
_WGS = "http://www.w3.org/2003/01/geo/wgs84_pos#"

_GEO_TTL = f"""\
@prefix hm:    <{_HM}> .
@prefix rdfs:  <http://www.w3.org/2000/01/rdf-schema#> .
@prefix wgs84: <{_WGS}> .

# Place 1: production place for MS-A and MS-B
<urn:place:cairo> a hm:Place ; rdfs:label "Cairo" ;
    wgs84:lat "30.0444" ; wgs84:long "31.2357" .

# Place 2: mentioned in MS-A only
<urn:place:jerusalem> a hm:Place ; rdfs:label "Jerusalem" ;
    wgs84:lat "31.7683" ; wgs84:long "35.2137" .

# Place 3: production place only, but NO coordinates
<urn:place:unknown> a hm:Place ; rdfs:label "Unknown place" .

<urn:hm:MS_990000000000000001> a hm:Manuscript_Object ;
    rdfs:label "MS A" ;
    hm:has_production_place <urn:place:cairo> ;
    hm:mentions_place       <urn:place:jerusalem> .

<urn:hm:MS_990000000000000002> a hm:Manuscript_Object ;
    rdfs:label "MS B" ;
    hm:has_production_place <urn:place:cairo> ;
    hm:has_production_place <urn:place:unknown> .
"""


@pytest_asyncio.fixture
async def project_with_geo(sample_run):
    from app.pipeline.rdf_build import rdf_output_path_for_run
    from app.pipeline.research_graph import _CACHE

    run_id = str(sample_run["run_id"])
    ttl_path = rdf_output_path_for_run(run_id)
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_GEO_TTL, encoding="utf-8")
    _CACHE.clear()
    try:
        yield sample_run
    finally:
        shutil.rmtree(ttl_path.parent, ignore_errors=True)
        _CACHE.clear()


def _url(project_id, mode: str | None = None) -> str:
    base = f"/api/projects/{project_id}/research/geography"
    if mode:
        base += f"?mode={mode}"
    return base


# ── heatmap mode ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_heatmap_returns_list(project_with_geo):
    """?mode=heatmap returns a list of weighted points."""
    client = project_with_geo["client"]
    resp = await client.get(_url(project_with_geo["project_id"], mode="heatmap"))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_heatmap_point_shape(project_with_geo):
    """Each heatmap point has lat, lon, weight, type."""
    client = project_with_geo["client"]
    resp = await client.get(_url(project_with_geo["project_id"], mode="heatmap"))
    assert resp.status_code == 200
    points = resp.json()
    assert len(points) > 0
    p = points[0]
    for key in ("lat", "lon", "weight", "type"):
        assert key in p, f"missing key: {key}"


@pytest.mark.asyncio
async def test_heatmap_cairo_has_weight_2(project_with_geo):
    """Cairo is a production place for 2 MSS → weight = 2."""
    client = project_with_geo["client"]
    resp = await client.get(_url(project_with_geo["project_id"], mode="heatmap"))
    points = {p["place"]: p for p in resp.json()}
    assert "urn:place:cairo" in points
    assert points["urn:place:cairo"]["weight"] == 2


@pytest.mark.asyncio
async def test_heatmap_type_production_vs_mentioned(project_with_geo):
    """Production and mention places have distinguishable type values."""
    client = project_with_geo["client"]
    resp = await client.get(_url(project_with_geo["project_id"], mode="heatmap"))
    points = {p["place"]: p for p in resp.json()}
    assert points["urn:place:cairo"]["type"] == "production"
    assert points["urn:place:jerusalem"]["type"] == "mentioned"


@pytest.mark.asyncio
async def test_heatmap_omits_places_without_coords(project_with_geo):
    """Places with no wgs84 coordinates are excluded from heatmap results."""
    client = project_with_geo["client"]
    resp = await client.get(_url(project_with_geo["project_id"], mode="heatmap"))
    places = {p["place"] for p in resp.json()}
    assert "urn:place:unknown" not in places


@pytest.mark.asyncio
async def test_heatmap_coords_correct(project_with_geo):
    """Coordinates match the wgs84 values in the graph."""
    client = project_with_geo["client"]
    resp = await client.get(_url(project_with_geo["project_id"], mode="heatmap"))
    points = {p["place"]: p for p in resp.json()}
    cairo = points["urn:place:cairo"]
    assert abs(cairo["lat"] - 30.0444) < 0.001
    assert abs(cairo["lon"] - 31.2357) < 0.001


# ── backward compat: no mode = original response ──────────────────────────

@pytest.mark.asyncio
async def test_geography_without_mode_unchanged(project_with_geo):
    """Without ?mode, the original per-place aggregated response is returned."""
    client = project_with_geo["client"]
    resp = await client.get(_url(project_with_geo["project_id"]))
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    # Original format has ms_count key
    assert all("ms_count" in r for r in rows if r.get("lat") is not None)


# ── error cases ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_heatmap_non_member_returns_403(project_with_geo, db_session, async_client):
    """Non-member cannot call the heatmap endpoint."""
    from app.auth import password as pw
    from app.auth.session import COOKIE_NAME, create_session
    from app.crypto import index as idx, kek as kek_mod, pii
    from app.models.user import ROLE_EDITOR, User
    import base64

    email = "outsider_geo@example.com"
    password = "Strong-Pass-Geo-1!"
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

    resp = await async_client.get(_url(project_with_geo["project_id"], mode="heatmap"))
    assert resp.status_code == 403
