"""Pre-defined SPARQL analytics queries over a merged project RDF graph.

Each function accepts an rdflib.Graph and returns JSON-serialisable Python
structures.  All queries are SELECT-only; nothing is written to the graph.
"""
from __future__ import annotations

import logging
from typing import Any

import rdflib
from rdflib import Namespace, URIRef
from rdflib.namespace import RDF, RDFS

logger = logging.getLogger(__name__)

HM    = Namespace("http://www.ontology.org.il/HebrewManuscripts/2025-12-06#")
CIDOC = Namespace("http://www.cidoc-crm.org/cidoc-crm/")
LRMOO = Namespace("http://iflastandards.info/ns/lrm/lrmoo/")
WGS84 = Namespace("http://www.w3.org/2003/01/geo/wgs84_pos#")

_INIT_NS = {
    "hm":        HM,
    "cidoc":     CIDOC,
    "lrmoo":     LRMOO,
    "wgs84":     WGS84,
    "rdf":       RDF,
    "rdfs":      RDFS,
}

_LABEL_QUERY = """
SELECT ?s ?label WHERE {
  ?s rdfs:label ?label .
  FILTER(LANG(?label) = "he" || LANG(?label) = "")
}
"""


def _label_map(graph: rdflib.Graph) -> dict[str, str]:
    """Build a URI→label lookup for the whole graph (single query, fast)."""
    labels: dict[str, str] = {}
    try:
        for row in graph.query(_LABEL_QUERY, initNs=_INIT_NS):
            uri = str(row.s)
            label = str(row.label)
            if uri not in labels or len(label) > len(labels[uri]):
                labels[uri] = label
    except Exception as exc:
        logger.debug("label_map query failed: %s", exc)
    return labels


# ── Co-occurrence ──────────────────────────────────────────────────────

_CO_OCCUR_Q = """
SELECT DISTINCT ?ms ?work WHERE {
  ?ms hm:has_work ?work .
}
"""


def query_co_occurrence(
    graph: rdflib.Graph,
    max_edges: int = 1000,
) -> dict[str, Any]:
    """Works that appear together in the same manuscripts, pre-aggregated.

    Returns {
        nodes: [{id, label, degree}],
        edges: [{work1, work2, shared_ms_count, ms_list}],
    }

    ``degree`` is the number of distinct co-works for each work node.
    ``shared_ms_count`` is the number of manuscripts both works appear in.
    ``ms_list`` is the sorted list of those manuscript URIs (capped at 20).
    """
    if len(graph) == 0:
        return {"nodes": [], "edges": []}
    try:
        labels = _label_map(graph)
        ms_works: dict[str, list[str]] = {}
        for row in graph.query(_CO_OCCUR_Q, initNs=_INIT_NS):
            ms = str(row.ms)
            work = str(row.work)
            ms_works.setdefault(ms, []).append(work)

        # Build adjacency: edge_key (frozenset) → set of manuscript URIs
        edge_mss: dict[frozenset, set[str]] = {}
        for ms, works in ms_works.items():
            deduped = list(dict.fromkeys(works))
            for i in range(len(deduped)):
                for j in range(i + 1, len(deduped)):
                    key: frozenset = frozenset([deduped[i], deduped[j]])
                    edge_mss.setdefault(key, set()).add(ms)

        # Sort edges by descending shared_ms_count, then cap
        sorted_edges = sorted(edge_mss.items(), key=lambda kv: len(kv[1]), reverse=True)[:max_edges]

        # Compute per-node degree from the retained edges
        degree: dict[str, int] = {}
        for key, _ in sorted_edges:
            w1, w2 = tuple(key)
            degree[w1] = degree.get(w1, 0) + 1
            degree[w2] = degree.get(w2, 0) + 1

        def _short(uri: str) -> str:
            return uri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]

        nodes = [
            {
                "id":     wid,
                "label":  labels.get(wid, _short(wid)),
                "degree": deg,
            }
            for wid, deg in sorted(degree.items(), key=lambda kv: -kv[1])
        ]

        edges = []
        for key, mss in sorted_edges:
            w1, w2 = tuple(key)
            edges.append({
                "work1":           w1,
                "work2":           w2,
                "shared_ms_count": len(mss),
                "ms_list":         sorted(mss)[:20],
            })

        return {"nodes": nodes, "edges": edges}
    except Exception as exc:
        logger.warning("co_occurrence query failed: %s", exc)
        return {"nodes": [], "edges": []}


