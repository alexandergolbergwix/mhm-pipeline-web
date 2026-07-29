"""Merge rich legacy MARC Wikidata items into canonical HMO Studio items.

Rule W-125: canonical Studio default must carry the full research-grade
claim surface (production, contents, agents, housing, codicology,
provenance) while keeping HMO local_ids, bridges, and existing QIDs.
"""

from __future__ import annotations

import re
from typing import Any

from app.pipeline.marc_verify_context import (
    canonical_control_number,
    primary_control_number_for,
)
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
        return _with_deduped_statements(canonical_items)
    if not canonical_items:
        return _with_deduped_statements(legacy_items)

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
            item.statements = dedupe_statements(item.statements)
            merged.append(_with_scoped_records(item))

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
            legacy.statements = dedupe_statements(legacy.statements)
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
        records=_merged_records(canonical, legacy),
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
    out.statements = dedupe_statements(out.statements)
    return out


def _with_scoped_records(item: WikidataItem) -> WikidataItem:
    """A manuscript item's MARC scope is its own record (Rule W-137)."""
    if (item.entity_type or "").strip().lower() == "manuscript":
        cn = primary_control_number_of(item)
        item.records = [cn] if cn else []
    return item


def _with_deduped_statements(items: list[WikidataItem]) -> list[WikidataItem]:
    for item in items:
        item.statements = dedupe_statements(item.statements)
        _with_scoped_records(item)
    return list(items)


def dedupe_statements(statements: list[WikidataStatement]) -> list[WikidataStatement]:
    """One statement per (property, value), keeping the best-sourced instance.

    ``_statement_key`` includes ``value_type``, so the same fact emitted as
    ``string`` and ``external-id`` survived twice — every manuscript shipped
    P3959 four or five times (Rule W-137). Ranking by (references, qualifiers)
    keeps the referenced instance the judge and curator want to see.
    """
    best: dict[tuple[str, str], WikidataStatement] = {}
    order: list[tuple[str, str]] = []
    for stmt in statements or []:
        key = (
            str(stmt.property_id or ""),
            str(stmt.value if stmt.value is not None else ""),
        )
        current = best.get(key)
        if current is None:
            best[key] = stmt
            order.append(key)
            continue
        rank = (len(stmt.references or []), len(stmt.qualifiers or []))
        current_rank = (len(current.references or []), len(current.qualifiers or []))
        if rank > current_rank:
            best[key] = stmt
    return [best[key] for key in order]


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


def _merged_records(canonical: WikidataItem, legacy: WikidataItem) -> list[str]:
    """Manuscripts keep only their own record; other types union sources.

    A manuscript's MARC slice is merged across its ``records``, so keeping
    linked CNs here feeds the judge (and the label/description builders) another
    manuscript's title, shelfmark and dates (Rule W-137). Persons and works
    legitimately span records — Rule W-63.
    """
    if (canonical.entity_type or "").strip().lower() == "manuscript":
        cn = primary_control_number_of(canonical) or primary_control_number_of(legacy)
        return [cn] if cn else []
    return _union_records(canonical.records, legacy.records)


def primary_control_number_of(item: WikidataItem) -> str:
    """The CN a manuscript item *is*, not the ones it merely links to."""
    return primary_control_number_for(
        sorted(_control_numbers_of(item)),
        item.local_id,
    )


def _index_manuscripts(items: list[WikidataItem]) -> dict[str, WikidataItem]:
    """Index legacy manuscripts by their OWN control number only.

    Indexing by every linked CN made several canonical manuscripts match the
    same legacy item, so each inherited that item's shelfmark, title, dates and
    holder — three distinct manuscripts shipped as `The British Library, F 8298`
    with `P217 = F 7956` (Rule W-137).
    """
    index: dict[str, WikidataItem] = {}
    for item in items:
        if (item.entity_type or "").lower() != "manuscript":
            continue
        cn = primary_control_number_of(item)
        if cn:
            index.setdefault(cn, item)
    return index


def _match_manuscript(
    item: WikidataItem,
    index: dict[str, WikidataItem],
) -> WikidataItem | None:
    cn = primary_control_number_of(item)
    if not cn:
        return None
    return index.get(cn)


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
