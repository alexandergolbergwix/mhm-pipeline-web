"""Pure functions for the Corpus Movement view (Phase 2).

Builds a per-manuscript provenance-path summary over all manuscripts in a
project:
  production place → intermediate waypoints (751 related places, 541/583
  provenance events, 942/710 former holders) → attested current physical
  holding institution (MARC 942 / 710 / holding_institutions).

Integrity rules (same spirit as Phase-1 guard layer):
- Never fabricate coordinates: points are plotted only when KIMA, the
  Ashkenazi gazetteer, or the audited holding-institution table resolves them.
- Never fabricate dates: production year only when MARC dates dict has one.
- Current holder uses the manuscript's actual attested holder (`holder_label`,
  `holder_lat`, `holder_lon`), falling back to NLI Jerusalem only when no
  specific holder is attested.
"""
from __future__ import annotations

from typing import Any

from app.pipeline.research_provenance_map import (
    _coords_from_payload,
    _year_band,
    is_owner_role,
    resolve_former_holder_coords,
    resolve_place_coords_from_matches_or_gazetteer,
    resolve_record_holder_stop,
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
    prod_coords = (
        resolve_place_coords_from_matches_or_gazetteer(
            prod_place_text, matches, production_only=True,
        )
        if prod_place_text
        else None
    )
    prod_lat: float | None = prod_coords[0] if prod_coords else None
    prod_lon: float | None = prod_coords[1] if prod_coords else None

    # Genres from MARC record.
    genres: list[str] = []
    for g in prepared.get("genre_form") or []:
        if isinstance(g, str) and g.strip():
            genres.append(g.strip())
    for g in prepared.get("genres") or []:
        if isinstance(g, str) and g.strip() and g.strip() not in genres:
            genres.append(g.strip())

    # Owners: entity_text of approved+medium/high-confidence owner matches + MARC 942/710 former holders.
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
    for fh_row in prepared.get("former_holders") or []:
        if isinstance(fh_row, dict):
            fh_name = str(fh_row.get("holder") or "").strip()
            if fh_name and fh_name not in owners:
                owners.append(fh_name)

    # Provenance-event places + intermediate waypoints (751 related places & 942 former holders).
    event_places: list[dict[str, Any]] = []
    seen_waypoint_labels: set[str] = (
        {prod_place_text.casefold()} if prod_place_text else set()
    )

    for ev in prepared.get("provenance_events") or []:
        if not isinstance(ev, dict):
            continue
        ev_place = str(ev.get("place_text") or "").strip()
        if not ev_place:
            continue
        ev_coords = resolve_place_coords_from_matches_or_gazetteer(ev_place, matches)
        ev_lat = ev_coords[0] if ev_coords else None
        ev_lon = ev_coords[1] if ev_coords else None
        seen_waypoint_labels.add(ev_place.casefold())
        event_places.append({
            "type": str(ev.get("type") or "provenance"),
            "place": ev_place,
            "lat": ev_lat,
            "lon": ev_lon,
            "year": ev.get("year"),
        })

    # MARC 751$a related / intermediate places (places the manuscript was in after production).
    for rp in prepared.get("related_places") or []:
        rp_text = str(rp or "").strip()
        if not rp_text or rp_text.casefold() in seen_waypoint_labels:
            continue
        rp_coords = resolve_place_coords_from_matches_or_gazetteer(rp_text, matches)
        if rp_coords is not None:
            seen_waypoint_labels.add(rp_text.casefold())
            event_places.append({
                "type": "significant_place",
                "place": rp_text,
                "lat": rp_coords[0],
                "lon": rp_coords[1],
                "year": None,
            })

    # MARC 942 / 710 former holding institutions & collections (e.g. Mossad ha-Rav Kook, Gaster, Adler).
    for fh_row in prepared.get("former_holders") or []:
        if not isinstance(fh_row, dict):
            continue
        fh_name = str(fh_row.get("holder") or "").strip()
        if not fh_name or fh_name.casefold() in seen_waypoint_labels:
            continue
        resolved_fh = resolve_former_holder_coords(fh_name, fh_row.get("city"))
        if resolved_fh is not None:
            seen_waypoint_labels.add(fh_name.casefold())
            sm = str(fh_row.get("shelfmark") or "").strip()
            wp_label = f"{fh_name} ({ resolved_fh[2] })" + (f" · {sm}" if sm else "")
            event_places.append({
                "type": "former_holder",
                "place": wp_label,
                "lat": resolved_fh[0],
                "lon": resolved_fh[1],
                "year": None,
            })

    # Attested current physical holder (MARC 942 / 710 / holding_institutions).
    holder = resolve_record_holder_stop(prepared)

    # All production + related + event + holder place names for the place filter.
    places: list[str] = []
    if prod_place_text:
        places.append(prod_place_text)
    for rp in prepared.get("related_places") or []:
        if isinstance(rp, str) and rp.strip() and rp.strip() not in places:
            places.append(rp.strip())
    for ep in event_places:
        if ep["place"] not in places:
            places.append(ep["place"])
    holder_place = str(holder.get("place") or "").strip()
    if holder_place and holder_place not in places:
        places.append(holder_place)

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
        "holder_lat": holder["lat"],
        "holder_lon": holder["lon"],
        "holder_label": holder["label"],
        "holder_place": holder_place or None,
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
    holder: str | None = None,
) -> dict[str, Any]:
    """Apply filters and build the corpus-movement response payload.

    *items* is the full unfiltered list from :func:`_extract_corpus_item`.
    Filters are ANDed; passing no filters returns all items.
    """
    place_lc = place.strip().lower() if place else None
    genre_lc = genre.strip().lower() if genre else None
    owner_lc = owner.strip().lower() if owner else None
    holder_lc = holder.strip().lower() if holder else None

    filtered: list[dict[str, Any]] = []
    for item in items:
        year = item["production_year"]
        year_e = item["production_year_earliest"]
        year_l = item["production_year_latest"]
        low = year_e if year_e is not None else year
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
        if holder_lc:
            if holder_lc not in str(item.get("holder_label") or "").lower():
                continue

        filtered.append(item)

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
    all_holders: set[str] = set()
    years: list[int] = []

    for item in items:
        for p in item["places"]:
            all_places.add(p)
        for g in item["genres"]:
            all_genres.add(g)
        for o in item["owners"]:
            all_owners.add(o)
        h = str(item.get("holder_label") or "").strip()
        if h:
            all_holders.add(h)
        y = item["production_year"]
        if y is not None:
            years.append(y)

    return {
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "places": sorted(all_places),
        "genres": sorted(all_genres),
        "owners": sorted(all_owners),
        "holders": sorted(all_holders),
    }