# ── People network ─────────────────────────────────────────────────────

_PERSON_MS_Q = """
SELECT DISTINCT ?ms ?person ?role WHERE {
  { ?ms hm:has_scribe ?person . BIND("scribe" AS ?role) }
  UNION { ?ms hm:has_owner  ?person . BIND("owner"  AS ?role) }
  UNION { ?work hm:has_author ?person .
          ?ms   hm:has_work   ?work .   BIND("author" AS ?role) }
  ?person rdf:type cidoc:E21_Person .
}
"""


def query_people_network(
    graph: rdflib.Graph,
    max_nodes: int = 400,
    layout_scale: float = 800.0,
    layout_seed: int = 42,
) -> dict[str, Any]:
    """Social network of persons linked to the same manuscripts.

    Returns {
        nodes: [{id, label, role, ms_count, x, y}],
        links: [{source, target, ms}],
    }.

    ``x`` / ``y`` are pre-computed spring-layout positions (networkx,
    same approach as the RDF graph endpoint) so the browser can use them
    as fixed initial coordinates instead of running a blocking D3
    simulation on mount.  The frontend still owns drag/zoom interactivity.
    """
    if len(graph) == 0:
        return {"nodes": [], "links": []}
    try:
        import networkx as nx  # noqa: PLC0415 — lazy import, mirrors rdf_build.py

        labels = _label_map(graph)

        # person → {role, set of manuscripts}
        person_info: dict[str, dict[str, Any]] = {}
        # ms → list of persons
        ms_persons: dict[str, list[str]] = {}

        for row in graph.query(_PERSON_MS_Q, initNs=_INIT_NS):
            pid  = str(row.person)
            ms   = str(row.ms)
            role = str(row.role)
            if pid not in person_info:
                person_info[pid] = {"role": role, "mss": set()}
            person_info[pid]["mss"].add(ms)
            # keep highest-priority role
            priority = {"scribe": 3, "author": 2, "owner": 1}
            if priority.get(role, 0) > priority.get(person_info[pid]["role"], 0):
                person_info[pid]["role"] = role
            ms_persons.setdefault(ms, []).append(pid)

        # Trim to max_nodes by ms_count descending
        sorted_persons = sorted(
            person_info.items(),
            key=lambda kv: len(kv[1]["mss"]),
            reverse=True,
        )[:max_nodes]
        included = {pid for pid, _ in sorted_persons}

        seen_edges: set[frozenset[str]] = set()
        links: list[dict[str, str]] = []
        for ms, persons in ms_persons.items():
            persons_in = [p for p in persons if p in included]
            for i in range(len(persons_in)):
                for j in range(i + 1, len(persons_in)):
                    key: frozenset = frozenset([persons_in[i], persons_in[j]])
                    if key not in seen_edges:
                        seen_edges.add(key)
                        links.append({
                            "source": persons_in[i],
                            "target": persons_in[j],
                            "ms":     ms,
                        })

        # Server-side spring layout — mirrors rdf_build.compute_layout
        g_nx = nx.Graph()
        for pid, _ in sorted_persons:
            g_nx.add_node(pid)
        for lnk in links:
            g_nx.add_edge(lnk["source"], lnk["target"])

        n = len(sorted_persons)
        pos: dict[str, tuple[float, float]] = nx.spring_layout(
            g_nx,
            scale=layout_scale,
            seed=layout_seed,
            iterations=60,
            k=1.2 / max(1.0, n ** 0.5),
        ) if n > 0 else {}

        nodes = [
            {
                "id":       pid,
                "label":    labels.get(pid, pid.rsplit("#", 1)[-1].rsplit("/", 1)[-1]),
                "role":     info["role"],
                "ms_count": len(info["mss"]),
                "x":        float(pos[pid][0]) if pid in pos else 0.0,
                "y":        float(pos[pid][1]) if pid in pos else 0.0,
            }
            for pid, info in sorted_persons
        ]

        return {"nodes": nodes, "links": links}
    except Exception as exc:
        logger.warning("people_network query failed: %s", exc)
        return {"nodes": [], "links": []}


