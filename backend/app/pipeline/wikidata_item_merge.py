"""Merge built Wikidata Studio items with curator overrides."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from converter.wikidata.catalog_references import STALE_CATALOGUE_EDIT, STALE_CATALOGUE_EDIT_MESSAGE

# The source audit identified these exact unbound work edits (W-337).
_STALE_CATALOGUE_EDITS = {
    ("QDraft_MS_990000645310205171", "17"): "Q141754916",
    ("QDraft_MS_990000656400205171", "16"): "Q141755010",
    ("QDraft_MS_990000665230205171", "18"): "Q141755388",
    ("QDraft_MS_990000896070205171", "17"): "Q141754945",
    ("QDraft_MS_990001028930205171", "17"): "Q141755028",
    ("QDraft_MS_990001218770205171", "17"): "Q141754943",
}


def is_stale_catalogue_edit(local_id: str, position: str, patch: object) -> bool:
    qid = _STALE_CATALOGUE_EDITS.get((local_id, position))
    return qid is not None and patch == {"value": qid}


def apply_wikidata_item_override(entity: dict[str, Any], ov: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of *entity* with override fields merged."""
    out = deepcopy(entity)
    labels = ov.get("labels") or {}
    descriptions = ov.get("descriptions") or {}
    aliases = ov.get("aliases") or {}
    remove = {int(i) for i in (ov.get("remove_statements") or [])}
    edits = ov.get("statement_edits") or {}
    add = ov.get("add_statements") or []

    if labels:
        cur = dict(out.get("labels") or {})
        for k, v in labels.items():
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = v
        out["labels"] = cur
    if descriptions:
        cur = dict(out.get("descriptions") or {})
        for k, v in descriptions.items():
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = v
        out["descriptions"] = cur
    if aliases:
        cur = dict(out.get("aliases") or {})
        for lang, vals in aliases.items():
            if vals is None:
                cur.pop(lang, None)
            else:
                cur[lang] = list(vals)
        out["aliases"] = cur

    stmts = list(out.get("statements") or [])
    for k, patch in edits.items():
        if is_stale_catalogue_edit(entity.get("local_id") or "", k, patch):
            issues = out.get("validation_issues") or []
            if not any(issue.get("code") == STALE_CATALOGUE_EDIT for issue in issues):
                out["validation_issues"] = [*issues, {
                    "code": STALE_CATALOGUE_EDIT,
                    "severity": "warning",
                    "message": STALE_CATALOGUE_EDIT_MESSAGE,
                }]
            continue
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(stmts) and isinstance(patch, dict):
            merged = dict(stmts[idx])
            merged.update(patch)
            stmts[idx] = merged
    if remove:
        stmts = [s for i, s in enumerate(stmts) if i not in remove]
    if add:
        stmts.extend(add)
    out["statements"] = stmts
    # The drawer accept binds an existing QID for good: every path that judges
    # or exports this item must see that binding, or the judge keeps failing
    # it as a live CREATE duplicate after the curator already chose (run
    # 47e03437: 4 adopted persons stayed fail because only the view layer
    # knew the QID).
    accepted = str(ov.get("accepted_foreign_qid") or "").strip()
    if accepted and not str(out.get("existing_qid") or "").strip():
        out["existing_qid"] = accepted
    # Carry prior AI verdict for sticky-full reuse (Rule W-171). Editorial
    # fields above may change claims — the partitioner re-checks the fingerprint.
    if isinstance(ov.get("ai_verdict"), dict) and ov.get("ai_verdict"):
        out["ai_verdict"] = dict(ov["ai_verdict"])
        if ov.get("ai_verdict_at"):
            out["ai_verdict_at"] = ov["ai_verdict_at"]
    return out


def override_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "labels": dict(row.labels or {}),
        "descriptions": dict(row.descriptions or {}),
        "aliases": dict(row.aliases or {}),
        "add_statements": list(row.add_statements or []),
        "remove_statements": list(row.remove_statements or []),
        "statement_edits": dict(row.statement_edits or {}),
        "person_identity_choice": deepcopy(getattr(row, "person_identity_choice", None)),
        "source_review_choices": deepcopy(getattr(row, "source_review_choices", None)),
        "approved": row.approved,
        "accept_foreign_modify": bool(row.accept_foreign_modify)
        if row.accept_foreign_modify is not None else False,
        "accepted_foreign_qid": row.accepted_foreign_qid,
        "ai_verdict": row.ai_verdict,
        "ai_verdict_at": row.ai_verdict_at.isoformat() if row.ai_verdict_at else None,
    }
