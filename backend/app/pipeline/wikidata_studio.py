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

import hashlib
import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi.concurrency import run_in_threadpool

if TYPE_CHECKING:
    from app.models.extraction_approval import ExtractionApproval
    from app.models.item_override import WikidataItemOverride
    from app.models.run import AuthorityMatch, RunRecord

logger = logging.getLogger(__name__)


def compute_build_fingerprint(
    records: list["RunRecord"],
    all_matches: list["AuthorityMatch"],
    entity_rows: list["ExtractionApproval"],
    override_rows: list["WikidataItemOverride"],
    approved_only: bool,
) -> str:
    """SHA-256 fingerprint of everything that feeds the Wikidata item builder.

    Changing any approval flag, override field, match payload, or NER
    text will produce a different fingerprint and trigger a rebuild on
    the next request.
    """
    def _h(obj: Any) -> str:
        return hashlib.sha256(
            json.dumps(obj, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

    parts = {
        "approved_only": approved_only,
        "records": sorted(r.control_number for r in records),
        "matches": sorted(
            (
                str(m.id), m.approved, m.wikidata_qid or "", m.viaf_id or "",
                m.mazal_id or "", _h(m.payload or {}),
            )
            for m in all_matches
        ),
        "entities": sorted(
            (
                str(e.id), bool(e.approved),
                e.override_text or "", e.override_type or "", e.override_role or "",
            )
            for e in entity_rows
        ),
        "overrides": sorted(
            (
                str(o.id), o.local_id,
                _h({
                    "labels": o.labels, "descriptions": o.descriptions,
                    "aliases": o.aliases, "add_statements": o.add_statements,
                    "remove_statements": o.remove_statements,
                    "statement_edits": o.statement_edits,
                }),
            )
            for o in override_rows
        ),
    }
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, default=str).encode()
    ).hexdigest()


async def build_items_for_run(
    *, marc_records: list[dict[str, Any]],
    approved_matches: list[dict[str, Any]],
    entities_by_cn: dict[str, list[dict[str, Any]]] | None = None,
    return_native: bool = False,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build Wikidata items + QuickStatements for *marc_records* enriched
    with *approved_matches* and Stage-2 NER *entities*.

    ``entities_by_cn`` maps control_number → list of entity dicts in
    the desktop's expected shape (``source`` ∈ {``person_ner``,
    ``provenance_ner``, ``contents_ner``}, ``type``, ``text``,
    ``start``, ``end``, ``role``, ``confidence``). The desktop builder
    reads ``record["entities"]`` to drive work-item creation
    (contents_ner WORK + FOLIO + WORK_AUTHOR — see Rule 47) and the
    P127/P571 provenance fallbacks. Without this merge, the studio
    creates zero work items (the rest of the pipeline's authority
    surface comes through ``marc_authority_matches``).

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
    # in the desktop pipeline) plus its NER ``entities`` (mirroring
    # ner_results.json + the desktop's _merge_ner_into_records step).
    by_cn: dict[str, list[dict[str, Any]]] = {}
    for m in approved_matches:
        by_cn.setdefault(m["control_number"], []).append(m)
    ents_by_cn = entities_by_cn or {}

    from app.pipeline.marc_ingest import prepare_record_for_pipeline  # noqa: PLC0415

    enriched: list[dict[str, Any]] = []
    for rec in marc_records:
        out = prepare_record_for_pipeline(rec)
        cn = str(
            out.get("_control_number")
            or out.get("control_number")
            or out.get("controlNumber")
            or ""
        )
        out["authors"]      = _to_dict_list(out.get("authors"),      default_role="author",      default_field="100")
        out["contributors"] = _to_dict_list(out.get("contributors"), default_role="contributor", default_field="700")
        out["subjects"]     = _to_dict_list(out.get("subjects"),     default_role="subject",     default_field="600")
        out["marc_authority_matches"] = [
            _approved_match_to_desktop_shape(m) for m in by_cn.get(cn, [])
        ]
        # Merge NER entities. Desktop _merge_ner_into_records uses
        # setdefault, so we don't clobber a record that arrived with
        # its own entities list (rare on the web — _to_dict_list above
        # already canonicalises authors/contributors/subjects, but the
        # ``entities`` channel is distinct).
        existing = out.get("entities") or []
        out["entities"] = list(existing) + ents_by_cn.get(cn, [])
        enriched.append(out)

    return await run_in_threadpool(_build_sync, enriched, return_native, overrides or {})


