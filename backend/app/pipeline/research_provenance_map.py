"""Assemble a geo+time-ordered provenance movement map for one manuscript.

Pure functions (no I/O, no network) so the integrity guards are unit-testable.
The router resolves the DB rows + owner coordinates, then calls
:func:`build_provenance_map`.

Stops (map points):
  - ``production``        — production place (KIMA coords) + production date band
  - ``owner``             — an owner, geolocated via Wikidata biographical proxy
  - ``significant_place`` — a related/significant place (751/752, KIMA coords)
  - ``current_holder``    — the National Library of Israel (Jerusalem) anchor

Integrity guards (plan §A–§D). Every guard has a test in
``test_provenance_map_guards.py``. Governing rule: missing/failed signal →
drop or visibly mark uncertain, never fabricate.
"""
from __future__ import annotations

import hashlib
from typing import Any

# Anachronism buffer (plan A5; mirrors the desktop Stage-3 DATE_BIRTH_BUFFER_YEARS).
DATE_BIRTH_BUFFER_YEARS = 5

# Current holder anchor: National Library of Israel, Jerusalem.
NLI_HOLDER = {
    "label": "National Library of Israel",
    "lat": 31.7942,
    "lon": 35.2007,
}

_OWNER_ROLE_TOKENS = (
    "owner", "former owner", "current owner", "former_owner", "current_owner",
    "possessor", "provenance",
    # Hebrew: בעלים / בעל / בעלים קודמים (former owner)
    "בעלים", "בעל",
)

_ACCEPTED_CONFIDENCE = ("high", "medium")


def is_owner_role(role: str) -> bool:
    """True when a role string denotes ownership (English or Hebrew)."""
    r = (role or "").strip().lower()
    if not r:
        return False
    return any(tok in r for tok in _OWNER_ROLE_TOKENS)


def _coords_from_payload(payload: dict[str, Any]) -> tuple[float, float] | None:
    """KIMA coords from an authority-match payload, validated (guard B1/A6)."""
    lat_raw = payload.get("kima_lat", payload.get("lat"))
    lon_raw = payload.get("kima_lon", payload.get("lon"))
    if lat_raw is None or lon_raw is None:
        return None
    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except (TypeError, ValueError):
        return None
    if not _valid_coords(lat, lon):
        return None
    return lat, lon


def _valid_coords(lat: float, lon: float) -> bool:
    for v in (lat, lon):
        if v != v or v in (float("inf"), float("-inf")):
            return False
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return False
    if abs(lat) < 1e-9 and abs(lon) < 1e-9:
        return False
    return True


def _year_band(dates: dict[str, Any] | None) -> tuple[int | None, int | None, int | None]:
    """Return (year_certain, year_earliest, year_latest) from a MARC dates dict."""
    if not isinstance(dates, dict):
        return None, None, None

    def _int(v: Any) -> int | None:
        try:
            return int(str(v).split("-")[0].split("T")[0])
        except (TypeError, ValueError, AttributeError):
            return None

    year = _int(dates.get("year"))
    early = _int(dates.get("date_start"))
    if early is None:
        early = _int(dates.get("year_start"))
    if early is None:
        early = year
    late = _int(dates.get("date_end"))
    if late is None:
        late = _int(dates.get("year_end"))
    if late is None:
        late = year
    return year, early, late


def _event_coords(
    ev: dict[str, Any], ev_place: str, matches: list[dict[str, Any]],
) -> tuple[float, float] | None:
    """Coords for a provenance event: matched authority place first, then the
    event's own merged lat/lon. Returns None when neither is geolocated."""
    ev_lc = ev_place.strip().lower()
    for m in matches:
        if m.get("entity_kind") != "place":
            continue
        mtext = str(m.get("entity_text") or "").strip().lower()
        if mtext and (mtext == ev_lc or mtext in ev_lc or ev_lc in mtext):
            c = _coords_from_payload(m.get("payload") or {})
            if c is not None:
                return c
    lat, lon = ev.get("lat"), ev.get("lon")
    if lat is not None and lon is not None:
        try:
            lat_f, lon_f = float(lat), float(lon)
        except (TypeError, ValueError):
            return None
        if _valid_coords(lat_f, lon_f):
            return lat_f, lon_f
    return None


