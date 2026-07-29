"""Hard export-quality gate for Wikidata Studio builds."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from converter.wikidata.item_validator import validate_item

# A label ending in a library signature: "Jerusalem, NLI, F 12345".
_LABEL_SHELFMARK_RE = re.compile(r"([A-Z]{1,3}\.?\s?\d[\d\-/. ]*)$")


def _statement_values(item: Any, property_id: str) -> list[str]:
    out: list[str] = []
    for stmt in getattr(item, "statements", []) or []:
        pid = str(
            getattr(stmt, "property_id", None)
            or (stmt.get("property_id") if isinstance(stmt, dict) else "")
            or (stmt.get("property") if isinstance(stmt, dict) else "")
            or "",
        )
        if pid != property_id:
            continue
        value = (
            getattr(stmt, "value", None)
            if not isinstance(stmt, dict) else stmt.get("value")
        )
        text = str(value or "").strip()
        if text:
            out.append(text)
    return out


def _norm_shelfmark(text: str) -> str:
    return re.sub(r"[\s.]+", "", str(text or "")).upper()


def _manuscript_identity_errors(items: list[Any]) -> list[str]:
    """Catch cross-record contamination before it can reach public Wikidata.

    Three canonical manuscripts once shipped with the same label and the same
    P217 because the legacy MARC join matched on any linked control number
    (Rule W-137). Identity defects are build bugs, never curator decisions.
    """
    errors: list[str] = []
    by_identity: dict[tuple[str, str], list[str]] = defaultdict(list)
    for item in items:
        if str(getattr(item, "entity_type", "") or "").strip().lower() != "manuscript":
            continue
        local_id = str(getattr(item, "local_id", "") or "")
        label = _label_text(item)
        shelfmarks = _statement_values(item, "P217")
        catalog_ids = _statement_values(item, "P3959")

        if len(catalog_ids) > 1:
            errors.append(
                f"MANUSCRIPT_MULTIPLE_CATALOG_IDS {local_id}: "
                f"P3959 emitted {len(catalog_ids)}× ({', '.join(catalog_ids)}) — "
                "a manuscript item owns exactly one catalog record",
            )
        if len(shelfmarks) > 1:
            errors.append(
                f"MANUSCRIPT_MULTIPLE_SHELFMARKS {local_id}: "
                f"P217 emitted {len(shelfmarks)}× ({', '.join(shelfmarks)})",
            )

        match = _LABEL_SHELFMARK_RE.search(label)
        if match and shelfmarks:
            wanted = _norm_shelfmark(match.group(1))
            have = {_norm_shelfmark(s) for s in shelfmarks}
            if wanted and not any(wanted in s or s in wanted for s in have):
                errors.append(
                    f"LABEL_SHELFMARK_MISMATCH {local_id}: label '{label}' "
                    f"does not match P217 {shelfmarks}",
                )
        if label and shelfmarks:
            by_identity[(label.casefold(), _norm_shelfmark(shelfmarks[0]))].append(local_id)

    for (label, shelfmark), local_ids in by_identity.items():
        if len(local_ids) > 1:
            errors.append(
                f"MANUSCRIPT_SHARED_IDENTITY {', '.join(sorted(local_ids))}: "
                f"{len(local_ids)} manuscript items share label '{label}' and "
                f"shelfmark '{shelfmark}'",
            )
    return errors


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
    errors: list[str] = _manuscript_identity_errors(items)
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
