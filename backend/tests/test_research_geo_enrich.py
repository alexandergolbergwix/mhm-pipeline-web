"""Unit tests for owner→place Wikidata enrichment (provenance movement map).

Pure-parser + guard tests (no network). See plan §A (A3, A6, A7, A8).
"""
from __future__ import annotations

import pytest

from app.pipeline import research_geo_enrich as g

_HUMAN = "http://www.ontology.org.il/x"  # not used; real type below


def _b(type_qid: str | None = "Q5", **props: str) -> dict:
    """Build one WDQS binding: type + place-prop WKT points."""
    row: dict = {}
    if type_qid is not None:
        row["type"] = {"value": f"http://www.wikidata.org/entity/{type_qid}"}
    for pid, wkt in props.items():
        row[pid] = {"value": wkt}
    return row


# ── coordinate sanity (A6) ──────────────────────────────────────────────

def test_valid_coords_accepts_in_range():
    assert g._valid_coords(31.8, 35.2)


@pytest.mark.parametrize("lat,lon", [(0, 0), (999, 0), (0, 999), (float("nan"), 1)])
def test_valid_coords_rejects_bad(lat, lon):
    assert not g._valid_coords(lat, lon)


def test_parse_point_roundtrip():
    assert g._parse_point("Point(35.2 31.8)") == (31.8, 35.2)


@pytest.mark.parametrize("bad", ["", "garbage", "Point(0 0)", "Point(500 10)"])
def test_parse_point_rejects(bad):
    assert g._parse_point(bad) is None


# ── place-binding parser (A3 / A8 / precedence) ──────────────────────────

def test_precedence_p551_wins_over_p19():
    b = [_b("Q5", P551="Point(35.2 31.8)", P19="Point(2 48)")]
    r = g._parse_place_binding(b)
    assert r is not None
    assert r["geo_source"] == "P551"
    assert (r["lat"], r["lon"]) == (31.8, 35.2)


def test_precedence_falls_through_to_p19():
    b = [_b("Q5", P19="Point(2 48)")]
    r = g._parse_place_binding(b)
    assert r and r["geo_source"] == "P19"


def test_a3_non_human_returns_none():
    # Q43229 = organisation; must never geolocate as an owner.
    b = [_b("Q43229", P19="Point(2 48)")]
    assert g._parse_place_binding(b) is None


def test_a3_missing_type_returns_none():
    b = [_b(None, P551="Point(35.2 31.8)")]
    assert g._parse_place_binding(b) is None


def test_a8_conflicting_coords_abstains():
    # Two distinct P551 coords → abstain that property; nothing else present.
    b = [_b("Q5", P551="Point(35.2 31.8)"), _b("Q5", P551="Point(10 20)")]
    assert g._parse_place_binding(b) is None


def test_a8_conflict_falls_through_to_next_property():
    b = [
        _b("Q5", P551="Point(35.2 31.8)"),
        _b("Q5", P551="Point(10 20)", P19="Point(2 48)"),
    ]
    r = g._parse_place_binding(b)
    assert r and r["geo_source"] == "P19"


def test_empty_bindings_returns_none():
    assert g._parse_place_binding([]) is None


# ── owner_place async guards (A7 / malformed / no-network) ───────────────

@pytest.mark.asyncio
async def test_owner_place_malformed_qid_returns_none():
    assert await g.owner_place("not-a-qid") is None
    assert await g.owner_place("") is None


@pytest.mark.asyncio
async def test_owner_place_no_network_env(monkeypatch):
    monkeypatch.setenv("MHM_NO_NETWORK", "true")
    assert await g.owner_place("Q42") is None


@pytest.mark.asyncio
async def test_owner_place_no_db_uses_fetch(monkeypatch):
    """Without a db_session, owner_place calls fetch directly (no cache)."""
    async def _fake_fetch_bindings(*a, **k):
        return [_b("Q5", P551="Point(35.2 31.8)")]

    # Patch the WKT-producing HTTP path by stubbing _parse via a fake response.
    import httpx

    class _Resp:
        status_code = 200
        def json(self):
            return {"results": {"bindings": [_b("Q5", P551="Point(35.2 31.8)")]}}

    class _Client:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k): return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    r = await g.owner_place("Q42")
    assert r is not None
    assert r["geo_source"] == "P551"
    assert (r["lat"], r["lon"]) == (31.8, 35.2)
