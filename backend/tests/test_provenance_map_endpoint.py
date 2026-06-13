"""Endpoint tests for the provenance movement map + manuscripts picker.

GET /api/projects/{id}/research/manuscripts
GET /api/projects/{id}/research/provenance-map?cn=<control_number>

DB-sourced (run_records + authority_matches), Redis/Postgres cached.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def project_with_map_data(sample_run, db_session):
    """Add a production-place KIMA match + an owner match to the sample run."""
    from app.models.run import AuthorityMatch, RunRecord

    run_id = sample_run["run_id"]

    # Update the seeded record to carry a production place + date.
    rec = (
        await db_session.execute(
            __import__("sqlalchemy").select(RunRecord).where(
                RunRecord.control_number == "990000000000000001",
            )
        )
    ).scalar_one()
    rec.marc = {
        "_control_number": "990000000000000001",
        "title": "Mishneh Torah",
        "place": "Sanaa",
        "dates": {"year": 1500},
        "contributors": [{"name": "Owner One", "role": "former owner"}],
    }

    # KIMA production-place match.
    db_session.add(AuthorityMatch(
        run_id=run_id, control_number="990000000000000001",
        entity_text="Sanaa", entity_kind="place", role="production place",
        matched_name="Sanaa", mazal_id="", viaf_id="", wikidata_qid="Q5806",
        confidence="high", source="kima",
        payload={"kima_lat": 15.35, "kima_lon": 44.2, "kima_id": 7, "wikidata_id": "Q5806"},
        approved=True,
    ))
    # Owner person match (approved, high, with lifespan + qid).
    db_session.add(AuthorityMatch(
        run_id=run_id, control_number="990000000000000001",
        entity_text="Owner One", entity_kind="person", role="former owner",
        matched_name="Owner One", mazal_id="", viaf_id="", wikidata_qid="Q111",
        confidence="high", source="wikidata",
        payload={"birth_year": 1480, "death_year": 1540},
        approved=True,
    ))
    await db_session.commit()
    return sample_run


def _map_url(project_id, cn: str, *, unapproved: bool = False) -> str:
    base = f"/api/projects/{project_id}/research/provenance-map?cn={cn}"
    if unapproved:
        base += "&include_unapproved=true"
    return base


# ── manuscripts picker ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_manuscripts_lists_records(project_with_map_data):
    client = project_with_map_data["client"]
    pid = project_with_map_data["project_id"]
    resp = await client.get(f"/api/projects/{pid}/research/manuscripts")
    assert resp.status_code == 200
    rows = resp.json()
    assert any(r["control_number"] == "990000000000000001" for r in rows)
    row = next(r for r in rows if r["control_number"] == "990000000000000001")
    assert row["production_year"] == 1500
    assert row["label"] == "Mishneh Torah"


@pytest.mark.asyncio
async def test_manuscripts_non_member_403(project_with_map_data, async_client, db_session):
    from app.auth import password as pw
    from app.auth.session import COOKIE_NAME, create_session
    from app.crypto import index as idx, kek as kek_mod, pii
    from app.models.user import ROLE_EDITOR, User
    import base64

    outsider = User(
        email_index=idx.blind_index("outsider_map@example.com"),
        email_encrypted=pii.encrypt_pii("outsider_map@example.com"),
        name_encrypted=pii.encrypt_pii("Out"),
        password_hash=pw.hash_password("Strong-Pass-789!"),
        kek_salt=pii.random_bytes(16), role=ROLE_EDITOR,
    )
    db_session.add(outsider)
    await db_session.commit()
    kek = kek_mod.derive_kek("Strong-Pass-789!", salt=outsider.kek_salt)
    row, secret = await create_session(db_session, user=outsider, kek=kek)
    await db_session.commit()
    cookie = f"{row.id}.{base64.urlsafe_b64encode(secret).decode().rstrip('=')}"
    async_client.cookies.set(COOKIE_NAME, cookie)
    pid = project_with_map_data["project_id"]
    resp = await async_client.get(f"/api/projects/{pid}/research/manuscripts")
    assert resp.status_code == 403


# ── provenance map ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_map_unknown_cn_404(project_with_map_data):
    client = project_with_map_data["client"]
    pid = project_with_map_data["project_id"]
    resp = await client.get(_map_url(pid, "does-not-exist"))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_map_has_production_and_current_holder(project_with_map_data):
    client = project_with_map_data["client"]
    pid = project_with_map_data["project_id"]
    resp = await client.get(_map_url(pid, "990000000000000001"))
    assert resp.status_code == 200
    body = resp.json()
    kinds = [s["kind"] for s in body["stops"]]
    assert "production" in kinds
    assert kinds[-1] == "current_holder"
    prod = next(s for s in body["stops"] if s["kind"] == "production")
    assert prod["lat"] == 15.35 and prod["lon"] == 44.2
    assert prod["year"] == 1500


@pytest.mark.asyncio
async def test_map_owner_geolocated_via_monkeypatched_enrichment(
    project_with_map_data, monkeypatch,
):
    """Owner gets a point when enrichment resolves coords; edge is inferred."""
    import app.routers.research_provenance as rp

    async def _fake_owner_place(qid, **kw):
        if qid == "Q111":
            return {"lat": 32.0, "lon": 35.0, "geo_source": "P551", "geo_source_label": "residence"}
        return None

    monkeypatch.setattr(rp, "owner_place", _fake_owner_place)
    client = project_with_map_data["client"]
    pid = project_with_map_data["project_id"]
    resp = await client.get(_map_url(pid, "990000000000000001"))
    assert resp.status_code == 200
    body = resp.json()
    owners = [s for s in body["stops"] if s["kind"] == "owner"]
    assert len(owners) == 1
    assert owners[0]["inferred_geo"] is True
    assert owners[0]["geo_source"] == "P551"
    assert any(e["inferred"] for e in body["edges"])
    # edges expose "from" (aliased), not "from_"
    assert all("from" in e for e in body["edges"])


@pytest.mark.asyncio
async def test_map_edge_serializes_from_alias(project_with_map_data):
    client = project_with_map_data["client"]
    pid = project_with_map_data["project_id"]
    resp = await client.get(_map_url(pid, "990000000000000001"))
    for e in resp.json()["edges"]:
        assert "from" in e and "to" in e
        assert "from_" not in e
