"""Pure functions for the Corpus Movement view (Phase 2).

Builds a per-manuscript production-arc summary over all manuscripts in a
project:  production place (KIMA coords) → current holder (NLI, Jerusalem).

Only production stops are included (no owner biographical proxies) because
the corpus view needs one well-defined anchor per manuscript, not a chain.
Owners and significant places appear as filter/facet dimensions only.

Integrity rules (same spirit as Phase-1 guard layer):
- Never fabricate coordinates: production point only when KIMA coords present.
- Never fabricate dates: production year only when MARC dates dict has one.
- current_holder is always NLI Jerusalem (hard constant, same as Phase 1).
"""
from __future__ import annotations

from typing import Any

from app.pipeline.research_provenance_map import (
    NLI_HOLDER,
    _coords_from_payload,
    _year_band,
    is_owner_role,
)


def _extract_corpus_item(
    prepared: dict[str, Any],
    matches: list[dict[str, Any]],
    control_number: str,
) -> dict[str, Any]:
    """Build one corpus-movement item from a prepared MARC record + authority matches.

    Returns a flat dict ready for JSON serialisation.  The caller is responsible
    for filtering; this function extracts everything.
    """
    prod_year, prod_early, prod_late = _year_band(prepared.get("dates"))
    prod_place_text = str(prepared.get("place") or "").strip()
    prod_lat: float | None = None
    prod_lon: float | None = None

    for m in matches:
        if m.get("entity_kind") != "place":
            continue
        role = (m.get("role") or "").lower()
        text = str(m.get("entity_text") or "").strip()
        if role.startswith("production") or (prod_place_text and (
            text == prod_place_text or
            text in prod_place_text or
            prod_place_text in text
        )):
            c = _coords_from_payload(m.get("payload") or {})
            if c is not None:
                prod_lat, prod_lon = c
                break

    # Genres from MARC record.
    genres: list[str] = []
    for g in prepared.get("genre_form") or []:
        if isinstance(g, str) and g.strip():
            genres.append(g.strip())
    for g in prepared.get("genres") or []:
        if isinstance(g, str) and g.strip():
            genres.append(g.strip())

    # Owners: entity_text of approved+medium/high-confidence owner matches.
    owners: list[str] = []
    for m in matches:
        if m.get("entity_kind") not in ("person", "organization") or not is_owner_role(
            m.get("role", "")
        ):
            continue
        if (m.get("confidence") or "").lower() not in ("high", "medium"):
            continue
        name = str(m.get("entity_text") or m.get("matched_name") or "").strip()
        if name and name not in owners:
            owners.append(name)

    # Provenance-event places (acquisition / conservation / exhibition) —
    # typed + dated waypoints with coords from the matching authority place
    # (Rule 60). Powers the place filter and optional extra arcs.
    event_places: list[dict[str, Any]] = []
    for ev in prepared.get("provenance_events") or []:
        if not isinstance(ev, dict):
            continue
        ev_place = str(ev.get("place_text") or "").strip()
        if not ev_place:
            continue
        ev_lc = ev_place.lower()
        ev_lat: float | None = None
        ev_lon: float | None = None
        for m in matches:
            if m.get("entity_kind") != "place":
                continue
            mtext = str(m.get("entity_text") or "").strip().lower()
            if mtext and (mtext == ev_lc or mtext in ev_lc or ev_lc in mtext):
                c = _coords_from_payload(m.get("payload") or {})
                if c is not None:
                    ev_lat, ev_lon = c
                    break
        event_places.append({
            "type": str(ev.get("type") or "provenance"),
            "place": ev_place,
            "lat": ev_lat,
            "lon": ev_lon,
            "year": ev.get("year"),
        })

    # All production + related + event place names for the place filter.
    places: list[str] = []
    if prod_place_text:
        places.append(prod_place_text)
    for rp in prepared.get("related_places") or []:
        if isinstance(rp, str) and rp.strip() and rp.strip() not in places:
            places.append(rp.strip())
    for ep in event_places:
        if ep["place"] not in places:
            places.append(ep["place"])

    return {
        "control_number": control_number,
        "label": str(prepared.get("title") or "").strip() or control_number,
        "production_lat": prod_lat,
        "production_lon": prod_lon,
        "production_year": prod_year,
        "production_year_earliest": prod_early,
        "production_year_latest": prod_late,
        "production_place": prod_place_text or None,
        "has_production_point": prod_lat is not None and prod_lon is not None,
        "holder_lat": NLI_HOLDER["lat"],
        "holder_lon": NLI_HOLDER["lon"],
        "holder_label": NLI_HOLDER["label"],
        "genres": genres,
        "owners": owners,
        "places": places,
        "event_places": event_places,
    }


def build_corpus_movement(
    items: list[dict[str, Any]],
    *,
    from_year: int | None = None,
    to_year: int | None = None,
    place: str | None = None,
    genre: str | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    """Apply filters and build the corpus-movement response payload.

    *items* is the full unfiltered list from :func:`_extract_corpus_item`.
    Filters are ANDed; passing no filters returns all items.
    """
    place_lc = place.strip().lower() if place else None
    genre_lc = genre.strip().lower() if genre else None
    owner_lc = owner.strip().lower() if owner else None

    filtered: list[dict[str, Any]] = []
    for item in items:
        # Year-range filter on the production year band.
        year = item["production_year"]
        year_e = item["production_year_earliest"]
        year_l = item["production_year_latest"]
        # Use the broadest available year for the range check.
        low  = year_e if year_e is not None else year
        high = year_l if year_l is not None else year
        if from_year is not None and high is not None and high < from_year:
            continue
        if to_year is not None and low is not None and low > to_year:
            continue

        if place_lc:
            if not any(place_lc in p.lower() for p in item["places"]):
                continue
        if genre_lc:
            if not any(genre_lc in g.lower() for g in item["genres"]):
                continue
        if owner_lc:
            if not any(owner_lc in o.lower() for o in item["owners"]):
                continue

        filtered.append(item)

    # Year-bucket aggregation (for the animated histogram).
    bucket: dict[int, int] = {}
    for item in filtered:
        y = item["production_year"]
        if y is not None:
            bucket[y] = bucket.get(y, 0) + 1

    year_counts = sorted(
        [{"year": y, "count": c} for y, c in bucket.items()],
        key=lambda x: x["year"],
    )

    return {"manuscripts": filtered, "year_counts": year_counts}


def build_corpus_facets(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract distinct facet values from the full unfiltered corpus."""
    all_places: set[str] = set()
    all_genres: set[str] = set()
    all_owners: set[str] = set()
    years: list[int] = []

    for item in items:
        for p in item["places"]:
            all_places.add(p)
        for g in item["genres"]:
            all_genres.add(g)
        for o in item["owners"]:
            all_owners.add(o)
        y = item["production_year"]
        if y is not None:
            years.append(y)

    return {
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "places": sorted(all_places),
        "genres": sorted(all_genres),
        "owners": sorted(all_owners),
    }
