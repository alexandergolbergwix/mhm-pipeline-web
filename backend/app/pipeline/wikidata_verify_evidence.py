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


# Which MARC slice name backs each projected claim. The judge must be able to
# check a claim against *its own* source field instead of scanning a blob —
# absent this map, evidenced claims (dates, extent, shelfmark, rights) read as
# unsupported (Rule W-137).
CLAIM_SOURCE_SLICES: dict[str, tuple[str, ...]] = {
    "P31": ("title", "genres", "carrier"),
    "P50": ("authors",),
    "P127": ("provenance",),
    "P136": ("genres",),
    "P186": ("material",),
    "P195": ("shelfmark", "provenance", "contributors"),
    "P217": ("shelfmark",),
    "P276": ("place", "shelfmark"),
    "P282": ("languages", "material"),
    "P407": ("languages",),
    "P571": ("dates",),
    "P921": ("subjects",),
    "P953": ("digital_access",),
    "P1071": ("place",),
    "P1104": ("extent",),
    "P1476": ("title",),
    "P1574": ("contents", "title", "related_records"),
    "P1680": ("title",),
    "P1684": ("notes", "colophon_text", "summary"),
    "P2048": ("extent",),
    "P2049": ("extent",),
    "P2093": ("authors", "contributors"),
    "P2635": ("extent",),
    "P3959": ("record_ids",),
    "P6108": ("digital_access",),
    "P6216": ("rights",),
    "P7153": ("place", "provenance"),
    "P9302": ("material", "languages"),
    "P11603": ("extent", "material"),
}

_MAX_CLAIM_EVIDENCE_CHARS = 400


def _statement_property_ids(item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for statement in item.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        pid = str(statement.get("property_id") or statement.get("property") or "").strip()
        if pid and pid not in out:
            out.append(pid)
    return out


def build_claim_sources(
    item: dict[str, Any],
    marc: dict[str, str],
    record_ids: list[str],
) -> dict[str, Any]:
    """Per-claim MARC provenance: PID → the slice text that supports it."""
    sources: dict[str, Any] = {}
    for pid in _statement_property_ids(item):
        slices = CLAIM_SOURCE_SLICES.get(pid)
        if not slices:
            continue
        evidence: dict[str, str] = {}
        for name in slices:
            if name == "record_ids":
                if record_ids:
                    evidence[name] = ", ".join(record_ids)
                continue
            text = str(marc.get(name) or "").strip()
            if text:
                evidence[name] = text[:_MAX_CLAIM_EVIDENCE_CHARS]
        sources[pid] = {
            "marc_slices": list(slices),
            "evidence": evidence,
            "supported": bool(evidence),
        }
    return sources


def build_statement_value_labels(item: dict[str, Any]) -> dict[str, str]:
    """Resolve QID/PID glosses for the judge without any network call.

    A bare ``Q33513`` reads as an unverifiable claim; the static desktop
    dictionary plus each item's own ``__LOCAL:`` targets cover everything we
    project (Rule W-137). Live WDQS lookups stay off the verify path
    (Rule W-116).
    """
    from converter.wikidata.property_labels import property_label, qid_label  # noqa: PLC0415

    out: dict[str, str] = {}
    targets = item.get("local_reference_targets")
    local_labels: dict[str, str] = {}
    if isinstance(targets, dict):
        for target_id, target in targets.items():
            labels = target.get("labels") if isinstance(target, dict) else None
            if isinstance(labels, dict):
                text = str(labels.get("en") or labels.get("he") or "").strip()
                if text:
                    local_labels[str(target_id)] = text

    for statement in item.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        pid = str(statement.get("property_id") or statement.get("property") or "").strip()
        if pid:
            label = property_label(pid)
            if label and label != pid:
                out[pid] = label
        value = str(statement.get("value") or "").strip()
        if not value or value in out:
            continue
        if value.startswith("__LOCAL:"):
            target_label = local_labels.get(value.removeprefix("__LOCAL:"))
            if target_label:
                out[value] = target_label
            continue
        if value.upper().startswith("Q"):
            label = str(statement.get("value_label") or "").strip() or qid_label(value)
            if label and label != value:
                out[value] = label
    return out


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
        "claim_sources": build_claim_sources(item, marc, record_ids),
        "value_labels": build_statement_value_labels(item),
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