# ── Ownership chains ───────────────────────────────────────────────────

_OWNERSHIP_Q = """
SELECT DISTINCT ?ms ?owner WHERE {
  ?ms hm:has_owner ?owner .
}
"""


def query_ownership_chains(graph: rdflib.Graph) -> list[dict[str, Any]]:
    """Per-manuscript ownership sequences.

    Returns [{ms, ms_label, owners: [{name, uri}]}].
    """
    if len(graph) == 0:
        return []
    try:
        labels = _label_map(graph)
        ms_owners: dict[str, list[str]] = {}
        for row in graph.query(_OWNERSHIP_Q, initNs=_INIT_NS):
            ms    = str(row.ms)
            owner = str(row.owner)
            ms_owners.setdefault(ms, []).append(owner)

        result = []
        for ms, owners in ms_owners.items():
            if not owners:
                continue
            result.append({
                "ms":       ms,
                "ms_label": labels.get(ms, ms.rsplit("/", 1)[-1]),
                "owners":   [
                    {
                        "name": labels.get(o, o.rsplit("#", 1)[-1].rsplit("/", 1)[-1]),
                        "uri":  o,
                    }
                    for o in dict.fromkeys(owners)
                ],
            })
        result.sort(key=lambda r: len(r["owners"]), reverse=True)
        return result
    except Exception as exc:
        logger.warning("ownership_chains query failed: %s", exc)
        return []


# ── Geography ──────────────────────────────────────────────────────────

_GEO_Q = """
SELECT DISTINCT ?ms ?place ?type WHERE {
  {
    ?ms hm:has_production_event ?event .
    ?event hm:has_production_place ?place .
    BIND("production" AS ?type)
  }
  UNION {
    ?ms hm:has_production_place ?place .
    BIND("production" AS ?type)
  }
  UNION {
    ?ms hm:mentions_place ?place .
    BIND("mentioned" AS ?type)
  }
}
"""

_COORDS_Q = """
SELECT DISTINCT ?place ?lat ?lon WHERE {
  ?place wgs84:lat  ?lat ;
         wgs84:long ?lon .
}
"""


def query_geography(graph: rdflib.Graph) -> list[dict[str, Any]]:
    """Place associations for each manuscript with optional coordinates.

    Returns one row **per place** (not per ms×place pair):
    [{place, place_label, lat, lon, type, ms_count, ms_labels}].

    The frontend can filter by ``type`` and search on ``place_label``
    without any aggregation work.  ``type`` is the most-common type
    for the place when a place appears in both categories (rare); in
    practice this is always consistent.
    """
    if len(graph) == 0:
        return []
    try:
        labels = _label_map(graph)

        # Pre-build coords map
        coords: dict[str, tuple[float, float]] = {}
        try:
            for row in graph.query(_COORDS_Q, initNs=_INIT_NS):
                try:
                    coords[str(row.place)] = (float(str(row.lat)), float(str(row.lon)))
                except (ValueError, TypeError):
                    pass
        except Exception:
            pass

        # Aggregate: place → {place_label, lat, lon, type, ms URIs set, ms_labels list}
        place_map: dict[str, dict[str, Any]] = {}
        seen_ms_place: set[tuple[str, str, str]] = set()

        def _short_place(uri: str) -> str:
            return uri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]

        for row in graph.query(_GEO_Q, initNs=_INIT_NS):
            ms    = str(row.ms)
            place = str(row.place)
            ptype = str(row.type)
            triple_key = (ms, place, ptype)
            if triple_key in seen_ms_place:
                continue
            seen_ms_place.add(triple_key)

            ms_label = labels.get(ms, ms.rsplit("/", 1)[-1])
            lat, lon = coords.get(place, (None, None))  # type: ignore[assignment]

            if place not in place_map:
                place_map[place] = {
                    "place":       place,
                    "place_label": labels.get(place, _short_place(place)),
                    "lat":         lat,
                    "lon":         lon,
                    "type":        ptype,
                    "_ms_set":     set(),
                    "ms_labels":   [],
                }
            entry = place_map[place]
            if ms not in entry["_ms_set"]:
                entry["_ms_set"].add(ms)
                entry["ms_labels"].append(ms_label)

        result = []
        for entry in place_map.values():
            result.append({
                "place":       entry["place"],
                "place_label": entry["place_label"],
                "lat":         entry["lat"],
                "lon":         entry["lon"],
                "type":        entry["type"],
                "ms_count":    len(entry["_ms_set"]),
                "ms_labels":   sorted(entry["ms_labels"]),
            })
        result.sort(key=lambda r: -r["ms_count"])
        return result
    except Exception as exc:
        logger.warning("geography query failed: %s", exc)
        return []


