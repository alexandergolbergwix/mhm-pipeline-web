"""Shared helpers for content-addressed ``kind=ai_verdict`` cache keys."""

from __future__ import annotations

from typing import Any


def normalise_string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({str(v) for v in values if v is not None and str(v)})


def normalise_claim_rows(claims: Any) -> list[dict[str, Any]]:
    if not isinstance(claims, list):
        return []
    normalised: list[dict[str, Any]] = []
    for stmt in claims:
        if not isinstance(stmt, dict):
            continue
        normalised.append({
            "property_id": str(stmt.get("property_id") or stmt.get("property") or ""),
            "datatype": stmt.get("datatype"),
            "value": stmt.get("value"),
        })
    return sorted(
        normalised,
        key=lambda row: (
            row.get("property_id") or "",
            str(row.get("datatype") or ""),
            str(row.get("value") or ""),
        ),
    )


def normalise_shacl_issues(issues: Any) -> list[dict[str, str]]:
    if not isinstance(issues, list):
        return []
    out: list[dict[str, str]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        out.append({
            "code": str(issue.get("code") or ""),
            "severity": str(issue.get("severity") or ""),
            "message": str(issue.get("message") or ""),
        })
    return sorted(out, key=lambda row: (row["code"], row["severity"], row["message"]))


def normalise_statement_rows(statements: Any) -> list[dict[str, Any]]:
    if not isinstance(statements, list):
        return []
    rows: list[dict[str, Any]] = []
    for stmt in statements:
        if not isinstance(stmt, dict):
            continue
        rows.append({
            "property": str(stmt.get("property") or stmt.get("property_id") or ""),
            "value_type": str(stmt.get("value_type") or stmt.get("datatype") or ""),
            "value": stmt.get("value"),
        })
    return sorted(
        rows,
        key=lambda row: (
            row.get("property") or "",
            row.get("value_type") or "",
            str(row.get("value") or ""),
        ),
    )


def sanitise_stored_verdict(
    stored: Any,
    *,
    expected_fingerprint: str,
) -> dict[str, Any] | None:
    """Return *stored* only when its ``cache_key`` matches *expected_fingerprint*."""
    if not isinstance(stored, dict) or not stored:
        return None
    cache_key = stored.get("cache_key")
    if not cache_key:
        return stored
    if str(cache_key) == expected_fingerprint:
        return stored
    return None
