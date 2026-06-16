"""Cross-source aggregation for the Linked Data Explorer Overview.

The Overview historically counted entities from the run's RDF graph only.
This module merges three sources into a single deduplicated set of entities,
recording which source(s) each merged entity came from:

  * ``rdf``      — the project's merged HMO graph (manuscripts.ttl)
  * ``wikidata`` — entities in the run that reconcile to a real Wikidata QID
                   (from the already-built Wikidata Studio items)
  * ``wikibase`` — entities in the project's own Wikibase (live SPARQL).
                   Degrades to nothing when WIKIBASE_SPARQL_URL is unset or
                   the endpoint is unreachable.

Counts are the number of distinct *merged* entities per type; ``by_source``
records how many of those carry each source. A single manuscript present in
both the RDF graph and Wikidata adds 1 to ``total`` and 1 to each of the
``rdf`` and ``wikidata`` sub-counts.
"""
from __future__ import annotations

import logging
import unicodedata
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import rdflib

from converter.config.vocabularies import ROLE_MAPPINGS

from app.pipeline.research_queries import (
    _AUTHOR_Q,
    _INIT_NS,
    _OWNER_Q,
    _SCRIBE_Q,
    _count,
    _label_map,
)

logger = logging.getLogger(__name__)

SOURCE_RDF = "rdf"
SOURCE_WIKIBASE = "wikibase"
SOURCE_WIKIDATA = "wikidata"
SOURCES = (SOURCE_RDF, SOURCE_WIKIBASE, SOURCE_WIKIDATA)

ENTITY_TYPES = ("manuscript", "work", "person", "place")

# Manuscript URIs are minted as ``<HM>MS_<normalize_string(control_number)>``
# and NLI-matched person/work/place URIs as ``<NLI_AUTHORITY_BASE><id>``
# (converter/transformer/uri_generator.py).
NLI_AUTHORITY_BASE = "https://www.nli.org.il/en/authorities/"

# External-id properties carried on Wikidata items (converter item builder).
_VIAF_PID = "P214"   # VIAF id (persons)
_J9U_PID = "P8189"   # National Library of Israel J9U authority id
_NNL_PID = "P3959"   # NLI / NNL manuscript catalogue id

_PREFIXES = (
    "PREFIX hm: <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#>\n"
    "PREFIX cidoc: <http://www.cidoc-crm.org/cidoc-crm/>\n"
    "PREFIX lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/>\n"
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
)

# WHERE patterns binding ``?uri`` to an entity of each type. Reused verbatim
# against the local rdflib graph (with initNs) and the remote Wikibase
# endpoint (with a PREFIX preamble), so both sources are typed identically.
_BODIES: dict[str, str] = {
    "manuscript": "{ ?uri a lrmoo:F4_Manifestation_Singleton . } "
                  "UNION { ?uri a hm:Bibliographic_Unit . }",
    "work":       "?ms hm:has_work ?uri .",
    "person":     "?uri a cidoc:E21_Person .",
    "place":      "?uri a cidoc:E53_Place .",
}


def _local_select(entity_type: str) -> str:
    return f"SELECT DISTINCT ?uri WHERE {{ {_BODIES[entity_type]} }}"


def _remote_select(entity_type: str) -> str:
    return (
        f"{_PREFIXES}\nSELECT DISTINCT ?uri ?label WHERE {{ "
        f"{_BODIES[entity_type]} OPTIONAL {{ ?uri rdfs:label ?label }} }}"
    )


# ── Data model ────────────────────────────────────────────────────────────

@dataclass
class ProviderEntity:
    """One source's view of an entity, before cross-source dedup."""
    entity_type: str
    label: str
    source: str
    qid: str | None = None
    control_number: str | None = None
    viaf_id: str | None = None
    nli_authority_id: str | None = None
    raw_uri: str | None = None


@dataclass
class MergedEntity:
    """A deduplicated entity, with the set of sources that contributed it."""
    id_key: str
    entity_type: str
    label: str
    sources: set[str] = field(default_factory=set)


# ── Normalisation ──────────────────────────────────────────────────────────

_uri_gen: Any = None


def _normalize_cn(control_number: str | None) -> str:
    """Reuse the converter's URI normalisation so a raw control number and an
    ``MS_<normalized>`` URI local-name collapse to the same merge key."""
    global _uri_gen
    if not control_number:
        return ""
    if _uri_gen is None:
        from converter.transformer.uri_generator import UriGenerator  # noqa: PLC0415
        _uri_gen = UriGenerator()
    return _uri_gen.normalize_string(control_number)


def _normalize_label(text: str | None) -> str:
    if not text:
        return ""
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(stripped.lower().split())


# ── Identity resolution ──────────────────────────────────────────────────