def query_geography_heatmap(graph: rdflib.Graph) -> list[dict[str, Any]]:
    """Weighted geographic points for heatmap rendering.

    Returns one entry **per place** (not per ms×place pair):
    [{place, place_label, lat, lon, weight, type}]

    ``weight`` is the number of manuscripts associated with the place.
    ``type`` is "production", "mentioned", or "both" when the place
    appears in both categories.

    Places without wgs84 coordinates are excluded.
    """
    if len(graph) == 0:
        return []
    try:
        labels = _label_map(graph)

        # Build coords map
        coords: dict[str, tuple[float, float]] = {}
        try:
            for row in graph.query(_COORDS_Q, initNs=_INIT_NS):
                try:
                    coords[str(row.place)] = (float(str(row.lat)), float(str(row.lon)))
                except (ValueError, TypeError):
                    pass
        except Exception:
            pass

        # Aggregate place → {ms_set, types_set}
        place_map: dict[str, dict[str, Any]] = {}
        seen: set[tuple[str, str]] = set()

        for row in graph.query(_GEO_Q, initNs=_INIT_NS):
            ms    = str(row.ms)
            place = str(row.place)
            ptype = str(row.type)
            key   = (ms, place)
            if key in seen:
                continue
            seen.add(key)

            if place not in place_map:
                place_map[place] = {"ms_set": set(), "types": set()}
            place_map[place]["ms_set"].add(ms)
            place_map[place]["types"].add(ptype)

        result: list[dict[str, Any]] = []
        for place, data in place_map.items():
            if place not in coords:
                continue
            lat, lon = coords[place]
            types = data["types"]
            if len(types) > 1:
                ptype = "both"
            else:
                ptype = next(iter(types))
            result.append({
                "place":       place,
                "place_label": labels.get(place, place.rsplit("/", 1)[-1]),
                "lat":         lat,
                "lon":         lon,
                "weight":      len(data["ms_set"]),
                "type":        ptype,
            })

        result.sort(key=lambda r: r["weight"], reverse=True)
        return result
    except Exception as exc:
        logger.warning("geography heatmap query failed: %s", exc)
        return []


# ── Summary ────────────────────────────────────────────────────────────

# The mapper types a manuscript as lrmoo:F4_Manifestation_Singleton (the physical
# item) and hm:Bibliographic_Unit on the same URI — it never emits hm:Manuscript_Object.
# Count the distinct URI carrying either class so the total survives vocab drift.
_MS_COUNT_Q     = (
    "SELECT (COUNT(DISTINCT ?ms) AS ?n) WHERE { "
    "{ ?ms rdf:type lrmoo:F4_Manifestation_Singleton . } "
    "UNION { ?ms rdf:type hm:Bibliographic_Unit . } }"
)
_WORK_COUNT_Q   = "SELECT (COUNT(DISTINCT ?w)  AS ?n) WHERE { ?ms hm:has_work ?w . }"
_PERSON_COUNT_Q = "SELECT (COUNT(DISTINCT ?p)  AS ?n) WHERE { ?p rdf:type cidoc:E21_Person . }"
_PLACE_COUNT_Q  = "SELECT (COUNT(DISTINCT ?pl) AS ?n) WHERE { ?pl rdf:type cidoc:E53_Place . }"
_SCRIBE_Q  = "SELECT (COUNT(DISTINCT ?p) AS ?n) WHERE { ?ms hm:has_scribe ?p . }"
_OWNER_Q   = "SELECT (COUNT(DISTINCT ?p) AS ?n) WHERE { ?ms hm:has_owner  ?p . }"
_AUTHOR_Q  = "SELECT (COUNT(DISTINCT ?p) AS ?n) WHERE { ?w  hm:has_author ?p . }"