def build_provenance_map(
    *,
    control_number: str,
    ms_label: str | None,
    record: dict[str, Any],
    matches: list[dict[str, Any]],
    owner_places: dict[str, dict[str, Any] | None],
    include_unapproved: bool = False,
) -> dict[str, Any]:
    """Build the movement map for one manuscript.

    ``owner_places`` maps owner QID → resolved place (``{lat,lon,geo_source,
    geo_source_label}``) or None, pre-fetched by the caller so this stays pure.
    """
    stops: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    # ── Production stop ────────────────────────────────────────────────
    prod_year, prod_early, prod_late = _year_band(record.get("dates"))
    prod_place_text = str(record.get("place") or "").strip()
    prod_coords: tuple[float, float] | None = None
    if prod_place_text:
        for m in matches:
            if m.get("entity_kind") != "place":
                continue
            role = (m.get("role") or "").lower()
            text = str(m.get("entity_text") or "").strip()
            if role.startswith("production") or text == prod_place_text:
                c = _coords_from_payload(m.get("payload") or {})
                if c is not None:
                    prod_coords = c
                    break
    prod_sort = prod_year if prod_year is not None else prod_early
    if prod_coords is not None or prod_year is not None or prod_early is not None:
        lat, lon = prod_coords if prod_coords else (None, None)
        stops.append({
            "kind": "production",
            "label": prod_place_text or "Production",
            "lat": lat, "lon": lon,
            "year": prod_year, "year_earliest": prod_early, "year_latest": prod_late,
            "certain": prod_year is not None,
            "inferred_geo": False,
            "time": float(prod_sort) if prod_sort is not None else None,
            "has_point": prod_coords is not None,
        })

    latest_for_anachronism = prod_late if prod_late is not None else prod_year

    # ── Provenance-event stops (acquisition / conservation / exhibition) ─
    # Typed + dated waypoints from record["provenance_events"] (Rule 60).
    # Coordinates come from the matching authority place (KIMA / gazetteer);
    # the event itself carries the date. Listed before generic significant
    # places so the same place text is not also emitted as an untyped stop.
    seen_place_text = {prod_place_text} if prod_place_text else set()
    _TYPED_EVENT_KINDS = {"acquisition", "conservation", "exhibition"}
    for ev in record.get("provenance_events") or []:
        if not isinstance(ev, dict):
            continue
        ev_place = str(ev.get("place_text") or "").strip()
        etype = str(ev.get("type") or "").lower()
        if not ev_place or etype not in _TYPED_EVENT_KINDS or ev_place in seen_place_text:
            continue
        coords = _event_coords(ev, ev_place, matches)
        if coords is None:
            continue  # no point → no map stop (never fabricate)
        seen_place_text.add(ev_place)
        ev_year = ev.get("year")
        try:
            ev_year = int(ev_year) if ev_year is not None else None
        except (TypeError, ValueError):
            ev_year = None
        stops.append({
            "kind": etype,
            "label": ev_place,
            "lat": coords[0], "lon": coords[1],
            "year": ev_year,
            "year_earliest": ev.get("year_earliest"),
            "year_latest": ev.get("year_latest"),
            "certain": ev_year is not None,
            "inferred_geo": False,
            "time": float(ev_year) if ev_year is not None else None,
            "has_point": True,
        })

    # ── Significant / related places (undated waypoints) ───────────────
    for m in matches:
        if m.get("entity_kind") != "place":
            continue
        role = (m.get("role") or "").lower()
        if role.startswith("production"):
            continue
        text = str(m.get("entity_text") or "").strip()
        if not text or text in seen_place_text:
            continue
        c = _coords_from_payload(m.get("payload") or {})
        if c is None:
            continue
        seen_place_text.add(text)
        stops.append({
            "kind": "significant_place",
            "label": text,
            "lat": c[0], "lon": c[1],
            "year": None, "year_earliest": None, "year_latest": None,
            "certain": False, "inferred_geo": False,
            "time": None, "has_point": True,
        })

    # ── Owner stops ────────────────────────────────────────────────────
    owners_dated: list[dict[str, Any]] = []
    owners_undated: list[dict[str, Any]] = []
    seen_owner: set[str] = set()
    for m in matches:
        # Persons AND organizations/collections may be owners (a named
        # collection like Braginsky is a corporate former-owner).
        if m.get("entity_kind") not in ("person", "organization") or not is_owner_role(
            m.get("role", "")
        ):
            continue
        name = str(m.get("entity_text") or m.get("matched_name") or "").strip()
        if not name or name in seen_owner:
            continue
        seen_owner.add(name)
        qid = str(m.get("wikidata_qid") or "").strip()
        payload = m.get("payload") or {}
        birth = payload.get("birth_year")
        death = payload.get("death_year")
        try:
            birth = int(birth) if birth is not None else None
        except (TypeError, ValueError):
            birth = None

        # A1 — approval gate.
        if not include_unapproved and not m.get("approved"):
            dropped.append({"label": name, "reason": "unapproved"})
            continue
        # A2 — confidence gate.
        if (m.get("confidence") or "").lower() not in _ACCEPTED_CONFIDENCE:
            dropped.append({"label": name, "reason": "low_confidence"})
            continue
        # A5 — anachronism: born after the manuscript could not have owned it.
        if (birth is not None and latest_for_anachronism is not None
                and birth > latest_for_anachronism + DATE_BIRTH_BUFFER_YEARS):
            dropped.append({"label": name, "reason": "anachronism"})
            continue

        place = owner_places.get(qid) if qid else None
        if place is None:
            # A7 — no resolvable coordinate → side list, never a fabricated point.
            dropped.append({"label": name, "reason": "no_location"})
            continue

        stop = {
            "kind": "owner",
            "label": name,
            "uri": qid,
            "lat": place["lat"], "lon": place["lon"],
            "year": None,
            "year_earliest": None, "year_latest": None,
            "birth_year": birth, "death_year": death,
            "certain": False, "inferred_geo": True,
            "geo_source": place.get("geo_source"),
            "geo_source_label": place.get("geo_source_label"),
            "approved": bool(m.get("approved")),
            "time": float(birth) if birth is not None else None,
            "has_point": True,
        }
        (owners_dated if birth is not None else owners_undated).append(stop)

    owners_dated.sort(key=lambda s: s["time"])
    stops.extend(owners_dated)
    stops.extend(owners_undated)

    # ── Current holder (present-day anchor, last) ──────────────────────
    stops.append({
        "kind": "current_holder",
        "label": NLI_HOLDER["label"],
        "lat": NLI_HOLDER["lat"], "lon": NLI_HOLDER["lon"],
        "year": None, "year_earliest": None, "year_latest": None,
        "certain": True, "inferred_geo": False, "is_present": True,
        "time": None, "has_point": True,
    })

    # ── Edges between consecutive geo-located stops ────────────────────
    mapped = [i for i, s in enumerate(stops) if s.get("has_point")]
    edges: list[dict[str, Any]] = []
    for a, b in zip(mapped, mapped[1:], strict=False):
        sa, sb = stops[a], stops[b]
        both_certain = bool(sa.get("certain")) and bool(sb.get("certain"))
        inferred = (not both_certain) or sa.get("inferred_geo") or sb.get("inferred_geo")
        # D2 — no implied direction between two undated stops.
        directed = sa.get("time") is not None or sb.get("time") is not None
        edges.append({"from": a, "to": b, "inferred": bool(inferred), "directed": bool(directed)})

    return {
        "control_number": control_number,
        "ms_label": ms_label,
        "stops": stops,
        "edges": edges,
        "dropped": dropped,
    }


def data_fingerprint(parts: list[str]) -> str:
    """Short stable hash for cache keys — invalidates when the inputs change."""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
