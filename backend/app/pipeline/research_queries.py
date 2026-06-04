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
    max_pairs: int = 1000,
) -> list[dict[str, Any]]:
    """Pairs of works that appear together in the same manuscript.

    Returns list of {ms, work1, work1_label, work2, work2_label}.
    """
    if len(graph) == 0:
        return []
    try:
        labels = _label_map(graph)
        ms_works: dict[str, list[str]] = {}
        for row in graph.query(_CO_OCCUR_Q, initNs=_INIT_NS):
            ms = str(row.ms)
            work = str(row.work)
            ms_works.setdefault(ms, []).append(work)

        pairs: list[dict[str, Any]] = []
        for ms, works in ms_works.items():
            works = list(dict.fromkeys(works))
            for i in range(len(works)):
                for j in range(i + 1, len(works)):
                    pairs.append({
                        "ms":          ms,
                        "work1":       works[i],
                        "work1_label": labels.get(works[i], works[i].rsplit("#", 1)[-1].rsplit("/", 1)[-1]),
                        "work2":       works[j],
                        "work2_label": labels.get(works[j], works[j].rsplit("#", 1)[-1].rsplit("/", 1)[-1]),
                    })
                    if len(pairs) >= max_pairs:
                        return pairs
        return pairs
    except Exception as exc:
        logger.warning("co_occurrence query failed: %s", exc)
        return []


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
) -> dict[str, Any]:
    """Social network of persons linked to the same manuscripts.

    Returns {nodes: [{id, label, role, ms_count}], links: [{source, target, ms}]}.
    """
    if len(graph) == 0:
        return {"nodes": [], "links": []}
    try:
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

        nodes = [
            {
                "id":       pid,
                "label":    labels.get(pid, pid.rsplit("#", 1)[-1].rsplit("/", 1)[-1]),
                "role":     info["role"],
                "ms_count": len(info["mss"]),
            }
            for pid, info in sorted_persons
        ]

        seen_edges: set[frozenset[str]] = set()
        links: list[dict[str, str]] = []
        for ms, persons in ms_persons.items():
            persons_in = [p for p in persons if p in included]
            for i in range(len(persons_in)):
                for j in range(i + 1, len(persons_in)):
                    key = frozenset([persons_in[i], persons_in[j]])
                    if key not in seen_edges:
                        seen_edges.add(key)
                        links.append({
                            "source": persons_in[i],
                            "target": persons_in[j],
                            "ms":     ms,
                        })

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

    Returns [{ms, ms_label, place, place_label, lat, lon, type}].
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

        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for row in graph.query(_GEO_Q, initNs=_INIT_NS):
            ms    = str(row.ms)
            place = str(row.place)
            ptype = str(row.type)
            key   = (ms, place, ptype)
            if key in seen:
                continue
            seen.add(key)
            lat, lon = coords.get(place, (None, None))  # type: ignore[assignment]
            result.append({
                "ms":          ms,
                "ms_label":    labels.get(ms, ms.rsplit("/", 1)[-1]),
                "place":       place,
                "place_label": labels.get(place, place.rsplit("#", 1)[-1].rsplit("/", 1)[-1]),
                "lat":         lat,
                "lon":         lon,
                "type":        ptype,
            })
        return result
    except Exception as exc:
        logger.warning("geography query failed: %s", exc)
        return []


# ── Summary ────────────────────────────────────────────────────────────

_MS_COUNT_Q     = "SELECT (COUNT(DISTINCT ?ms) AS ?n) WHERE { ?ms rdf:type hm:Manuscript_Object . }"
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