def _count(graph: rdflib.Graph, sparql: str) -> int:
    try:
        for row in graph.query(sparql, initNs=_INIT_NS):
            return int(str(row.n))
    except Exception:
        pass
    return 0


# ── Provenance timeline ────────────────────────────────────────────────

_PROV_DATE_Q = """
SELECT ?certDate ?earliest ?latest ?prodPlace WHERE {
  OPTIONAL { <{ms}> hm:has_production_date_certain ?certDate . }
  OPTIONAL { <{ms}> hm:earliest_possible_date      ?earliest . }
  OPTIONAL { <{ms}> hm:latest_possible_date        ?latest   . }
  OPTIONAL { <{ms}> hm:has_production_place        ?prodPlace . }
}
LIMIT 1
"""

_PROV_OWNERS_Q = """
SELECT DISTINCT ?owner WHERE {
  <{ms}> hm:has_owner ?owner .
}
"""


def query_provenance(
    graph: rdflib.Graph,
    ms_uri: str,
) -> list[dict[str, Any]]:
    """Return ordered provenance events for a single manuscript.

    Events:
      - type="production"  — production date + optional place
      - type="ownership"   — each hm:has_owner person

    Each event has: {type, label, uri|None, year|None,
    year_earliest|None, year_latest|None, place|None}

    Returns an empty list if the manuscript has no provenance data (no
    dates, no owners) — callers should still return 200 in that case.
    """
    if len(graph) == 0:
        return []

    labels = _label_map(graph)

    def _short(uri: str) -> str:
        return uri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]

    def _label(uri: str) -> str:
        return labels.get(uri, _short(uri))

    def _year(val: Any) -> int | None:
        try:
            return int(str(val).split("-")[0].split("T")[0])
        except (ValueError, TypeError, AttributeError):
            return None

    events: list[dict[str, Any]] = []

    # Production event
    try:
        date_q = _PROV_DATE_Q.replace("{ms}", ms_uri)
        for row in graph.query(date_q, initNs=_INIT_NS):
            cert   = _year(row.certDate)  if row.certDate  else None
            early  = _year(row.earliest)  if row.earliest  else None
            late   = _year(row.latest)    if row.latest    else None
            place_uri = str(row.prodPlace) if row.prodPlace else None
            if cert is not None or early is not None or late is not None:
                events.append({
                    "type":          "production",
                    "label":         _label(place_uri) if place_uri else "Production",
                    "uri":           place_uri,
                    "year":          cert,
                    "year_earliest": early,
                    "year_latest":   late,
                    "place":         place_uri,
                })
    except Exception as exc:
        logger.warning("provenance date query failed for %s: %s", ms_uri, exc)

    # Ownership events
    try:
        owners_q = _PROV_OWNERS_Q.replace("{ms}", ms_uri)
        for row in graph.query(owners_q, initNs=_INIT_NS):
            owner_uri = str(row.owner)
            events.append({
                "type":          "ownership",
                "label":         _label(owner_uri),
                "uri":           owner_uri,
                "year":          None,
                "year_earliest": None,
                "year_latest":   None,
                "place":         None,
            })
    except Exception as exc:
        logger.warning("provenance owners query failed for %s: %s", ms_uri, exc)

    return events


def query_summary(graph: rdflib.Graph) -> dict[str, Any]:
    """Aggregate statistics about the merged graph."""
    return {
        "total_manuscripts": _count(graph, _MS_COUNT_Q),
        "total_works":       _count(graph, _WORK_COUNT_Q),
        "total_persons":     _count(graph, _PERSON_COUNT_Q),
        "total_places":      _count(graph, _PLACE_COUNT_Q),
        "persons_by_role": {
            "scribe": _count(graph, _SCRIBE_Q),
            "owner":  _count(graph, _OWNER_Q),
            "author": _count(graph, _AUTHOR_Q),
        },
        "triples": len(graph),
    }
