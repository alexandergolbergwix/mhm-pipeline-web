"""Fail-closed authority checks required before HMO item creation."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


def validate_authority_rows(rows: Iterable[Any]) -> dict[str, Any]:
    owners: dict[tuple[str, str], set[str]] = defaultdict(set)
    invalid: list[dict[str, str]] = []
    for row in rows:
        if not bool(getattr(row, "approved", False)):
            continue
        text = str(getattr(row, "entity_text", "")).strip()
        for kind, value in (
            ("wikidata", getattr(row, "wikidata_qid", "")),
            ("mazal", getattr(row, "mazal_id", "")),
            ("viaf", getattr(row, "viaf_id", "")),
        ):
            identifier = str(value or "").strip()
            if not identifier:
                continue
            if kind == "viaf" and identifier.startswith("987007"):
                invalid.append({
                    "entity_text": text,
                    "kind": kind,
                    "identifier": identifier,
                    "reason": "NLI/Mazal identifier stored as VIAF",
                })
                continue
            owners[(kind, identifier)].add(text)
    conflicts = [
        {"kind": kind, "identifier": identifier, "owners": sorted(values)}
        for (kind, identifier), values in owners.items()
        if len(values) > 1
    ]
    return {
        "conflicts": conflicts,
        "invalid": invalid,
        "ready": not conflicts and not invalid,
    }


def format_authority_gate_error(gate: dict[str, Any], *, max_rows: int = 8) -> str:
    """Human-readable block reason for upload UI / job failures."""
    conflicts = list(gate.get("conflicts") or [])
    invalid = list(gate.get("invalid") or [])
    total = len(conflicts) + len(invalid)
    lines = [
        "HMO authority gate blocked item creation: "
        f"{total} conflict(s) or invalid identifier(s). "
        "Unapprove or correct the colliding approved AuthorityMatch rows, then retry."
    ]
    shown = 0
    for row in conflicts:
        if shown >= max_rows:
            break
        owners = ", ".join(str(o) for o in (row.get("owners") or [])[:4])
        extra = len(row.get("owners") or []) - 4
        if extra > 0:
            owners = f"{owners}, +{extra} more"
        lines.append(
            f"- conflict {row.get('kind')}={row.get('identifier')}: {owners}"
        )
        shown += 1
    for row in invalid:
        if shown >= max_rows:
            break
        lines.append(
            f"- invalid {row.get('kind')}={row.get('identifier')} "
            f"({row.get('entity_text')}: {row.get('reason')})"
        )
        shown += 1
    remaining = total - shown
    if remaining > 0:
        lines.append(f"- … +{remaining} more")
    return "\n".join(lines)
