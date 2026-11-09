"""Shared streaming serialisers for section-level export.

All async generators yield ``bytes`` so callers can wrap them in a
``StreamingResponse`` without buffering the full payload in memory.

Heavy operations (rdflib serialisation) run in ``asyncio.to_thread``
per Rule W-8 — never block the event loop.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import re
import uuid
from collections.abc import AsyncIterator, Iterable
from datetime import date, datetime
from typing import Any, NamedTuple


# ── JSON ─────────────────────────────────────────────────────────────


def json_default(value: Any) -> Any:
    """JSON encoder fallback shared by the streaming serialisers."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serialisable")


# Backwards-compatible private alias (older call sites).
_json_default = json_default


async def json_stream(payload: dict[str, Any] | list[Any]) -> AsyncIterator[bytes]:
    """Yield the payload as 64 KB chunks of UTF-8 encoded JSON."""
    encoded = json.dumps(payload, default=_json_default, ensure_ascii=False).encode("utf-8")
    chunk = 64 * 1024
    for i in range(0, len(encoded), chunk):
        yield encoded[i: i + chunk]


async def json_array_stream(
    header: dict[str, Any],
    items_key: str,
    items: AsyncIterator[dict[str, Any]] | Iterable[dict[str, Any]],
) -> AsyncIterator[bytes]:
    """Stream a ``{...header, "<items_key>": [item, item, ...]}`` document.

    Unlike :func:`json_stream`, which serialises the whole payload before
    yielding, this emits the header prefix first, then one serialised item
    per yield, then the closing bracket — memory stays O(1 item) and the
    first bytes go out before any slow item generation starts (Heroku's
    router kills a request that sends no data within 30 s).

    The item dicts are serialised one at a time with the same defaults as
    :func:`json_stream`, so the document is byte-compatible with a plain
    ``json.dumps`` of ``{**header, items_key: [...]}``.
    """
    prefix = json.dumps(header, default=_json_default, ensure_ascii=False)
    opener = f'"{items_key}":['
    yield (f"{prefix[:-1]},{opener}" if header else f"{{{opener}").encode("utf-8")
    first = True

    def _encode(item: dict[str, Any]) -> bytes:
        nonlocal first
        sep = b"" if first else b","
        first = False
        return sep + json.dumps(item, default=_json_default, ensure_ascii=False).encode("utf-8")

    if hasattr(items, "__aiter__"):
        async for item in items:
            yield _encode(item)
    else:
        for item in items:
            yield _encode(item)
    yield b"]}"


# ── CSV ──────────────────────────────────────────────────────────────


