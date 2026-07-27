"""Merge rich legacy MARC Wikidata items into canonical HMO Studio items.

Rule W-125: canonical Studio default must carry the full research-grade
claim surface (production, contents, agents, housing, codicology,
provenance) while keeping HMO local_ids, bridges, and existing QIDs.
"""

from __future__ import annotations

import re
from typing import Any

from app.pipeline.marc_verify_context import canonical_control_number
from converter.wikidata.item_models import WikidataItem, WikidataStatement

# Prefer the canonical value when both sides emit the same PID.
_CANONICAL_PREFERRED_PIDS = frozenset({
    "P31",
    "P2888",
    "P973",
})

_QID_RE = re.compile(r"^Q\d+$", re.IGNORECASE)


def merge_legacy_into_canonical(
    canonical_items: list[WikidataItem],
    legacy_items: list[WikidataItem],
) -> list[WikidataItem]:
    """Enrich canonical items with legacy MARC/authority claims.

    - Keeps every canonical ``local_id`` / HMO bridge / ``existing_qid``.
    - Unions statements from the best-matching legacy item.
    - Appends unmatched legacy persons/works (already fail-closed on IDs).
    - Does not append unmatched legacy manuscripts (canonical is the MS root).
    """
    if not legacy_items:
        return list(canonical_items)
    if not canonical_items:
        return list(legacy_items)

    legacy_ms = _index_manuscripts(legacy_items)
    legacy_persons = _index_persons(legacy_items)
    legacy_works = _index_works(legacy_items)
    used_legacy_ids: set[str] = set()

    merged: list[WikidataItem] = []
    for item in canonical_items:
        et = (item.entity_type or "").strip().lower()
        legacy: WikidataItem | None = None
        if et == "manuscript":
            legacy = _match_manuscript(item, legacy_ms)
        elif et == "person":
            legacy = _match_person(item, legacy_persons)
        elif et == "work":
            legacy = _match_work(item, legacy_works)
        if legacy is not None:
            used_legacy_ids.add(legacy.local_id or id(legacy).__repr__())
            merged.append(_merge_pair(item, legacy))
        else:
            merged.append(item)

    for legacy in legacy_items:
        lid = legacy.local_id or ""
        key = lid or id(legacy).__repr__()
        if key in used_legacy_ids:
            continue
        et = (legacy.entity_type or "").strip().lower()
        if et == "manuscript":
            # Canonical already owns the manuscript public item for each CN.
            continue
        if et in {"person", "work"}:
            merged.append(legacy)
    return merged


def _merge_pair(canonical: WikidataItem, legacy: WikidataItem) -> WikidataItem:
    out = WikidataItem(
        labels=dict(canonical.labels or {}),
        descriptions=dict(canonical.descriptions or {}),
        aliases={
            lang: list(values)
            for lang, values in (canonical.aliases or {}).items()
        },
        statements=list(canonical.statements or []),
        existing_qid=canonical.existing_qid or legacy.existing_qid,
        entity_type=canonical.entity_type or legacy.entity_type,
        semantic_type=canonical.semantic_type or legacy.semantic_type,
        local_id=canonical.local_id,
        records=_union_records(canonical.records, legacy.records),
        authority_evidence=_union_evidence(
            canonical.authority_evidence, legacy.authority_evidence,
        ),
        work_candidate_evidence=_union_work_evidence(
            canonical.work_candidate_evidence, legacy.work_candidate_evidence,
        ),
    )
    # Prefer non-empty legacy labels/descriptions when canonical is thin.
    for lang, label in (legacy.labels or {}).items():
        text = str(label or "").strip()
        if text and not str(out.labels.get(lang) or "").strip():
            out.labels[lang] = text
    for lang, desc in (legacy.descriptions or {}).items():
        text = str(desc or "").strip()
        if text and not str(out.descriptions.get(lang) or "").strip():
            out.descriptions[lang] = text
    for lang, aliases in (legacy.aliases or {}).items():
        bucket = out.aliases.setdefault(lang, [])
        for alias in aliases or []:
            text = str(alias or "").strip()
            if text and text not in bucket:
                bucket.append(text)

    seen = {_statement_key(stmt) for stmt in out.statements}
    for stmt in legacy.statements or []:
        pid = str(stmt.property_id or "")
        key = _statement_key(stmt)
        if key in seen:
            continue
        if pid in _CANONICAL_PREFERRED_PIDS and _has_pid(out.statements, pid):
            continue
        out.statements.append(stmt)
        seen.add(key)
    return out


def _statement_key(stmt: WikidataStatement) -> tuple[str, str, str]:
    return (
        str(stmt.property_id or ""),
        str(stmt.value_type or ""),
        str(stmt.value if stmt.value is not None else ""),
    )


def _has_pid(statements: list[WikidataStatement], pid: str) -> bool:
    return any(str(s.property_id or "") == pid for s in statements)