# Key precedence: a higher-precedence key always wins when picking a cluster's
# representative id_key.
_KEY_PRECEDENCE = ("qid:", "cn:", "viaf:", "nli:", "lbl:", "raw:")


def _entity_keys(e: ProviderEntity) -> list[str]:
    """All identity keys this entity exposes, highest precedence first.

    Keys that co-occur on one entity are later union-merged, so an entity that
    exposes both ``qid:Q1`` and ``cn:123`` links those two clusters together.

    The normalized-label key is a *fallback only* — emitted solely when the
    entity carries no strong identifier. Emitting it alongside a strong key
    would over-merge two genuinely-distinct entities that happen to share a
    label (e.g. two manuscripts with different control numbers but the same
    title).
    """
    keys: list[str] = []
    if e.qid:
        keys.append(f"qid:{e.qid}")
    if e.entity_type == "manuscript":
        cn = _normalize_cn(e.control_number)
        if cn:
            keys.append(f"cn:{cn}")
    if e.entity_type == "person":
        if e.viaf_id:
            keys.append(f"viaf:{e.viaf_id}")
        if e.nli_authority_id:
            keys.append(f"nli:{e.nli_authority_id}")
    if e.entity_type in ("place", "work") and e.nli_authority_id:
        keys.append(f"nli:{e.nli_authority_id}")
    if not keys:
        label = _normalize_label(e.label)
        if label:
            keys.append(f"lbl:{e.entity_type}:{label}")
    if not keys:
        keys.append(f"raw:{e.entity_type}:{e.raw_uri or id(e)}")
    return keys


def _best_key(keys: Iterable[str]) -> str:
    keys = list(keys)
    for prefix in _KEY_PRECEDENCE:
        for k in keys:
            if k.startswith(prefix):
                return k
    return keys[0]


def merge_entities(entities: list[ProviderEntity]) -> list[MergedEntity]:
    """Union-find dedup across sources, keyed by :func:`_entity_keys`."""
    parent: dict[str, str] = {}

    def find(k: str) -> str:
        parent.setdefault(k, k)
        root = k
        while parent[root] != root:
            root = parent[root]
        while parent[k] != root:
            parent[k], k = root, parent[k]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    per_entity_keys: list[list[str]] = []
    for e in entities:
        ks = _entity_keys(e)
        per_entity_keys.append(ks)
        for k in ks[1:]:
            union(ks[0], k)

    clusters: dict[str, dict[str, Any]] = {}
    for e, ks in zip(entities, per_entity_keys, strict=True):
        root = find(ks[0])
        c = clusters.setdefault(
            root, {"keys": set(), "sources": set(), "types": {}, "labels": []},
        )
        c["keys"].update(ks)
        c["sources"].add(e.source)
        c["types"][e.entity_type] = c["types"].get(e.entity_type, 0) + 1
        if e.label:
            c["labels"].append((e.source, e.label))

    merged: list[MergedEntity] = []
    for c in clusters.values():
        etype = max(c["types"], key=lambda t: c["types"][t])
        merged.append(MergedEntity(
            id_key=_best_key(c["keys"]),
            entity_type=etype,
            label=_best_label(c["labels"]),
            sources=set(c["sources"]),
        ))
    return merged


def _best_label(labels: list[tuple[str, str]]) -> str:
    """Prefer an RDF (Hebrew) label, then any non-empty label."""
    for source, label in labels:
        if source == SOURCE_RDF and label:
            return label
    for _source, label in labels:
        if label:
            return label
    return ""


# ── Providers ────────────────────────────────────────────────────────────

def rdf_provider(graph: rdflib.Graph) -> list[ProviderEntity]:
    """Entities present in the merged HMO RDF graph."""
    if len(graph) == 0:
        return []
    labels = _label_map(graph)
    out: list[ProviderEntity] = []
    for entity_type in ENTITY_TYPES:
        try:
            rows = list(graph.query(_local_select(entity_type), initNs=_INIT_NS))
        except Exception as exc:
            logger.warning("rdf_provider query failed for %s: %s", entity_type, exc)
            continue
        for row in rows:
            uri = str(row.uri)
            out.append(_rdf_entity(entity_type, uri, labels.get(uri, "")))
    return out


def _rdf_entity(entity_type: str, uri: str, label: str) -> ProviderEntity:
    local = uri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    control_number = None
    nli_authority_id = None
    if entity_type == "manuscript" and local.startswith("MS_"):
        control_number = local[3:]
    if uri.startswith(NLI_AUTHORITY_BASE):
        nli_authority_id = uri[len(NLI_AUTHORITY_BASE):]
    return ProviderEntity(
        entity_type=entity_type,
        label=label or local,
        source=SOURCE_RDF,
        control_number=control_number,
        nli_authority_id=nli_authority_id,
        raw_uri=uri,
    )