async def csv_stream(
    rows: list[dict[str, Any]],
    fieldnames: list[str],
) -> AsyncIterator[bytes]:
    """Yield the rows as UTF-8 CSV bytes (header + data, 64 KB chunks).

    Fields missing from a row are rendered as empty strings. Extra
    fields not in ``fieldnames`` are silently dropped so the column
    set is always the declared schema.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=fieldnames,
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writeheader()
    for row in rows:
        # Normalise any non-string scalars so csv doesn't write repr().
        safe: dict[str, Any] = {}
        for k in fieldnames:
            v = row.get(k)
            if v is None:
                safe[k] = ""
            elif isinstance(v, (datetime, date)):
                safe[k] = v.isoformat()
            elif isinstance(v, (dict, list)):
                safe[k] = json.dumps(v, ensure_ascii=False)
            else:
                safe[k] = v
        writer.writerow(safe)

    encoded = buf.getvalue().encode("utf-8-sig")  # BOM so Excel opens UTF-8 correctly
    chunk = 64 * 1024
    for i in range(0, len(encoded), chunk):
        yield encoded[i: i + chunk]


# ── RDF (TTL / NT) ───────────────────────────────────────────────────


async def ttl_stream(graph: Any) -> AsyncIterator[bytes]:
    """Serialise an rdflib.Graph to Turtle and stream in 64 KB chunks.

    ``rdflib.Graph.serialize`` is CPU-bound so it runs in a threadpool.
    """
    ttl_bytes: bytes = await asyncio.to_thread(
        lambda: graph.serialize(format="turtle").encode("utf-8"),
    )
    chunk = 64 * 1024
    for i in range(0, len(ttl_bytes), chunk):
        yield ttl_bytes[i: i + chunk]


async def nt_stream(graph: Any) -> AsyncIterator[bytes]:
    """Serialise an rdflib.Graph to N-Triples and stream in 64 KB chunks."""
    nt_bytes: bytes = await asyncio.to_thread(
        lambda: graph.serialize(format="nt").encode("utf-8"),
    )
    chunk = 64 * 1024
    for i in range(0, len(nt_bytes), chunk):
        yield nt_bytes[i: i + chunk]


# ── Wikidata / Wikibase items → RDF ──────────────────────────────────

_WD_PROP_PREFIX = "http://www.wikidata.org/prop/direct/"
_WD_PROP_BASE = "http://www.wikidata.org/prop/"
_WD_STATEMENT_PREFIX = "http://www.wikidata.org/prop/statement/"
_WD_QUALIFIER_PREFIX = "http://www.wikidata.org/prop/qualifier/"
_WD_REFERENCE_PREFIX = "http://www.wikidata.org/prop/reference/"
_WD_STATEMENT_NODE_PREFIX = "http://www.wikidata.org/entity/statement/"
_WD_REFERENCE_NODE_PREFIX = "http://www.wikidata.org/reference/"
_WIKIBASE_ONTOLOGY_PREFIX = "http://wikiba.se/ontology#"
_PROV_PREFIX = "http://www.w3.org/ns/prov#"
_WIKIDATA_ENTITY_PREFIX = "http://www.wikidata.org/entity/"
_RANK_NODE_NAMES = {
    "normal": "NormalRank",
    "preferred": "PreferredRank",
    "deprecated": "DeprecatedRank",
}
_PROPERTY_ID_PATTERN = re.compile(r"P[1-9][0-9]*")
_ITEM_ID_PATTERN = re.compile(r"Q[1-9][0-9]*")


class _WdNamespaces(NamedTuple):
    direct: Any
    prop: Any
    statement: Any
    qualifier: Any
    reference: Any
    statement_node: Any
    reference_node: Any
    wikibase: Any
    prov: Any


def _wd_namespaces(rdflib: Any) -> _WdNamespaces:
    namespace = rdflib.Namespace
    return _WdNamespaces(
        direct=namespace(_WD_PROP_PREFIX),
        prop=namespace(_WD_PROP_BASE),
        statement=namespace(_WD_STATEMENT_PREFIX),
        qualifier=namespace(_WD_QUALIFIER_PREFIX),
        reference=namespace(_WD_REFERENCE_PREFIX),
        statement_node=namespace(_WD_STATEMENT_NODE_PREFIX),
        reference_node=namespace(_WD_REFERENCE_NODE_PREFIX),
        wikibase=namespace(_WIKIBASE_ONTOLOGY_PREFIX),
        prov=namespace(_PROV_PREFIX),
    )


def _digest(*parts: object) -> str:
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _property_id(raw: object) -> str:
    text = str(raw or "").removeprefix("wdt:")
    if text.isdigit():
        text = f"P{text}"
    if not _PROPERTY_ID_PATTERN.fullmatch(text):
        raise ValueError(f"Invalid Wikidata property id {raw!r}; cannot export to RDF")
    return text


def _rank_name(raw: object) -> str:
    rank = str(raw or "normal").strip().lower()
    if rank not in _RANK_NODE_NAMES:
        raise ValueError(f"Unknown statement rank {raw!r}; cannot export to RDF")
    return rank


def _value_node(value: object) -> Any:
    import rdflib  # noqa: PLC0415 — Rule W-7

    if isinstance(value, str):
        if _ITEM_ID_PATTERN.fullmatch(value):
            return rdflib.URIRef(_WIKIDATA_ENTITY_PREFIX + value)
        return rdflib.Literal(value)
    if isinstance(value, (int, float)):
        return rdflib.Literal(value)
    return rdflib.Literal(str(value))


def _snak_pair(snak: object, predicate_namespace: Any) -> tuple[Any, Any]:
    """Return the predicate and object for a qualifier or reference snak."""
    if not isinstance(snak, dict):
        raise ValueError("Qualifier and reference snaks must be objects; cannot export to RDF")
    pid = _property_id(snak.get("property") or snak.get("property_id"))
    value = snak.get("value")
    if value in (None, ""):
        raise ValueError(f"{pid} qualifier or reference has no value; cannot export to RDF")
    return predicate_namespace[pid], _value_node(value)


def _reference_groups(claim: dict[str, Any]) -> list[list[Any]]:
    """One snak group per reference node; ``reference_blocks`` wins over flat references."""
    blocks = claim.get("reference_blocks")
    if isinstance(blocks, list) and blocks:
        return [block for block in blocks if block]
    groups: list[list[Any]] = []
    for reference in claim.get("references") or []:
        if isinstance(reference, dict) and isinstance(reference.get("snaks"), list):
            groups.append(reference["snaks"])
        else:
            groups.append([reference])
    return [group for group in groups if group]


def _parse_claim(claim: object) -> tuple[str, Any, str, dict[str, Any]] | None:
    """Return (property id, value, rank, claim), or None when the claim has no value."""
    if not isinstance(claim, dict):
        raise ValueError("Statement must be an object; cannot export to RDF")
    pid = _property_id(claim.get("property") or claim.get("property_id") or claim.get("pid"))
    raw_value = (
        claim.get("value") or claim.get("datavalue")
        or (claim.get("mainsnak") or {}).get("datavalue")
    )
    if raw_value is None:
        return None
    return pid, _value_node(raw_value), _rank_name(claim.get("rank")), claim


def _best_rank_by_property(parsed: list[tuple[str, Any, str, dict[str, Any]]]) -> dict[str, str]:
    """Truthy rank per property: ``preferred`` if any exists, else ``normal``."""
    ranks: dict[str, set[str]] = {}
    for pid, _value, rank, _claim in parsed:
        ranks.setdefault(pid, set()).add(rank)
    best: dict[str, str] = {}
    for pid, found in ranks.items():
        if "preferred" in found:
            best[pid] = "preferred"
        elif "normal" in found:
            best[pid] = "normal"
    return best


def _add_claims(g: Any, ns: _WdNamespaces, subj: Any, claims: list[Any]) -> None:
    """Write each statement as a Wikidata statement node, with its truthy triple when best."""
    parsed = [entry for entry in (_parse_claim(claim) for claim in claims) if entry is not None]
    best = _best_rank_by_property(parsed)
    for pid, value, rank, claim in parsed:
        node = ns.statement_node[_digest(str(subj), claim)]
        g.add((subj, ns.prop[pid], node))
        g.add((node, ns.statement[pid], value))
        g.add((node, ns.wikibase["rank"], ns.wikibase[_RANK_NODE_NAMES[rank]]))
        if best.get(pid) == rank:
            g.add((subj, ns.direct[pid], value))
        for snak in claim.get("qualifiers") or []:
            predicate, qualifier_value = _snak_pair(snak, ns.qualifier)
            g.add((node, predicate, qualifier_value))
        for group in _reference_groups(claim):
            reference = ns.reference_node[_digest(group)]
            g.add((node, ns.prov["wasDerivedFrom"], reference))
            for snak in group:
                predicate, reference_value = _snak_pair(snak, ns.reference)
                g.add((reference, predicate, reference_value))


def items_to_rdf_graph(items: list[dict[str, Any]], base_uri: str) -> Any:
    """Convert a ``StudioBuildResponse.items`` list to an rdflib.Graph.

    Emits:
    - ``rdfs:label`` for every label (language-tagged literal).
    - ``schema:description`` for every description.
    - Wikidata statement nodes for every claim (``p:``, ``ps:``, ``wikibase:rank``),
      with ``pq:`` qualifiers and ``prov:wasDerivedFrom`` reference nodes (``pr:``).
    - Truthy ``wdt:`` triples only for the best non-deprecated rank of each property,
      so a deprecated statement never asserts a direct value.

    Raises ``ValueError`` when a statement cannot be represented faithfully
    (invalid property id, unknown rank, or a qualifier or reference without a value).

    ``base_uri`` is ``http://www.wikidata.org/entity/`` for Wikidata
    exports and ``https://mhm-hmo.wikibase.cloud/entity/`` for Wikibase.

    This function is synchronous and runs inside ``asyncio.to_thread``.
    The callers (ttl_stream / nt_stream) already handle threading.
    """
    import rdflib  # noqa: PLC0415 — Rule W-7

    RDFS = rdflib.namespace.RDFS
    SCHEMA = rdflib.Namespace("https://schema.org/")
    ns = _wd_namespaces(rdflib)

    g = rdflib.Graph()
    g.bind("rdfs", RDFS)
    g.bind("schema", SCHEMA)
    g.bind("wdt", ns.direct)
    g.bind("p", ns.prop)
    g.bind("ps", ns.statement)
    g.bind("pq", ns.qualifier)
    g.bind("pr", ns.reference)
    g.bind("wikibase", ns.wikibase)
    g.bind("prov", ns.prov)

    for item in items:
        # Use the QID when available; fall back to the local id.
        item_id = item.get("qid") or item.get("id") or item.get("local_id") or ""
        if not item_id:
            continue
        # Absolute URI or prefix with base_uri
        if item_id.startswith("http"):
            subj = rdflib.URIRef(item_id)
        else:
            subj = rdflib.URIRef(base_uri + item_id)

        # Labels
        for lang, val in (item.get("labels") or {}).items():
            if val:
                g.add((subj, RDFS.label, rdflib.Literal(str(val), lang=lang)))

        # Descriptions
        for lang, val in (item.get("descriptions") or {}).items():
            if val:
                g.add((subj, SCHEMA.description, rdflib.Literal(str(val), lang=lang)))

        _add_claims(g, ns, subj, list(item.get("claims") or item.get("statements") or []))

    return g
