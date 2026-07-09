"""SHACL blocking helpers for eval-agent (mirrors backend upload gate)."""

from __future__ import annotations

from typing import Any

BLOCKING_SEVERITIES = frozenset({"Violation", "Error"})


def blocking_shacl_issues(
    issues: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return SHACL issues that must block approval."""
    if not issues:
        return []
    return [
        issue for issue in issues
        if str(issue.get("severity") or "") in BLOCKING_SEVERITIES
    ]
