"""Hard export-quality gate for Wikidata Studio builds."""

from __future__ import annotations

from typing import Any

from converter.wikidata.item_validator import _KNOWN_BAD_P31_QIDS


def _label_text(item: Any) -> str:
    labels = getattr(item, "labels", {}) or {}
    if isinstance(labels, dict):
        return str(
            labels.get("en") or labels.get("he") or next(iter(labels.values()), "") or "",
        ).strip()
    return ""


def _stmt_values(item: Any, pid: str) -> list[str]:
    out: list[str] = []
    for s in getattr(item, "statements", []) or []:
        prop = getattr(s, "property_id", None) or getattr(s, "property", None)
        if prop == pid:
            out.append(str(getattr(s, "value", "") or ""))
    return out


def assert_wikidata_export_quality(items: list[Any]) -> None:
    """Raise when built items have ERROR-severity issues that indicate a build bug."""
    errors: list[str] = []
    for item in items:
        local_id = str(getattr(item, "local_id", "") or "")
        if not _label_text(item):
            errors.append(f"MISSING_LABEL {local_id}: item has no label")
        for p31 in _stmt_values(item, "P31"):
            if p31 in _KNOWN_BAD_P31_QIDS:
                errors.append(
                    f"P31_WRONG_QID {local_id}: {p31} ({_KNOWN_BAD_P31_QIDS[p31]})",
                )
    if not errors:
        return
    sample = errors[:12]
    suffix = f" (+{len(errors) - len(sample)} more)" if len(errors) > len(sample) else ""
    raise ValueError(
        f"Wikidata export quality gate failed with {len(errors)} issue(s){suffix}:\n"
        + "\n".join(sample),
    )
