"""Merge AI-suggested Wikidata fixes into curator override payloads."""

from __future__ import annotations

from typing import Any


def merge_ai_fixes(
    fixes: list[dict[str, Any]],
    *,
    labels: dict[str, str | None] | None = None,
    descriptions: dict[str, str | None] | None = None,
    add_statements: list[dict[str, Any]] | None = None,
    remove_statements: list[int] | None = None,
) -> dict[str, Any]:
    """Return override fragments to PATCH after merging with existing state."""
    out_labels = dict(labels or {})
    out_descs = dict(descriptions or {})
    out_add = list(add_statements or [])
    out_rm = set(remove_statements or [])

    for fix in fixes:
        if not isinstance(fix, dict):
            continue
        if str(fix.get("confidence") or "") != "high":
            continue
        target = str(fix.get("target") or "")
        if target == "label.en" and fix.get("value"):
            out_labels["en"] = str(fix["value"])
        elif target == "label.he" and fix.get("value"):
            out_labels["he"] = str(fix["value"])
        elif target == "description.en" and fix.get("value"):
            out_descs["en"] = str(fix["value"])
        elif target == "description.he" and fix.get("value"):
            out_descs["he"] = str(fix["value"])
        elif target == "statement.remove":
            idx = fix.get("studio_statement_index")
            if isinstance(idx, int):
                out_rm.add(idx)
            elif str(idx or "").isdigit():
                out_rm.add(int(idx))
        elif target == "statement.add":
            pid = str(fix.get("property_id") or fix.get("property") or "")
            value = fix.get("value")
            if not pid or value is None:
                continue
            value_s = str(value)
            vtype = str(fix.get("value_type") or "")
            if not vtype:
                vtype = "wikibase-item" if value_s.startswith("Q") else "string"
            stmt: dict[str, Any] = {
                "property_id": pid,
                "property": pid,
                "value": value_s,
                "value_type": vtype,
            }
            if value_s.startswith("Q"):
                stmt["value_id"] = value_s
            out_add.append(stmt)

    payload: dict[str, Any] = {}
    if out_labels:
        payload["labels"] = out_labels
    if out_descs:
        payload["descriptions"] = out_descs
    if out_add:
        payload["add_statements"] = out_add
    if out_rm:
        payload["remove_statements"] = sorted(out_rm)
    return payload
