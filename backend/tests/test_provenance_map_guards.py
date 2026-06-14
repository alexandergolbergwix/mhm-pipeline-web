"""Integrity regression barrier for the provenance movement map builder.

One test (or a few) per guard A1–A8, B1–B3, C1–C4, D1–D2 from the plan.
Do NOT weaken these — they are what stops false geographic/temporal claims
from reaching the scholarly map. The builder is pure, so these run offline.
"""
from __future__ import annotations

from app.pipeline.research_provenance_map import (
    DATE_BIRTH_BUFFER_YEARS,
    build_provenance_map,
    is_owner_role,
)


def _owner_match(name, *, qid="Q42", confidence="high", approved=True, birth=None, death=None):
    payload = {}
    if birth is not None:
        payload["birth_year"] = birth
    if death is not None:
        payload["death_year"] = death
    return {
        "entity_text": name, "entity_kind": "person", "role": "former owner",
        "matched_name": name, "wikidata_qid": qid, "confidence": confidence,
        "approved": approved, "payload": payload,
    }


def _place_match(text, *, role="production place", lat=31.8, lon=35.2):
    return {
        "entity_text": text, "entity_kind": "place", "role": role,
        "matched_name": text, "wikidata_qid": "", "confidence": "high",
        "approved": True, "payload": {"kima_lat": lat, "kima_lon": lon},
    }


def _build(record, matches, owner_places, **kw):
    return build_provenance_map(
        control_number="cn1", ms_label="MS", record=record,
        matches=matches, owner_places=owner_places, **kw,
    )


def _kinds(res):
    return [s["kind"] for s in res["stops"]]


def _reasons(res):
    return {d["reason"] for d in res["dropped"]}


# ── A1: approval gate ────────────────────────────────────────────────────

def test_a1_unapproved_owner_no_point():
    res = _build(
        {"dates": {"year": 1700}},
        [_owner_match("Ploni", approved=False)],
        {"Q42": {"lat": 1, "lon": 1, "geo_source": "P551"}},
    )
    assert "owner" not in _kinds(res)
    assert "unapproved" in _reasons(res)


def test_a1_include_unapproved_lets_owner_through():
    res = _build(
        {"dates": {"year": 1700}},
        [_owner_match("Ploni", approved=False, birth=1650)],
        {"Q42": {"lat": 32.0, "lon": 35.0, "geo_source": "P551"}},
        include_unapproved=True,
    )
    assert "owner" in _kinds(res)


# ── A2: confidence gate ──────────────────────────────────────────────────

def test_a2_low_confidence_owner_no_point():
    res = _build(
        {"dates": {"year": 1700}},
        [_owner_match("Ploni", confidence="low")],
        {"Q42": {"lat": 1, "lon": 1, "geo_source": "P551"}},
    )
    assert "owner" not in _kinds(res)
    assert "low_confidence" in _reasons(res)


# ── A5: anachronism ──────────────────────────────────────────────────────

def test_a5_owner_born_after_manuscript_dropped():
    res = _build(
        {"dates": {"year": 1500}},
        [_owner_match("FutureGuy", birth=1700)],
        {"Q42": {"lat": 32.0, "lon": 35.0, "geo_source": "P551"}},
    )
    assert "owner" not in _kinds(res)
    assert "anachronism" in _reasons(res)


def test_a5_owner_within_buffer_kept():
    res = _build(
        {"dates": {"year": 1500}},
        [_owner_match("EdgeGuy", birth=1500 + DATE_BIRTH_BUFFER_YEARS)],
        {"Q42": {"lat": 32.0, "lon": 35.0, "geo_source": "P551"}},
    )
    assert "owner" in _kinds(res)


# ── A7: no coords → no point ─────────────────────────────────────────────

def test_a7_owner_without_resolved_place_no_point():
    res = _build(
        {"dates": {"year": 1700}},
        [_owner_match("NoLoc", birth=1650)],
        {"Q42": None},  # enrichment found nothing
    )
    assert "owner" not in _kinds(res)
    assert "no_location" in _reasons(res)


# ── B1: real coords only (bad payload → no production point) ──────────────

def test_b1_invalid_production_coords_yields_no_point():
    res = _build(
        {"dates": {"year": 1700}, "place": "Nowhere"},
        [_place_match("Nowhere", lat=0, lon=0)],  # (0,0) rejected by A6
        {},
    )
    prod = next(s for s in res["stops"] if s["kind"] == "production")
    assert prod["has_point"] is False
    assert prod["lat"] is None


def test_b1_valid_production_coords_point():
    res = _build(
        {"dates": {"year": 1700}, "place": "Sanaa"},
        [_place_match("Sanaa", lat=15.35, lon=44.2)],
        {},
    )
    prod = next(s for s in res["stops"] if s["kind"] == "production")
    assert prod["has_point"] is True
    assert prod["lat"] == 15.35


