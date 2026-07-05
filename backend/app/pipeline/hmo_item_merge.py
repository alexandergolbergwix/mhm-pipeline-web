"""Merge resolved HMO items with curator overrides for the review UI."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def apply_hmo_item_override(entity: dict[str, Any], ov: dict[str, Any]) -> dict[str, Any]:
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

    claims = list(out.get("claims") or [])
    for k, patch in edits.items():
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(claims) and isinstance(patch, dict):
            merged = dict(claims[idx])
            merged.update(patch)
            claims[idx] = merged
    if remove:
        claims = [c for i, c in enumerate(claims) if i not in remove]
    if add:
        claims.extend(add)
    out["claims"] = claims
    return out


def override_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "labels": dict(row.labels or {}),
        "descriptions": dict(row.descriptions or {}),
        "aliases": dict(row.aliases or {}),
        "add_statements": list(row.add_statements or []),
        "remove_statements": list(row.remove_statements or []),
        "statement_edits": dict(row.statement_edits or {}),
        "approved": row.approved,
        "ai_verdict": row.ai_verdict,
        "ai_verdict_at": row.ai_verdict_at.isoformat() if row.ai_verdict_at else None,
    }
