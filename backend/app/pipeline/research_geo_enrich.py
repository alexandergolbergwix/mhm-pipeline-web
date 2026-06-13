"""Owner → geographic place enrichment for the provenance movement map.

The catalogue gives us owner *names* (and, after authority matching, a
Wikidata QID + lifespan) but never a *location*. To place an owner on the
movement map we look up a biographical place from Wikidata — in strict
precedence: residence (P551) → work location (P937) → place of death (P20) →
place of birth (P19) — and resolve its coordinate location (P625).

Every result is a **biographical proxy**, never a claim that the owner held
the manuscript there. The caller flags it inferred and records which property
produced it (``geo_source``).

Integrity guards enforced here (see plan §A):
- A3 entity-type: the QID must be a human (``P31 = Q5``) or no place is returned.
- A6 coordinate sanity: lat ∈ [-90,90], lon ∈ [-180,180], finite, not (0,0).
- A7 no-coords → None (never invent coordinates).
- A8 external-input validation: WKT parsed strictly; a property with multiple
  *distinct* coordinates is abstained (skipped), per the Rule-40 pattern.

The lookup is wrapped in the two-tier inference cache
(``kind="wikidata.person_place"``, Redis L1 → Postgres L2) so it is global and
incremental. Honours ``MHM_NO_NETWORK``. Never raises — any failure → None.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_WDQS = "https://query.wikidata.org/sparql"
_Q_HUMAN = "Q5"

# Place-property precedence: residence > work location > death > birth.
_PLACE_PROPS: tuple[tuple[str, str], ...] = (
    ("P551", "residence"),
    ("P937", "work location"),
    ("P20", "place of death"),
    ("P19", "place of birth"),
)

_QID_RE = re.compile(r"^Q[1-9][0-9]*$")
# WKT point as returned by WDQS for a P625 coordinate: "Point(lon lat)".
_POINT_RE = re.compile(
    r"^\s*Point\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)\s*$",
    re.IGNORECASE,
)


def _valid_coords(lat: float, lon: float) -> bool:
    """Guard A6 — finite, in range, and not the (0,0) null-island placeholder."""
    for v in (lat, lon):
        if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
            return False
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return False
    if abs(lat) < 1e-9 and abs(lon) < 1e-9:  # (0,0) — almost always a placeholder
        return False
    return True


def _parse_point(wkt: str) -> tuple[float, float] | None:
    """Parse a WDQS ``Point(lon lat)`` literal → (lat, lon), validated."""
    m = _POINT_RE.match(wkt or "")
    if m is None:
        return None
    try:
        lon = float(m.group(1))
        lat = float(m.group(2))
    except (TypeError, ValueError):
        return None
    if not _valid_coords(lat, lon):
        return None
    return lat, lon


def _parse_place_binding(bindings: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pure parser over WDQS JSON bindings → ``{lat, lon, geo_source}`` | None.

    Implements guards A3 (must be human), A8 (abstain on conflicting coords for
    a property), and the property precedence. Kept pure so it is unit-testable
    without network. Expected binding keys: ``type`` (P31 QID) and one var per
    place property named by its PID (``P551`` … ``P19``) carrying a WKT point.
    """
    if not bindings:
        return None

    # A3 — collect P31 values; require Q5 (human) to be among them.
    types: set[str] = set()
    # Per-property set of distinct parsed coords (for A8 abstain-on-conflict).
    coords_by_prop: dict[str, set[tuple[float, float]]] = {pid: set() for pid, _ in _PLACE_PROPS}

    for b in bindings:
        tval = (b.get("type") or {}).get("value", "")
        if tval:
            types.add(tval.rsplit("/", 1)[-1])
        for pid, _label in _PLACE_PROPS:
            cell = b.get(pid)
            if not cell:
                continue
            parsed = _parse_point(cell.get("value", ""))
            if parsed is not None:
                coords_by_prop[pid].add(parsed)

    if _Q_HUMAN not in types:
        # Not a human (org / place / disambiguation page) → never geolocate.
        return None

    for pid, label in _PLACE_PROPS:
        candidates = coords_by_prop[pid]
        if len(candidates) == 1:
            lat, lon = next(iter(candidates))
            return {"lat": lat, "lon": lon, "geo_source": pid, "geo_source_label": label}
        # len == 0 → property absent; len > 1 → A8 abstain, try next property.
    return None


def _build_sparql(qid: str) -> str:
    return (
        "SELECT ?type ?P551 ?P937 ?P20 ?P19 WHERE { "
        f"OPTIONAL {{ wd:{qid} wdt:P31 ?type . }} "
        f"OPTIONAL {{ wd:{qid} wdt:P551 ?r . ?r wdt:P625 ?P551 . }} "
        f"OPTIONAL {{ wd:{qid} wdt:P937 ?w . ?w wdt:P625 ?P937 . }} "
        f"OPTIONAL {{ wd:{qid} wdt:P20 ?d . ?d wdt:P625 ?P20 . }} "
        f"OPTIONAL {{ wd:{qid} wdt:P19 ?b . ?b wdt:P625 ?P19 . }} "
        "} LIMIT 50"
    )


async def owner_place(
    qid: str,
    *,
    db_session: Any | None = None,
    user_id: Any | None = None,
    skip_cache: bool = False,
) -> dict[str, Any] | None:
    """Return ``{lat, lon, geo_source, geo_source_label}`` for an owner QID, or None.

    Cached under ``kind="wikidata.person_place"``. Returns None when the QID is
    malformed, ``MHM_NO_NETWORK`` is set, the entity is not a human, no place
    property resolves to a single valid coordinate, or any error occurs.
    """
    qid = (qid or "").strip()
    if not _QID_RE.match(qid):
        return None
    if os.environ.get("MHM_NO_NETWORK", "").lower() in ("1", "true", "yes"):
        return None

    import asyncio  # noqa: PLC0415

    async def _fetch() -> dict[str, Any] | None:
        import httpx  # noqa: PLC0415

        def _call() -> list[dict[str, Any]]:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(
                    _WDQS,
                    params={"query": _build_sparql(qid), "format": "json"},
                    headers={
                        "Accept": "application/sparql-results+json",
                        "User-Agent": "mhm-pipeline-web/1.0 (provenance-map)",
                    },
                )
                if resp.status_code != 200:
                    return []
                return resp.json().get("results", {}).get("bindings", [])

        try:
            bindings = await asyncio.to_thread(_call)
        except Exception as exc:  # noqa: BLE001
            logger.debug("owner_place WDQS failed for %s: %s", qid, exc)
            return None
        return _parse_place_binding(bindings)

    if db_session is None:
        return await _fetch()

    from app.pipeline.inference_cache import cache_lookup_or_call  # noqa: PLC0415

    return await cache_lookup_or_call(
        db_session,
        kind="wikidata.person_place",
        query_summary={"op": "owner_place", "qid": qid},
        fetch=_fetch,
        user_id=user_id,
        skip_cache=skip_cache,
    )
