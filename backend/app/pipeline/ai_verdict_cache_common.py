"""Shared helpers for content-addressed ``kind=ai_verdict`` cache keys."""

from __future__ import annotations

from typing import Any

_VOLATILE_METADATA_KEYS = frozenset({
    "timestamp", "timestamp_ms", "created_at", "updated_at", "judged_at",
    "requested_at", "fetched_at", "retrieved_at", "checked_at", "uploaded_at",
    "upload_at", "ai_verdict_at", "completed_at", "now", "nonce", "request_id",
    "trace_id",
})


def strip_volatile_metadata(value: Any) -> Any:
    """Remove transport timestamps without removing semantic date values."""
    if isinstance(value, dict):
        return {
            key: strip_volatile_metadata(nested)
            for key, nested in value.items()
            if str(key).lower() not in _VOLATILE_METADATA_KEYS
        }
    if isinstance(value, list):
        return [strip_volatile_metadata(entry) for entry in value]
    return value


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


# A provider failure uses abstain as a public no-decision verdict. The old
# value remains accepted so old rows can migrate safely (Rule W-211).
JUDGE_FAILURE_OVERALL = "verification_failed"
VERIFICATION_STATUS_JUDGED = "judged"
VERIFICATION_STATUS_PROVIDER_ERROR = "provider_error"
_PUBLIC_OVERALLS = frozenset({"full", "pass", "partial", "fail", "abstain"})


def normalise_public_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    """Return a verdict with a public overall value and diagnostic status."""
    public_verdict = {
        key: value
        for key, value in verdict.items()
        if key != "public_verdict_migration"
    }
    overall = str(public_verdict.get("overall") or "").strip().lower()
    is_failure = (
        overall in {JUDGE_FAILURE_OVERALL, "unknown"}
        or bool(public_verdict.get("judge_failure"))
        or str(public_verdict.get("verification_status") or "").strip().lower()
        == VERIFICATION_STATUS_PROVIDER_ERROR
    )
    if not is_failure and overall in _PUBLIC_OVERALLS:
        return public_verdict
    error = str(
        public_verdict.get("verification_error")
        or public_verdict.get("error")
        or ""
    ).strip()
    return {
        **public_verdict,
        "overall": "abstain",
        "judge_failure": True,
        "verification_status": VERIFICATION_STATUS_PROVIDER_ERROR,
        "verification_error": error or None,
    }


def normalise_verdict_body(verdict: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Split a raw eval-agent verdict envelope into a body and a judge error.

    Returns ``(body, judge_error)``. When the judge did not actually answer, the
    body reports ``overall="abstain"`` with ``judge_failure`` and
    ``verification_error`` instead of the substantive ``fail / no / no`` the
    eval-agent dataclass defaults to.

    One manuscript in run 48ba6c13 was persisted as `overall="fail",
    name_ok="no", type_ok="no"` with an empty ``reasoning``: a transport failure
    stored as a hard rejection. The envelope-level ``error`` that explained it
    ("no verdict (judge failure)") was never read on this path, and the job
    snapshot dropped the empty reasoning entirely, so the curator saw a reasoned-
    looking fail with no reason.
    """
    body = dict(verdict.get("verdict") or {}) if isinstance(verdict, dict) else {}
    envelope_error = str((verdict or {}).get("error") or "").strip()
    overall = str(body.get("overall") or "").strip()
    reasoning = str(body.get("reasoning") or "").strip()
    status = str(
        body.get("verification_status")
        or (verdict or {}).get("verification_status")
        or ""
    ).strip().lower()

    reason = ""
    if envelope_error:
        reason = envelope_error
    elif body.get("judge_failure") or status == VERIFICATION_STATUS_PROVIDER_ERROR:
        reason = str(
            body.get("verification_error")
            or "judge provider did not return a valid verdict"
        ).strip()
    elif not overall or overall in {"unknown", JUDGE_FAILURE_OVERALL}:
        reason = "judge returned no overall verdict"
    elif overall in _PUBLIC_OVERALLS - {"abstain"} and not reasoning:
        reason = "judge returned no reasoning"

    if not reason:
        return body, None

    return {
        **body,
        "overall": "abstain",
        "name_ok": "unknown",
        "type_ok": "unknown",
        "role_ok": body.get("role_ok") or "n/a",
        "judge_failure": True,
        "verification_status": VERIFICATION_STATUS_PROVIDER_ERROR,
        "verification_error": reason,
        "reasoning": (
            f"Judge failure: {reason}. This is NOT an assessment of the item — "
            "the check did not complete and must be re-run."
        ),
        "error": reason,
    }, reason


def is_judge_failure(body: Any) -> bool:
    return (
        isinstance(body, dict)
        and (
            bool(body.get("judge_failure"))
            or str(body.get("overall") or "").strip().lower()
            in {JUDGE_FAILURE_OVERALL, "unknown"}
            or str(body.get("verification_status") or "").strip().lower()
            == VERIFICATION_STATUS_PROVIDER_ERROR
        )
    )
