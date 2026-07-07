"""SHACL upload gate helpers for HMO Wikibase item writes."""

from __future__ import annotations

from typing import Any

BLOCKING_SEVERITIES = frozenset({"Violation", "Error"})
_WIKIBASE_UNSUPPORTED_LANGS = frozenset({"und", ""})


def blocking_shacl_issues(
    issues: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return SHACL issues that must block a live upload by default."""
    if not issues:
        return []
    return [
        issue for issue in issues
        if str(issue.get("severity") or "") in BLOCKING_SEVERITIES
    ]


def format_shacl_block_message(issues: list[dict[str, Any]]) -> str:
    """Human-readable summary for audit logs and upload outcomes."""
    if not issues:
        return "blocked by SHACL validation"
    parts = [
        str(issue.get("message") or "SHACL violation").strip()
        for issue in issues[:5]
    ]
    extra = len(issues) - len(parts)
    if extra > 0:
        parts.append(f"(+{extra} more)")
    return "; ".join(parts)


def sanitize_wikibase_labels(labels: dict[str, str]) -> dict[str, str]:
    """Drop unsupported language codes and never emit ``und`` to Wikibase."""
    out: dict[str, str] = {}
    for lang, value in labels.items():
        text = str(value or "").strip()
        if not text:
            continue
        code = str(lang or "").strip().lower()
        if code in _WIKIBASE_UNSUPPORTED_LANGS:
            code = "en"
        out.setdefault(code, text)
    if not out:
        out["en"] = "Untitled"
    if "en" not in out:
        out["en"] = next(iter(out.values()))
    return out


def sanitize_wikibase_descriptions(descriptions: dict[str, str]) -> dict[str, str]:
    """Same language hygiene as :func:`sanitize_wikibase_labels`."""
    out: dict[str, str] = {}
    for lang, value in descriptions.items():
        text = str(value or "").strip()
        if not text:
            continue
        code = str(lang or "").strip().lower()
        if code in _WIKIBASE_UNSUPPORTED_LANGS:
            code = "en"
        out.setdefault(code, text)
    return out
