"""Multi-source evidence packs for Wikidata Studio AI verify (Rule W-124).

The judge must see every durable channel we already hold — MARC, VIAF,
Mazal/NLI, existing Wikidata, and the project HMO Wikibase — plus the
WikiProject Manuscripts skill (injected separately). This module shapes
those channels into a single ``verify_evidence`` dict on each Studio item
before the eval-agent fixture is written.
"""

from __future__ import annotations

from typing import Any

from app.pipeline.marc_verify_context import canonical_control_number
from app.pipeline.wikidata_verdict_cache import marc_context_for_wikidata_item

_WIKIBASE_HOST_HINTS = (
    "mhm-hmo.wikibase.cloud",
    "wikibase.cloud",
)
_WIKIDATA_HOST_HINTS = (
    "www.wikidata.org",
    "wikidata.org",
)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _authority_rows(item: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _as_list(item.get("authority_evidence")):
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _kind_of(row: dict[str, Any]) -> str:
    return str(row.get("kind") or row.get("source") or "").strip().lower()


def _partition_authority(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    viaf: list[dict[str, Any]] = []
    mazal: list[dict[str, Any]] = []
    wikidata: list[dict[str, Any]] = []
    kima: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for row in rows:
        kind = _kind_of(row)
        if "viaf" in kind:
            viaf.append(row)
        elif "mazal" in kind or kind in {"nli", "j9u"}:
            mazal.append(row)
        elif "wikidata" in kind or kind in {"wd", "qid"}:
            wikidata.append(row)
        elif "kima" in kind:
            kima.append(row)
        else:
            other.append(row)
    return {
        "viaf": viaf,
        "mazal": mazal,
        "wikidata": wikidata,
        "kima": kima,
        "other": other,
    }


def _statement_identifier_rows(item: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    viaf: list[dict[str, str]] = []
    mazal: list[dict[str, str]] = []
    for stmt in _as_list(item.get("statements")):
        if not isinstance(stmt, dict):
            continue
        prop = str(stmt.get("property") or stmt.get("property_id") or "").upper()
        value = str(stmt.get("value") or stmt.get("value_id") or "").strip()
        if not value:
            continue
        if prop == "P214":
            viaf.append({"property": "P214", "value": value})
        elif prop == "P8189":
            mazal.append({"property": "P8189", "value": value})
    return {"viaf_from_statements": viaf, "mazal_from_statements": mazal}


def _bridge_statements(item: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for stmt in _as_list(item.get("statements")):
        if not isinstance(stmt, dict):
            continue
        prop = str(stmt.get("property") or stmt.get("property_id") or "").upper()
        if prop not in {"P2888", "P973"}:
            continue
        value = str(stmt.get("value") or "").strip()
        if not value:
            continue
        host = "hmo_wikibase" if any(h in value for h in _WIKIBASE_HOST_HINTS) else (
            "wikidata" if any(h in value for h in _WIKIDATA_HOST_HINTS) else "other"
        )
        out.append({"property": prop, "value": value, "host": host})
    return out


def _hmo_wikibase_page_url(qid: str) -> str:
    q = str(qid or "").strip()
    if not q:
        return ""
    if not q.upper().startswith("Q"):
        q = f"Q{q}"
    return f"https://mhm-hmo.wikibase.cloud/wiki/Item:{q}"


def build_verify_evidence_pack(
    item: dict[str, Any],
    marc_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Shape every available evidence channel for one Studio item."""
    marc = marc_context_for_wikidata_item(item, marc_records)
    authority = _partition_authority(_authority_rows(item))
    from_stmts = _statement_identifier_rows(item)
    hmo_qid = str(item.get("hmo_wikibase_id") or "").strip()
    existing_qid = str(item.get("existing_qid") or "").strip()
    source_uri = str(item.get("source_uri") or "").strip()
    live = item.get("wikidata_live")
    record_ids = [
        canonical_control_number(cn)
        for cn in (
            item.get("record_ids")
            or item.get("records")
            or item.get("control_numbers")
            or []
        )
        if canonical_control_number(cn)
    ]

    return {
        "record_ids": record_ids,
        "marc": marc,
        "marc_present": bool(marc),
        "viaf": {
            "authority_rows": authority["viaf"],
            "from_statements": from_stmts["viaf_from_statements"],
        },
        "mazal": {
            "authority_rows": authority["mazal"],
            "from_statements": from_stmts["mazal_from_statements"],
        },
        "kima": {"authority_rows": authority["kima"]},
        "wikidata_existing": {
            "existing_qid": existing_qid or None,
            "wikidata_uri": (
                f"https://www.wikidata.org/wiki/{existing_qid}"
                if existing_qid else None
            ),
            "authority_rows": authority["wikidata"],
            "live": live if isinstance(live, dict) else None,
        },
        "hmo_wikibase": {
            "hmo_wikibase_id": hmo_qid or None,
            "source_uri": source_uri or None,
            "page_url": _hmo_wikibase_page_url(hmo_qid) or None,
            "projection_source": item.get("projection_source"),
            "bridge_statements": _bridge_statements(item),
        },
        "authority_other": authority["other"],
        "work_candidate_evidence": item.get("work_candidate_evidence") or {},
        "local_reference_targets": item.get("local_reference_targets") or {},
        "wpm_data_model_url": (
            "https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model"
        ),
    }


def enrich_items_with_verify_evidence(
    items: list[dict[str, Any]],
    marc_records: list[dict[str, Any]],
) -> None:
    """Attach ``verify_evidence`` + ``_marc_context`` on every item in place."""
    for item in items:
        pack = build_verify_evidence_pack(item, marc_records)
        item["verify_evidence"] = pack
        item["_marc_context"] = pack.get("marc") or {}
