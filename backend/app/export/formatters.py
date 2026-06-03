"""Shared streaming serialisers for section-level export.

All async generators yield ``bytes`` so callers can wrap them in a
``StreamingResponse`` without buffering the full payload in memory.

Heavy operations (rdflib serialisation) run in ``asyncio.to_thread``
per Rule W-8 — never block the event loop.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any


# ── JSON ─────────────────────────────────────────────────────────────


def _json_default(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serialisable")


async def json_stream(payload: dict[str, Any] | list[Any]) -> AsyncIterator[bytes]:
    """Yield the payload as 64 KB chunks of UTF-8 encoded JSON."""
    encoded = json.dumps(payload, default=_json_default, ensure_ascii=False).encode("utf-8")
    chunk = 64 * 1024
    for i in range(0, len(encoded), chunk):
        yield encoded[i: i + chunk]


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
_WIKIDATA_ENTITY_PREFIX = "http://www.wikidata.org/entity/"

def items_to_rdf_graph(items: list[dict[str, Any]], base_uri: str) -> Any:
    """Convert a ``StudioBuildResponse.items`` list to an rdflib.Graph.

    Emits:
    - ``rdfs:label`` for every label (language-tagged literal).
    - ``schema:description`` for every description.
    - Direct Wikidata property triples for every claim
      (``wdt:P31``, ``wdt:P569``, ``wdt:P570``, etc.).

    ``base_uri`` is ``http://www.wikidata.org/entity/`` for Wikidata
    exports and ``https://mhm-hmo.wikibase.cloud/entity/`` for Wikibase.

    This function is synchronous and runs inside ``asyncio.to_thread``.
    The callers (ttl_stream / nt_stream) already handle threading.
    """
    import rdflib  # noqa: PLC0415 — Rule W-7

    RDFS = rdflib.namespace.RDFS
    SCHEMA = rdflib.Namespace("https://schema.org/")
    WDT = rdflib.Namespace(_WD_PROP_PREFIX)

    g = rdflib.Graph()
    g.bind("rdfs", RDFS)
    g.bind("schema", SCHEMA)
    g.bind("wdt", WDT)

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

        # Claims / statements — each claim is expected to carry:
        #   {"property": "P31", "value": "Q5"} or {"property": "P569", "value": "1850"}
        for claim in (item.get("claims") or item.get("statements") or []):
            prop = claim.get("property") or claim.get("pid") or ""
            if not prop:
                continue
            if not prop.startswith("P"):
                prop = prop.lstrip("wdt:").lstrip("P")
            pred = WDT[f"P{prop.lstrip('P')}"]
            val = claim.get("value") or claim.get("datavalue") or claim.get("mainsnak", {}).get("datavalue")
            if val is None:
                continue
            if isinstance(val, str):
                # Q-values become URIs; others become literals
                if val.startswith("Q"):
                    obj: rdflib.term.Node = rdflib.URIRef(_WIKIDATA_ENTITY_PREFIX + val)
                else:
                    obj = rdflib.Literal(val)
            elif isinstance(val, (int, float)):
                obj = rdflib.Literal(val)
            else:
                obj = rdflib.Literal(str(val))
            g.add((subj, pred, obj))

    return g
