"""Wikidata Studio backend — uses the *real* desktop item builder.

The desktop pipeline's ``converter.wikidata.item_builder.WikidataItemBuilder``
turns authority-enriched manuscript records into ``WikidataItem`` objects
covering every property the desktop ships with (P31 manuscript class,
P50 author, P136 genre, P407 language, …). The desktop pipeline's
``converter.wikidata.quickstatements.QuickStatementsExporter`` then
serialises them to the QuickStatements v2 text format.

This module is the thin glue: it stitches a run's records + its
*approved* authority matches into the desktop builder's input shape,
runs the builder in a threadpool (it's sync, by design), and returns
both the structured items and the QuickStatements blob.

NO logic is re-implemented here — every fix the desktop pipeline lands
(safety guards, Rule 23 / 38 modification gate, Rule 42 multi-P31,
Rule 47 work-item CREATE ordering, …) is one ``cp`` away.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from fastapi.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


async def build_items_for_run(
    *, marc_records: list[dict[str, Any]],
    approved_matches: list[dict[str, Any]],
    return_native: bool = False,
) -> dict[str, Any]:
    """Build Wikidata items + QuickStatements for *marc_records* enriched
    with *approved_matches*.

    Returns::

        {
            "items":           [ <dict-of-WikidataItem>, … ],
            "quickstatements": "CREATE\\nLAST  …",
            "summary":         { "total_items":int, "manuscripts":int,
                                  "persons":int, "works":int,
                                  "statements":int },
        }
    """
    # Build the desktop's expected per-record input shape:
    # each record carries its own ``marc_authority_matches`` list of
    # *approved* matches (mirroring what authority_enriched.json holds
    # in the desktop pipeline).
    by_cn: dict[str, list[dict[str, Any]]] = {}
    for m in approved_matches:
        by_cn.setdefault(m["control_number"], []).append(m)

    enriched: list[dict[str, Any]] = []
    for rec in marc_records:
        cn = str(rec.get("_control_number", ""))
        out = dict(rec)
        out["authors"]      = _to_dict_list(out.get("authors"),      default_role="author",      default_field="100")
        out["contributors"] = _to_dict_list(out.get("contributors"), default_role="contributor", default_field="700")
        out["subjects"]     = _to_dict_list(out.get("subjects"),     default_role="subject",     default_field="600")
        out["marc_authority_matches"] = [
            _approved_match_to_desktop_shape(m) for m in by_cn.get(cn, [])
        ]
        enriched.append(out)

    return await run_in_threadpool(_build_sync, enriched, return_native)


def _build_sync(records: list[dict[str, Any]], return_native: bool = False) -> dict[str, Any]:
    from converter.wikidata.item_builder import WikidataItemBuilder  # noqa: PLC0415
    from converter.wikidata.quickstatements import QuickStatementsExporter  # noqa: PLC0415

    builder = WikidataItemBuilder(reconciler=None)  # SPARQL-free for the web
    items = builder.build_all(records)

    exporter = QuickStatementsExporter()
    qs_text = exporter.export(items)

    summary = {
        "total_items": len(items),
        "manuscripts": sum(1 for i in items if getattr(i, "entity_type", "") == "manuscript"),
        "persons":     sum(1 for i in items if getattr(i, "entity_type", "") == "person"),
        "works":       sum(1 for i in items if getattr(i, "entity_type", "") == "work"),
        "statements":  sum(len(getattr(i, "statements", []) or []) for i in items),
    }

    return {
        "items": [_serialise_item(i) for i in items],
        "native_items": items if return_native else None,
        "quickstatements": qs_text,
        "summary": summary,
    }


def _approved_match_to_desktop_shape(m: dict[str, Any]) -> dict[str, Any]:
    """Re-shape a web ``authority_matches`` row into the dict the
    desktop ``WikidataItemBuilder`` reads off ``marc_authority_matches``.

    Desktop expects: name, role, field, mazal_id, viaf_uri (full URL),
    wikidata_qid, confidence (high/medium/low or float), sources,
    source_count, birth_year, death_year, preferred_name_lat, gnd_id,
    lc_id, isni, bnf_id, guard_flags, plus the curator's ``approved`` bool.
    """
    payload = m.get("payload") or {}
    cluster = payload.get("cluster_ids") or {}
    return {
        "name":               m.get("entity_text", ""),
        "role":               m.get("role", ""),
        "field":              "700/710/711",
        "mazal_id":           m.get("mazal_id", ""),
        "viaf_uri":           (
            f"https://viaf.org/viaf/{m['viaf_id']}"
            if m.get("viaf_id") else ""
        ),
        "wikidata_qid":       m.get("wikidata_qid", ""),
        "confidence":         m.get("confidence", "low"),
        "source":             m.get("source", ""),
        "sources":            payload.get("sources") or [],
        "source_count":       payload.get("source_count") or 1,
        "birth_year":         payload.get("birth_year"),
        "death_year":         payload.get("death_year"),
        "preferred_name_lat": payload.get("preferred_name_lat", ""),
        "gnd_id":             cluster.get("gnd", ""),
        "lc_id":              cluster.get("lccn", ""),
        "isni":               cluster.get("isni", ""),
        "bnf_id":             cluster.get("bnf", ""),
        "guard_flags":        payload.get("guard_flags") or [],
        "matched":            1,
        "approved":           True,
    }


def _serialise_item(item: Any) -> dict[str, Any]:
    """Best-effort dataclass-to-dict so the JSON response carries every
    label/description/alias/statement the builder emitted.

    ``WikidataItem`` is a dataclass; ``asdict`` recurses through its
    statements + qualifiers + references. The few non-dataclass values
    (Enum ranks, etc.) are coerced to strings.
    """
    try:
        return _coerce(asdict(item))
    except Exception:
        # Fall back to public attrs.
        return {
            "labels":       getattr(item, "labels", {}),
            "descriptions": getattr(item, "descriptions", {}),
            "aliases":      getattr(item, "aliases", {}),
            "statements":   [_coerce(s.__dict__) for s in getattr(item, "statements", [])],
            "existing_qid": getattr(item, "existing_qid", None),
            "entity_type":  getattr(item, "entity_type", None),
        }


def _to_dict_list(
    value: Any, *, default_role: str, default_field: str,
) -> list[dict[str, Any]]:
    """Desktop's WikidataItemBuilder iterates contributors / authors /
    subjects expecting each entry to be a dict (``entry.get('name', '')``).
    Web uploads sometimes carry plain strings; normalise here.
    """
    if value is None:
        return []
    out: list[dict[str, Any]] = []
    for v in value if isinstance(value, list) else [value]:
        if isinstance(v, dict):
            out.append(v)
        elif isinstance(v, str) and v.strip():
            out.append({"name": v.strip(), "role": default_role, "field": default_field})
    return out


def _coerce(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _coerce(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce(v) for v in value]
    if isinstance(value, tuple):
        return [_coerce(v) for v in value]
    if hasattr(value, "value") and hasattr(value, "name"):  # Enum-ish
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