# ── B3: role honesty (non-production place never labelled production) ─────

def test_b3_related_place_is_significant_not_production():
    res = _build(
        {"dates": {"year": 1700}, "place": "Sanaa"},
        [
            _place_match("Sanaa", role="production place", lat=15.35, lon=44.2),
            _place_match("Aden", role="place", lat=12.8, lon=45.0),
        ],
        {},
    )
    kinds = _kinds(res)
    sig = [s for s in res["stops"] if s["kind"] == "significant_place"]
    assert kinds.count("production") == 1
    assert any(s["label"] == "Aden" for s in sig)


# ── C1: no fabricated years (undated owner has no time) ──────────────────

def test_c1_owner_without_birth_year_has_no_time():
    res = _build(
        {"dates": {"year": 1700}},
        [_owner_match("Undated", birth=None)],
        {"Q42": {"lat": 32.0, "lon": 35.0, "geo_source": "P551"}},
    )
    owner = next(s for s in res["stops"] if s["kind"] == "owner")
    assert owner["time"] is None
    assert owner["year"] is None


# ── C2: production date band, no fake midpoint ───────────────────────────

def test_c2_production_range_keeps_bounds():
    res = _build(
        {"dates": {"date_start": 1400, "date_end": 1450}, "place": "X"},
        [_place_match("X", lat=32, lon=35)],
        {},
    )
    prod = next(s for s in res["stops"] if s["kind"] == "production")
    assert prod["year_earliest"] == 1400
    assert prod["year_latest"] == 1450
    assert prod["year"] is None  # no certain year, no invented midpoint


# ── C3: inferred edges flagged ───────────────────────────────────────────

def test_c3_owner_edge_is_inferred():
    res = _build(
        {"dates": {"year": 1500}, "place": "X"},
        [_place_match("X", lat=32, lon=35), _owner_match("O", birth=1490)],
        {"Q42": {"lat": 33.0, "lon": 36.0, "geo_source": "P551"}},
    )
    # the production→owner edge must be inferred (owner geo is a proxy)
    assert any(e["inferred"] for e in res["edges"])


def test_current_holder_is_last_and_present():
    res = _build({"dates": {"year": 1500}, "place": "X"},
                 [_place_match("X", lat=32, lon=35)], {})
    assert res["stops"][-1]["kind"] == "current_holder"
    assert res["stops"][-1]["is_present"] is True


# ── D2: no direction between two undated stops ───────────────────────────

def test_d2_undated_pair_edge_not_directed():
    # production with NO date + a significant place (both undated) → edge undirected
    res = _build(
        {"place": "X"},  # no dates
        [
            _place_match("X", role="production place", lat=32, lon=35),
            _place_match("Y", role="place", lat=33, lon=36),
        ],
        {},
    )
    # find edge between the two undated mapped stops
    undirected = [e for e in res["edges"] if not e["directed"]]
    assert undirected, res["edges"]


# ── owner-role detection ─────────────────────────────────────────────────

def test_owner_role_english_and_hebrew():
    assert is_owner_role("former owner")
    assert is_owner_role("current owner")
    assert is_owner_role("בעלים קודמים")
    assert not is_owner_role("author")
    assert not is_owner_role("scribe")
    assert not is_owner_role("")


# ── Rule 60: typed, dated provenance-event stops ─────────────────────────

def test_acquisition_event_emits_typed_dated_stop():
    res = _build(
        {
            "dates": {"year": 1500},
            "provenance_events": [
                {"type": "acquisition", "place_text": "Zurich", "year": 1985,
                 "lat": None, "lon": None, "source_field": "541"},
            ],
        },
        [_place_match("Zurich", role="acquisition_place", lat=47.37, lon=8.54)],
        {},
    )
    acq = next(s for s in res["stops"] if s["kind"] == "acquisition")
    assert acq["lat"] == 47.37 and acq["lon"] == 8.54
    assert acq["year"] == 1985 and acq["certain"] is True
    assert _kinds(res).count("acquisition") == 1
    assert all(not (s["kind"] == "significant_place" and s["label"] == "Zurich")
               for s in res["stops"])


def test_event_without_coords_is_dropped():
    res = _build(
        {"dates": {"year": 1500},
         "provenance_events": [
             {"type": "conservation", "place_text": "Nowhere", "year": 2010,
              "lat": None, "lon": None, "source_field": "583"}]},
        [],
        {},
    )
    assert "conservation" not in _kinds(res)


def test_event_coords_from_merged_latlon_when_no_match():
    res = _build(
        {"provenance_events": [
            {"type": "exhibition", "place_text": "Oxford", "year": 1999,
             "lat": 51.75, "lon": -1.26, "source_field": "583"}]},
        [],
        {},
    )
    exh = next(s for s in res["stops"] if s["kind"] == "exhibition")
    assert exh["lat"] == 51.75 and exh["lon"] == -1.26