def rdf_persons_by_role(graph: rdflib.Graph) -> dict[str, int]:
    """Role counts are an RDF-only concept (Wikibase/Wikidata don't carry the
    project's role taxonomy)."""
    if len(graph) == 0:
        return {"scribe": 0, "owner": 0, "author": 0}
    return {
        "scribe": _count(graph, _SCRIBE_Q),
        "owner":  _count(graph, _OWNER_Q),
        "author": _count(graph, _AUTHOR_Q),
    }


_SCRIBE_PERSON_Q = """
SELECT DISTINCT ?p WHERE {
  ?ms hm:has_scribe ?p .
  ?p rdf:type cidoc:E21_Person .
}
"""

_OWNER_PERSON_Q = """
SELECT DISTINCT ?p WHERE {
  ?ms hm:has_owner ?p .
  ?p rdf:type cidoc:E21_Person .
}
"""

_AUTHOR_PERSON_Q = """
SELECT DISTINCT ?p WHERE {
  ?work hm:has_author ?p .
  ?ms hm:has_work ?work .
  ?p rdf:type cidoc:E21_Person .
}
"""


def _rdf_role_entities(graph: rdflib.Graph, query: str) -> list[ProviderEntity]:
    if len(graph) == 0:
        return []
    labels = _label_map(graph)
    try:
        rows = list(graph.query(query, initNs=_INIT_NS))
    except Exception as exc:
        logger.warning("rdf role query failed: %s", exc)
        return []
    out: list[ProviderEntity] = []
    for row in rows:
        uri = str(row.p)
        out.append(_rdf_entity("person", uri, labels.get(uri, "")))
    return out


def _authority_role_entities(matches: list[dict[str, Any]] | None, role: str) -> list[ProviderEntity]:
    out: list[ProviderEntity] = []
    for match in matches or []:
        if not isinstance(match, dict):
            continue
        if _role_bucket(match.get("role")) != role:
            continue
        if match.get("entity_kind") not in ("person", "organization"):
            continue
        label = str(match.get("entity_text") or match.get("matched_name") or "").strip()
        if not label:
            continue
        out.append(ProviderEntity(
            entity_type="person",
            label=label,
            source=SOURCE_WIKIDATA,
            qid=match.get("wikidata_qid") or None,
            viaf_id=match.get("viaf_id") or None,
            nli_authority_id=match.get("mazal_id") or None,
            raw_uri=label,
        ))
    return out


def aggregated_persons_by_role(
    graph: rdflib.Graph,
    authority_matches: list[dict[str, Any]] | None,
) -> dict[str, int]:
    """Distinct people by role across RDF plus approved authority matches."""
    role_entities = {
        "scribe": _rdf_role_entities(graph, _SCRIBE_PERSON_Q),
        "owner": _rdf_role_entities(graph, _OWNER_PERSON_Q),
        "author": _rdf_role_entities(graph, _AUTHOR_PERSON_Q),
    }
    for role in ("scribe", "owner", "author"):
        role_entities[role].extend(_authority_role_entities(authority_matches, role))
    return {role: len(merge_entities(entities)) for role, entities in role_entities.items()}


_STUDIO_TO_ENTITY_TYPE = {"manuscript": "manuscript", "person": "person", "work": "work"}

_ROLE_SCRIBE = {"scribe", "transcriber", "copyist", "editor"}
_ROLE_AUTHOR = {"author", "translator", "commentator"}


def _normalize_role(role: str | None) -> str:
    raw = (role or "").strip().lower().rstrip(".")
    return ROLE_MAPPINGS.get(raw, raw)


def _role_bucket(role: str | None) -> str | None:
    normalized = _normalize_role(role)
    if not normalized:
        return None
    if normalized in _ROLE_SCRIBE:
        return "scribe"
    if normalized in _ROLE_AUTHOR:
        return "author"
    if normalized.startswith("former_owner") or normalized.startswith("current_owner"):
        return "owner"
    if normalized in {"owner", "possessor", "provenance"}:
        return "owner"
    return None


def wikidata_provider(items: list[dict[str, Any]] | None) -> list[ProviderEntity]:
    """Entities from the run's built Wikidata Studio items that reconcile to a
    real Wikidata QID. QID-less items are local candidates, not yet on
    Wikidata, and are excluded (they are counted under the RDF source)."""
    out: list[ProviderEntity] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        entity_type = _STUDIO_TO_ENTITY_TYPE.get((item.get("entity_type") or "").strip())
        if entity_type is None:
            continue
        qid = item.get("existing_qid")
        if not (isinstance(qid, str) and qid.startswith("Q") and qid[1:].isdigit()):
            continue
        ext = _extract_external_ids(item)
        control_number = item.get("local_id") if entity_type == "manuscript" else None
        out.append(ProviderEntity(
            entity_type=entity_type,
            label=_best_item_label(item),
            source=SOURCE_WIKIDATA,
            qid=qid,
            control_number=control_number or ext.get("cn"),
            viaf_id=ext.get("viaf"),
            nli_authority_id=ext.get("nli"),
            raw_uri=qid,
        ))
    return out