def _build_sync(
    records: list[dict[str, Any]],
    return_native: bool = False,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from converter.wikidata.item_builder import WikidataItemBuilder  # noqa: PLC0415
    from converter.wikidata.quickstatements import QuickStatementsExporter  # noqa: PLC0415

    builder = WikidataItemBuilder(reconciler=None)  # SPARQL-free for the web
    items = builder.build_all(records)

    # Apply per-item curator overrides in place.
    if overrides:
        for it in items:
            ov = overrides.get(_local_id_for(it))
            if ov:
                _apply_override(it, ov)

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
    # Strip surrounding/trailing quotes that MARC parsers sometimes leave in
    # role strings (e.g. 'former owner"', '"(מעתיק)"') so ROLE_TO_PID can
    # match them correctly.
    raw_role = str(m.get("role") or "").strip().strip('"').strip()
    return {
        "name":               m.get("entity_text", ""),
        "role":               raw_role,
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
    """Best-effort dataclass-to-dict + English-label enrichment.

    Every emitted statement / qualifier / reference snak gets two extra
    fields the UI consumes directly:

        ``property_label`` — e.g. "instance of"  for P31
        ``value_label``    — e.g. "Ktiv (NLI manuscript catalog)" for Q118384267

    Both fall back to the bare PID / QID when the desktop's static
    label dictionary doesn't cover the value. The frontend then lazily
    fetches unknown labels via /api/wikidata/labels and patches in.
    """
    try:
        data = _coerce(asdict(item))
    except Exception:
        data = {
            "labels":       getattr(item, "labels", {}),
            "descriptions": getattr(item, "descriptions", {}),
            "aliases":      getattr(item, "aliases", {}),
            "statements":   [_coerce(s.__dict__) for s in getattr(item, "statements", [])],
            "existing_qid": getattr(item, "existing_qid", None),
            "entity_type":  getattr(item, "entity_type", None),
        }

    # Enrich every statement (+ its qualifiers + references) with labels.
    for stmt in data.get("statements") or []:
        _enrich_snak(stmt)
        for q in stmt.get("qualifiers") or []:
            _enrich_snak(q)
        for r in stmt.get("references") or []:
            # Reference snaks come either as a flat dict or wrapped in
            # {"snaks": [...]} — desktop emits both shapes.
            if isinstance(r, dict):
                _enrich_snak(r)
                for s in r.get("snaks") or []:
                    _enrich_snak(s)
    return data


def _enrich_snak(snak: Any) -> None:
    """In-place: stamp ``property_label`` + ``value_label`` on a
    statement / qualifier / reference dict.

    Only stamp when the desktop's static dictionary actually carries
    the label — never the bare PID/QID. (desktop's ``property_label`` /
    ``qid_label`` helpers fall back to the id string, which would
    short-circuit the frontend's lazy lookup against live Wikidata.
    Q652 was the canonical regression: returned "Q652" instead of
    "Italian", so the lazy store never got a chance to resolve.)
    """
    if not isinstance(snak, dict):
        return
    from converter.wikidata.property_labels import PROPERTY_LABELS, QID_LABELS  # noqa: PLC0415

    prop = snak.get("property") or snak.get("property_id")
    if isinstance(prop, str) and prop:
        plabel = PROPERTY_LABELS.get(prop)
        if plabel:
            snak["property_label"] = plabel

    # value_label: only set if the value is a Q-id AND the static dict
    # knows it. Otherwise the frontend's useLabelStore lazy-fetches.
    val = snak.get("value")
    vid = snak.get("value_id")
    qid = vid if isinstance(vid, str) and vid.startswith("Q") else \
          (val if isinstance(val, str) and val.startswith("Q") and val[1:].isdigit() else None)
    if qid:
        vlabel = QID_LABELS.get(qid)
        if vlabel:
            snak["value_label"] = vlabel


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


def local_id_for_item(item: Any) -> str:
    """Stable handle for one built item, used to key overrides.

    Matches what the frontend computes per row (entity_type:index works
    in the visible list, but for *persisted* edits we need something
    stable across rebuilds — pick a real identifier instead).
    """
    return _local_id_for(item)


def _local_id_for(item: Any) -> str:
    for attr in ("local_id", "id"):
        v = getattr(item, attr, None)
        if v:
            return str(v)
    # Fallback: stable composite of entity_type + first label + first
    # external ID. Good enough for runs whose contents don't change.
    et = getattr(item, "entity_type", "") or "item"
    labels = getattr(item, "labels", {}) or {}
    label = labels.get("en") or labels.get("he") or next(iter(labels.values()), "")
    # Pull the first external-id-shaped statement value (Mazal, VIAF, Wikidata)
    ext = ""
    for s in getattr(item, "statements", []) or []:
        prop = getattr(s, "property", None) or getattr(s, "property_id", None)
        if prop in ("P8189", "P214", "P244", "P227", "P213"):
            ext = str(getattr(s, "value", "") or getattr(s, "value_id", "") or "")
            break
    return f"{et}::{ext or label or 'unknown'}"


def _apply_override(item: Any, ov: dict[str, Any]) -> None:
    """Shallow-merge a curator override onto a native WikidataItem."""
    labels       = ov.get("labels")       or {}
    descriptions = ov.get("descriptions") or {}
    aliases      = ov.get("aliases")      or {}
    remove       = set(int(i) for i in (ov.get("remove_statements") or []))
    edits        = ov.get("statement_edits") or {}        # {"3": {value: "Q5"}}
    add          = ov.get("add_statements") or []

    if labels:
        cur = getattr(item, "labels", {}) or {}
        cur.update({k: v for k, v in labels.items() if v is not None})
        # remove keys explicitly set to None
        for k, v in labels.items():
            if v is None and k in cur:
                cur.pop(k, None)
        item.labels = cur
    if descriptions:
        cur = getattr(item, "descriptions", {}) or {}
        cur.update({k: v for k, v in descriptions.items() if v is not None})
        for k, v in descriptions.items():
            if v is None and k in cur:
                cur.pop(k, None)
        item.descriptions = cur
    if aliases:
        cur = getattr(item, "aliases", {}) or {}
        for lang, vals in aliases.items():
            if vals is None:
                cur.pop(lang, None)
            else:
                cur[lang] = list(vals)
        item.aliases = cur

    stmts = list(getattr(item, "statements", []) or [])

    # Statement edits — shallow-set attributes on the dataclass.
    for k, patch in edits.items():
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(stmts):
            stmt = stmts[idx]
            for field, value in (patch or {}).items():
                if hasattr(stmt, field):
                    setattr(stmt, field, value)

    # Statement removals (apply after edits so indices stay stable).
    if remove:
        stmts = [s for i, s in enumerate(stmts) if i not in remove]

    # Appended statements — only safe if we have the desktop's
    # WikidataStatement available; otherwise stash a plain dict that
    # the QS exporter can stringify.
    for raw in add:
        if isinstance(raw, dict):
            stmts.append(_dict_to_stmt(raw))

    item.statements = stmts


def _dict_to_stmt(d: dict[str, Any]) -> Any:
    """Best-effort: build a WikidataStatement from a flat dict the UI
    sent (``{"property":"P31","value":"Q5"}``)."""
    try:
        from converter.wikidata.item_builder import WikidataStatement  # noqa: PLC0415

        return WikidataStatement(
            property=str(d.get("property") or d.get("property_id") or ""),
            value=d.get("value"),
            value_id=str(d.get("value_id") or "") or None,
            value_type=str(d.get("value_type") or "") or None,
            rank=str(d.get("rank") or "normal"),
        )
    except Exception:
        # Bare dict — the exporter coerces via _coerce on serialisation.
        return d
