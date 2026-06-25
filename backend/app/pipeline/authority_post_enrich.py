"""Post-enrich passes: personality cross-links and Wikidata crosscheck."""
from __future__ import annotations

from typing import Any

from app.pipeline.entity_normalize import normalize_entity_key, normalize_entity_text, normalize_role


_AUTHOR_ROLES = frozenset({
    "author", "contributor", "scribe", "translator", "editor", "commentator",
})


def apply_personality_cross_links(rows: list[Any]) -> int:
    """Link subject rows to author personality mazal_id on the same manuscript."""
    by_cn: dict[str, list[Any]] = {}
    for row in rows:
        by_cn.setdefault(str(row.control_number or ""), []).append(row)

    updated = 0
    for cn_rows in by_cn.values():
        personality_by_name: dict[str, str] = {}
        for row in cn_rows:
            if str(row.entity_kind or "") != "person":
                continue
            role = normalize_role(str(row.role or ""))
            if role not in _AUTHOR_ROLES:
                continue
            payload = dict(row.payload or {})
            if payload.get("main_marc_tag") and payload.get("main_marc_tag") != "100":
                continue
            if not row.mazal_id:
                continue
            key = normalize_entity_key(normalize_entity_text(str(row.entity_text or "")))
            if key:
                personality_by_name[key] = str(row.mazal_id)

        for row in cn_rows:
            if str(row.entity_kind or "") != "person":
                continue
            if normalize_role(str(row.role or "")) != "subject":
                continue
            key = normalize_entity_key(normalize_entity_text(str(row.entity_text or "")))
            linked = personality_by_name.get(key)
            if not linked:
                continue
            payload = dict(row.payload or {})
            if payload.get("linked_personality_mazal_id") == linked:
                continue
            payload["linked_personality_mazal_id"] = linked
            row.payload = payload
            updated += 1
    return updated


def apply_wikidata_crosscheck_pass(rows: list[Any]) -> int:
    """Re-run Wikidata crosscheck with sibling context; clear bad QIDs."""
    from app.pipeline import authority_hardening  # noqa: PLC0415

    by_cn: dict[str, list[Any]] = {}
    for row in rows:
        by_cn.setdefault(str(row.control_number or ""), []).append(row)

    downgraded = 0
    for cn_rows in by_cn.values():
        snapshots: list[dict[str, Any]] = []
        for m in cn_rows:
            snapshots.append({
                "matched_name": m.matched_name,
                "entity_text": m.entity_text,
                "entity_kind": m.entity_kind,
                "role": m.role,
                "confidence": m.confidence,
                "mazal_id": m.mazal_id,
                "viaf_id": m.viaf_id,
                "wikidata_qid": m.wikidata_qid,
                "payload": dict(m.payload or {}),
                "_row": m,
            })
        for snap in snapshots:
            m = snap.pop("_row")
            siblings = [
                {k: v for k, v in other.items() if k != "_row"}
                for other in snapshots if other.get("entity_text") != snap.get("entity_text")
            ]
            payload_pre = dict(snap.get("payload") or {})
            ctx = authority_hardening.HardeningContext(
                siblings=siblings,
                preferred_name_lat=payload_pre.get("preferred_name_lat"),
                biographical_dates_in_marc=bool(
                    payload_pre.get("birth_year") or payload_pre.get("death_year")
                ),
                entity_kind=str(snap.get("entity_kind") or "person"),
                role=str(snap.get("role") or ""),
                ms_year=payload_pre.get("ms_year"),
                birth_year=payload_pre.get("birth_year"),
                death_year=payload_pre.get("death_year"),
                enable_wikidata_crosscheck=True,
            )
            hardened = authority_hardening.apply_hardening_guards(snap, context=ctx)
            new_flags = set(hardened["payload"].get("guard_flags") or [])
            if "wikidata_crosscheck_fail" in new_flags:
                m.wikidata_qid = hardened.get("wikidata_qid") or ""
                m.viaf_id = hardened.get("viaf_id") or ""
                m.confidence = str(hardened.get("confidence") or m.confidence)
                m.payload = hardened["payload"]
                downgraded += 1
    return downgraded


def finalize_authority_matches(rows: list[Any]) -> dict[str, int]:
    """Post-enrich passes shared by initial run and re-enrich."""
    return {
        "cross_linked": apply_personality_cross_links(rows),
        "wikidata_crosschecked": apply_wikidata_crosscheck_pass(rows),
    }
