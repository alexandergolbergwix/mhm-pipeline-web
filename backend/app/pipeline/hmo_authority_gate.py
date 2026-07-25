"""Fail-closed authority checks required before HMO item creation."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


def _row_text(row: Any) -> str:
    return str(getattr(row, "entity_text", "") or "").strip()


def _row_id(row: Any) -> str:
    value = getattr(row, "id", None)
    return str(value) if value is not None else ""


def _identifier_fields(row: Any) -> tuple[tuple[str, str], ...]:
    return (
        ("wikidata", str(getattr(row, "wikidata_qid", "") or "").strip()),
        ("mazal", str(getattr(row, "mazal_id", "") or "").strip()),
        ("viaf", str(getattr(row, "viaf_id", "") or "").strip()),
    )


def match_owner_summary(row: Any) -> dict[str, Any]:
    """Curator-facing summary of one AuthorityMatch row."""
    return {
        "match_id": _row_id(row),
        "entity_text": _row_text(row),
        "matched_name": str(getattr(row, "matched_name", "") or "").strip(),
        "control_number": str(getattr(row, "control_number", "") or "").strip(),
        "entity_kind": str(getattr(row, "entity_kind", "") or "").strip(),
        "role": str(getattr(row, "role", "") or "").strip(),
        "confidence": str(getattr(row, "confidence", "") or "").strip(),
        "source": str(getattr(row, "source", "") or "").strip(),
        "mazal_id": str(getattr(row, "mazal_id", "") or "").strip(),
        "viaf_id": str(getattr(row, "viaf_id", "") or "").strip(),
        "wikidata_qid": str(getattr(row, "wikidata_qid", "") or "").strip(),
        "approved": bool(getattr(row, "approved", False)),
    }


def validate_authority_rows(rows: Iterable[Any]) -> dict[str, Any]:
    """Upload gate: ready iff no identifier conflicts and no invalid VIAF slots."""
    report = build_authority_conflict_report(rows)
    return {
        "conflicts": [
            {
                "kind": c["kind"],
                "identifier": c["identifier"],
                "owners": [o["entity_text"] for o in c["owners"]],
            }
            for c in report["conflicts"]
        ],
        "invalid": [
            {
                "entity_text": i["entity_text"],
                "kind": i["kind"],
                "identifier": i["identifier"],
                "reason": i["reason"],
            }
            for i in report["invalid"]
        ],
        "ready": report["ready"],
    }


def build_authority_conflict_report(rows: Iterable[Any]) -> dict[str, Any]:
    """Rich conflict report with match ids for the HMO Studio resolver UI."""
    approved = [row for row in rows if bool(getattr(row, "approved", False))]
    by_id: dict[tuple[str, str], list[Any]] = defaultdict(list)
    invalid: list[dict[str, Any]] = []

    for row in approved:
        for kind, identifier in _identifier_fields(row):
            if not identifier:
                continue
            if kind == "viaf" and identifier.startswith("987007"):
                summary = match_owner_summary(row)
                invalid.append({
                    **summary,
                    "kind": kind,
                    "identifier": identifier,
                    "reason": "NLI/Mazal identifier stored as VIAF",
                })
                continue
            by_id[(kind, identifier)].append(row)

    conflicts: list[dict[str, Any]] = []
    for (kind, identifier), owners in sorted(by_id.items(), key=lambda x: (x[0][0], x[0][1])):
        texts = {_row_text(row) for row in owners if _row_text(row)}
        if len(texts) <= 1:
            continue
        # Dedupe by match id while preserving order.
        seen: set[str] = set()
        owner_summaries: list[dict[str, Any]] = []
        for row in owners:
            mid = _row_id(row)
            if mid and mid in seen:
                continue
            if mid:
                seen.add(mid)
            owner_summaries.append(match_owner_summary(row))
        conflicts.append({
            "kind": kind,
            "identifier": identifier,
            "owners": owner_summaries,
        })

    return {
        "conflicts": conflicts,
        "invalid": invalid,
        "ready": not conflicts and not invalid,
        "conflict_count": len(conflicts),
        "invalid_count": len(invalid),
    }


def format_authority_gate_error(gate: dict[str, Any], *, max_rows: int = 8) -> str:
    """Human-readable block reason for upload UI / job failures."""
    conflicts = list(gate.get("conflicts") or [])
    invalid = list(gate.get("invalid") or [])
    total = len(conflicts) + len(invalid)
    lines = [
        "HMO authority gate blocked item creation: "
        f"{total} conflict(s) or invalid identifier(s). "
        "Open the Authority conflicts panel in HMO Studio, keep one match "
        "per shared ID (or unapprove the rest), then retry."
    ]
    shown = 0
    for row in conflicts:
        if shown >= max_rows:
            break
        owners_raw = row.get("owners") or []
        # Support both rich report owners and legacy text-only lists.
        owner_labels: list[str] = []
        for o in owners_raw[:4]:
            if isinstance(o, dict):
                owner_labels.append(str(o.get("entity_text") or ""))
            else:
                owner_labels.append(str(o))
        owners = ", ".join(label for label in owner_labels if label)
        extra = len(owners_raw) - 4
        if extra > 0:
            owners = f"{owners}, +{extra} more"
        lines.append(
            f"- conflict {row.get('kind')}={row.get('identifier')}: {owners}"
        )
        shown += 1
    for row in invalid:
        if shown >= max_rows:
            break
        entity = row.get("entity_text") or ""
        lines.append(
            f"- invalid {row.get('kind')}={row.get('identifier')} "
            f"({entity}: {row.get('reason')})"
        )
        shown += 1
    remaining = total - shown
    if remaining > 0:
        lines.append(f"- … +{remaining} more")
    return "\n".join(lines)