def _extract_external_ids(item: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for stmt in item.get("statements") or []:
        if not isinstance(stmt, dict):
            continue
        pid = stmt.get("property") or stmt.get("property_id")
        value = stmt.get("value") or stmt.get("value_id")
        if not (isinstance(pid, str) and isinstance(value, str) and value):
            continue
        if pid == _VIAF_PID:
            out.setdefault("viaf", value)
        elif pid == _J9U_PID:
            out.setdefault("nli", value)
        elif pid == _NNL_PID:
            out.setdefault("cn", value)
    return out


def _best_item_label(item: dict[str, Any]) -> str:
    labels = item.get("labels") or {}
    if isinstance(labels, dict):
        for lang in ("he", "en"):
            if labels.get(lang):
                return str(labels[lang])
        for value in labels.values():
            if value:
                return str(value)
    return str(item.get("existing_qid") or "")


async def wikibase_provider(
    wikibase_url: str,
    runner: Callable[[str, str], Awaitable[dict[str, Any]]],
) -> list[ProviderEntity]:
    """Entities in the project's own Wikibase, queried live.

    Returns ``[]`` immediately when no endpoint is configured, and on any
    network/parse error — this source must never break the summary request.
    The Wikibase is assumed to mirror the HMO ontology (same class URIs).
    """
    if not wikibase_url:
        return []
    out: list[ProviderEntity] = []
    for entity_type in ENTITY_TYPES:
        try:
            data = await runner(wikibase_url, _remote_select(entity_type))
            out.extend(_parse_wikibase_bindings(entity_type, data))
        except Exception as exc:
            logger.warning("wikibase_provider failed for %s: %s", entity_type, exc)
            return []
    return out


def _parse_wikibase_bindings(entity_type: str, data: dict[str, Any]) -> list[ProviderEntity]:
    bindings = (data or {}).get("results", {}).get("bindings", [])
    out: list[ProviderEntity] = []
    for b in bindings:
        uri = (b.get("uri") or {}).get("value")
        if not uri:
            continue
        label = (b.get("label") or {}).get("value", "")
        e = _rdf_entity(entity_type, uri, label)
        e.source = SOURCE_WIKIBASE
        out.append(e)
    return out


# ── Summary assembly ───────────────────────────────────────────────────────

def build_aggregated_summary(
    merged: list[MergedEntity],
    persons_by_role: dict[str, int],
    triples: int,
    sources_available: dict[str, bool],
) -> dict[str, Any]:
    by_type: dict[str, dict[str, Any]] = {
        t: {"total": 0, "by_source": {s: 0 for s in SOURCES}}
        for t in ENTITY_TYPES
    }
    for m in merged:
        bt = by_type.get(m.entity_type)
        if bt is None:
            continue
        bt["total"] += 1
        for source in m.sources:
            if source in bt["by_source"]:
                bt["by_source"][source] += 1
    return {
        "total_manuscripts": by_type["manuscript"]["total"],
        "total_works":       by_type["work"]["total"],
        "total_persons":     by_type["person"]["total"],
        "total_places":      by_type["place"]["total"],
        "persons_by_role":   persons_by_role,
        "triples":           triples,
        "by_type":           by_type,
        "sources_available": sources_available,
    }


def compute_aggregated_summary(
    graph: rdflib.Graph,
    studio_items: list[dict[str, Any]] | None,
    wikibase_entities: list[ProviderEntity] | None,
    authority_matches: list[dict[str, Any]] | None = None,
    *,
    wikibase_configured: bool,
) -> dict[str, Any]:
    """Synchronous core: merge all sources into the aggregated summary.

    ``wikibase_entities`` is fetched by the caller (async httpx) and passed in
    so this function stays pure/threadpool-friendly.
    """
    rdf_entities = rdf_provider(graph)
    wd_entities = wikidata_provider(studio_items)
    wb_entities = list(wikibase_entities or [])
    merged = merge_entities(rdf_entities + wd_entities + wb_entities)
    sources_available = {
        SOURCE_RDF:      len(graph) > 0,
        SOURCE_WIKIDATA: bool(wd_entities),
        SOURCE_WIKIBASE: wikibase_configured,
    }
    return build_aggregated_summary(
        merged, aggregated_persons_by_role(graph, authority_matches), len(graph), sources_available,
    )