def _union_records(a: list[str] | None, b: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in list(a or []) + list(b or []):
        cn = canonical_control_number(raw)
        if cn and cn not in seen:
            seen.add(cn)
            out.append(cn)
    return out


def _union_evidence(
    a: list[dict[str, object]] | None,
    b: list[dict[str, object]] | None,
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in list(a or []) + list(b or []):
        if not isinstance(row, dict):
            continue
        key = "|".join(
            str(row.get(k) or "")
            for k in ("kind", "source", "viaf_uri", "mazal_id", "wikidata_qid", "identifier")
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(row))
    return out


def _union_work_evidence(a: Any, b: Any) -> list[dict[str, object]]:
    rows_a = a if isinstance(a, list) else ([a] if isinstance(a, dict) and a else [])
    rows_b = b if isinstance(b, list) else ([b] if isinstance(b, dict) and b else [])
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in list(rows_a) + list(rows_b):
        if not isinstance(row, dict):
            continue
        key = str(row.get("title") or row.get("source_text") or row)
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(row))
    return out


def _control_numbers_of(item: WikidataItem) -> set[str]:
    out: set[str] = set()
    for raw in item.records or []:
        cn = canonical_control_number(raw)
        if cn:
            out.add(cn)
    for stmt in item.statements or []:
        if str(stmt.property_id or "") == "P3959":
            cn = canonical_control_number(stmt.value)
            if cn:
                out.add(cn)
    return out


def _index_manuscripts(items: list[WikidataItem]) -> dict[str, WikidataItem]:
    index: dict[str, WikidataItem] = {}
    for item in items:
        if (item.entity_type or "").lower() != "manuscript":
            continue
        for cn in _control_numbers_of(item):
            index.setdefault(cn, item)
    return index


def _match_manuscript(
    item: WikidataItem,
    index: dict[str, WikidataItem],
) -> WikidataItem | None:
    for cn in _control_numbers_of(item):
        hit = index.get(cn)
        if hit is not None:
            return hit
    return None


def _person_keys(item: WikidataItem) -> set[str]:
    keys: set[str] = set()
    for stmt in item.statements or []:
        pid = str(stmt.property_id or "")
        value = str(stmt.value or "").strip()
        if pid == "P214" and value:
            keys.add(f"viaf:{value}")
        if pid == "P8189" and value:
            keys.add(f"mazal:{value}")
        if pid in {"P214", "P8189"}:
            continue
        if _QID_RE.fullmatch(value) and item.existing_qid and value == item.existing_qid:
            keys.add(f"qid:{value}")
    if item.existing_qid:
        keys.add(f"qid:{item.existing_qid}")
    for lang in ("he", "en"):
        label = str((item.labels or {}).get(lang) or "").strip().casefold()
        if label:
            keys.add(f"label:{label}")
    for row in item.authority_evidence or []:
        if not isinstance(row, dict):
            continue
        viaf = str(row.get("viaf_uri") or row.get("viaf_id") or "").strip()
        if viaf:
            viaf = viaf.rstrip("/").rsplit("/", 1)[-1]
            keys.add(f"viaf:{viaf}")
        mazal = str(row.get("mazal_id") or "").strip()
        if mazal:
            keys.add(f"mazal:{mazal}")
        qid = str(row.get("wikidata_qid") or "").strip()
        if qid:
            keys.add(f"qid:{qid}")
    return keys


def _index_persons(items: list[WikidataItem]) -> dict[str, WikidataItem]:
    index: dict[str, WikidataItem] = {}
    for item in items:
        if (item.entity_type or "").lower() != "person":
            continue
        for key in _person_keys(item):
            index.setdefault(key, item)
    return index


def _match_person(
    item: WikidataItem,
    index: dict[str, WikidataItem],
) -> WikidataItem | None:
    for key in _person_keys(item):
        hit = index.get(key)
        if hit is not None:
            return hit
    return None


def _work_keys(item: WikidataItem) -> set[str]:
    keys: set[str] = set()
    for lang in ("he", "en"):
        label = str((item.labels or {}).get(lang) or "").strip().casefold()
        if label:
            keys.add(f"label:{label}")
    for stmt in item.statements or []:
        if str(stmt.property_id or "") == "P1476":
            title = str(stmt.value or "").strip().casefold()
            if title:
                keys.add(f"title:{title}")
    if item.existing_qid:
        keys.add(f"qid:{item.existing_qid}")
    if item.local_id:
        keys.add(f"local:{item.local_id}")
    return keys


def _index_works(items: list[WikidataItem]) -> dict[str, WikidataItem]:
    index: dict[str, WikidataItem] = {}
    for item in items:
        if (item.entity_type or "").lower() != "work":
            continue
        for key in _work_keys(item):
            index.setdefault(key, item)
    return index


def _match_work(
    item: WikidataItem,
    index: dict[str, WikidataItem],
) -> WikidataItem | None:
    for key in _work_keys(item):
        hit = index.get(key)
        if hit is not None:
            return hit
    return None
