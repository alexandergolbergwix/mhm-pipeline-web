"""Clear false live identities and leftover ``__LOCAL:`` on Studio natives.

Judge/audit read the Studio cache, not upload-prepare. Authority-stamped
``existing_qid`` values that fail W-190 (or a work ritual/concept QID) must
be dropped on the native itself, and ``__LOCAL:`` snaks that already have an
in-corpus live Q must be rewritten before a live write (Rule W-194).
"""

from __future__ import annotations

from typing import Any

from app.pipeline.wikidata_duplicate_probe import person_heading_conflicts_live_label
from app.pipeline.wikidata_local_refs import LOCAL_PREFIX, P_EXEMPLAR_OF, Q_UNKNOWN_TEXT
from converter.wikidata.property_mapping import work_item_forbidden_update_qids

IDENTITY_PIDS = frozenset({"P214", "P8189", "P244", "P227", "P213", "P268"})

WRITTEN_WORK_P31 = frozenset({
    "Q47461344",  # written work
    "Q7725634",   # literary work
    "Q5185279",   # poem
    "Q571",       # book
    "Q8261",      # novel
})
RITUAL_OR_CONCEPT_P31 = frozenset({
    "Q4502142",  # religious rite
    "Q1344",     # prayer
    "Q2916094",  # liturgy
    "Q500647",   # ritual
    "Q3077454",  # Jewish prayer
    "Q16502",    # religious practice
})


def existing_qid_of(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("existing_qid") or item.get("existing_qid") or "").strip()
    return str(getattr(item, "existing_qid", None) or "").strip()


def set_existing_qid(item: Any, qid: str | None) -> None:
    value = str(qid or "").strip() or None
    if isinstance(item, dict):
        item["existing_qid"] = value
        if "existing_qid" in item:
            item["existing_qid"] = value
        return
    item.existing_qid = value


def entity_type_of(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("entity_type") or item.get("type") or "").strip().lower()
    return str(getattr(item, "entity_type", "") or "").strip().lower()


def local_id_of(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("local_id") or "").strip()
    return str(getattr(item, "local_id", "") or "").strip()


