"""Hard export-quality gate for Wikidata Studio builds."""

from __future__ import annotations

from typing import Any

from converter.wikidata.item_validator import validate_item


def _label_text(item: Any) -> str:
    labels = getattr(item, "labels", {}) or {}
    if isinstance(labels, dict):
        return str(
            labels.get("en") or labels.get("he") or next(iter(labels.values()), "") or "",
        ).strip()
    return ""


def assert_wikidata_export_quality(items: list[Any]) -> None:
    """Raise when built items have ERROR-severity issues that indicate a build bug.

    Uses the full ``validate_item`` ERROR set so a bad projection cannot be
    cached or handed to the curator as clean Studio output.
    """
    errors: list[str] = []
    for item in items:
        local_id = str(getattr(item, "local_id", "") or "")
        if not _label_text(item):
            errors.append(f"MISSING_LABEL {local_id}: item has no label")
        for issue in validate_item(item):
            if issue.severity != "error":
                continue
            errors.append(f"{issue.code} {local_id}: {issue.message}")
    if not errors:
        return
    sample = errors[:12]
    suffix = f" (+{len(errors) - len(sample)} more)" if len(errors) > len(sample) else ""
    raise ValueError(
        f"Wikidata export quality gate failed with {len(errors)} issue(s){suffix}:\n"
        + "\n".join(sample),
    )