def sanitize_studio_items_for_live(
    items: list[Any],
    *,
    live_entities: dict[str, dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Mutate natives: drop false live QIDs, rewrite in-corpus ``__LOCAL:``."""
    live_entities = live_entities or {}
    cleared_persons = 0
    cleared_works = 0
    for item in items:
        qid = existing_qid_of(item)
        if not qid:
            continue
        etype = entity_type_of(item)
        live = live_entities.get(qid) or {}
        if etype == "person" and _person_qid_conflicts(item, live):
            set_existing_qid(item, None)
            strip_identity_pids(item)
            cleared_persons += 1
        elif etype == "work" and _work_qid_conflicts(qid, live):
            set_existing_qid(item, None)
            cleared_works += 1

    local_stats = rewrite_local_to_live_qids(items)
    local_stats["cleared_person_qids"] = cleared_persons
    local_stats["cleared_work_qids"] = cleared_works
    return local_stats


def rewrite_local_to_live_qids(items: list[Any]) -> dict[str, int]:
    """Replace ``__LOCAL:`` with the target's remaining live QID; degrade danglers."""
    by_id = {local_id_of(item): item for item in items if local_id_of(item)}
    rewritten = 0
    degraded = 0
    dropped = 0
    for item in items:
        statements = _statements_of(item)
        kept: list[Any] = []
        for stmt in statements:
            value = _stmt_value(stmt)
            target = _local_target(value)
            if not target:
                _rewrite_local_in_qualifiers(stmt, by_id)
                kept.append(stmt)
                continue
            dest = by_id.get(target)
            if dest is not None:
                live_q = existing_qid_of(dest)
                if live_q:
                    _set_stmt_value(stmt, live_q)
                    rewritten += 1
                kept.append(stmt)
                continue
            if _stmt_pid(stmt) == P_EXEMPLAR_OF:
                _set_stmt_value(stmt, Q_UNKNOWN_TEXT)
                degraded += 1
                kept.append(stmt)
            else:
                dropped += 1
        _set_statements(item, kept)
    return {
        "local_rewritten": rewritten,
        "local_degraded": degraded,
        "local_dropped": dropped,
    }


def strip_identity_pids(item: Any) -> int:
    """Drop VIAF/NLI/etc. snaks so CREATE does not restate a colliding live key."""
    statements = _statements_of(item)
    kept = [stmt for stmt in statements if _stmt_pid(stmt) not in IDENTITY_PIDS]
    removed = len(statements) - len(kept)
    _set_statements(item, kept)
    return removed


def item_has_identity_pid(item: Any) -> bool:
    """True when any VIAF/NLI/etc. snak still carries a value."""
    for stmt in _statements_of(item):
        if _stmt_pid(stmt) in IDENTITY_PIDS and str(_stmt_value(stmt) or "").strip():
            return True
    return False


def person_has_publishable_identity(item: Any) -> bool:
    """True when a person still has a live QID or a remaining identity PID."""
    if existing_qid_of(item):
        return True
    return item_has_identity_pid(item)


def live_labels_from_entity(entity: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    labels = (entity or {}).get("labels") or {}
    if not isinstance(labels, dict):
        return out
    for lang, row in labels.items():
        if isinstance(row, dict):
            text = str(row.get("value") or "").strip()
        else:
            text = str(row or "").strip()
        if text:
            out[str(lang)] = text
    return out


def live_p31_from_entity(entity: dict[str, Any] | None) -> list[str]:
    claims = (entity or {}).get("claims") or {}
    rows = claims.get("P31") or []
    qids: list[str] = []
    if not isinstance(rows, list):
        return qids
    for claim in rows:
        snak = (claim or {}).get("mainsnak") or {}
        dv = (snak.get("datavalue") or {}).get("value") or {}
        qid = str(dv.get("id") or "").strip()
        if qid.startswith("Q"):
            qids.append(qid)
    return qids


def _person_qid_conflicts(item: Any, live: dict[str, Any]) -> bool:
    if not live:
        return False
    labels = live_labels_from_entity(live)
    if not labels:
        return True
    return person_heading_conflicts_live_label(
        item,
        live_en=labels.get("en") or "",
        live_he=labels.get("he") or "",
    )


def _work_qid_conflicts(qid: str, live: dict[str, Any]) -> bool:
    if qid in work_item_forbidden_update_qids():
        return True
    if not live:
        return False
    p31 = set(live_p31_from_entity(live))
    if p31 & WRITTEN_WORK_P31:
        return False
    return bool(p31 & RITUAL_OR_CONCEPT_P31)


def _local_target(value: Any) -> str:
    text = str(value or "")
    if not text.startswith(LOCAL_PREFIX):
        return ""
    return text[len(LOCAL_PREFIX):].strip()


def _statements_of(item: Any) -> list[Any]:
    if isinstance(item, dict):
        return list(item.get("statements") or [])
    return list(getattr(item, "statements", None) or [])


def _set_statements(item: Any, statements: list[Any]) -> None:
    if isinstance(item, dict):
        item["statements"] = statements
        return
    item.statements = statements


def _stmt_pid(stmt: Any) -> str:
    if isinstance(stmt, dict):
        return str(stmt.get("property_id") or stmt.get("property") or "").strip()
    return str(getattr(stmt, "property_id", "") or "").strip()


def _stmt_value(stmt: Any) -> Any:
    if isinstance(stmt, dict):
        return stmt.get("value")
    return getattr(stmt, "value", None)


def _set_stmt_value(stmt: Any, value: Any) -> None:
    if isinstance(stmt, dict):
        stmt["value"] = value
        return
    stmt.value = value


def _rewrite_local_in_qualifiers(stmt: Any, by_id: dict[str, Any]) -> None:
    if isinstance(stmt, dict):
        raw = list(stmt.get("qualifiers") or [])
    else:
        raw = list(getattr(stmt, "qualifiers", None) or [])
    qualifiers: list[Any] = []
    for qualifier in raw:
        if not isinstance(qualifier, dict):
            qualifiers.append(qualifier)
            continue
        target = _local_target(qualifier.get("value"))
        if not target:
            qualifiers.append(qualifier)
            continue
        dest = by_id.get(target)
        if dest is None:
            continue
        live_q = existing_qid_of(dest)
        if live_q:
            qualifier = dict(qualifier)
            qualifier["value"] = live_q
        qualifiers.append(qualifier)
    if isinstance(stmt, dict):
        stmt["qualifiers"] = qualifiers
    else:
        stmt.qualifiers = qualifiers
