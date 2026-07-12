#!/usr/bin/env python3
"""Audit Wikidata Studio exports and report only actionable quality failures.

The checker accepts both the authority-approved section export and the modern
merged diagnostic JSON/CSV export. It is intentionally read-only and emits
compact evidence: local ID, label, failing checks, and the source fields needed
to repair the builder.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

_HEBREW_RE = re.compile(r"[\u0590-\u05ff]")
_GERSHAYIM_RE = re.compile(r'(?<=[\u0590-\u05ff])"(?=[\u0590-\u05ff])')


def _json_cell(value: object) -> object:
    if value in (None, ""):
        return {}
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _items(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        rows: list[dict[str, Any]] = []
        with path.open(newline="", encoding="utf-8-sig") as stream:
            for raw in csv.DictReader(stream):
                row: dict[str, Any] = dict(raw)
                for field in (
                    "statements_json", "validation_issues_json",
                    "authority_evidence_json", "local_reference_targets_json",
                    "marc_context_json", "ai_verdict_json", "aliases_json",
                    "record_ids_json",
                ):
                    if field in row:
                        row[field.removesuffix("_json")] = _json_cell(row[field])
                row["labels"] = {
                    key: row.get(key, "")
                    for key in ("label_en", "label_he")
                    if row.get(key, "")
                }
                row["descriptions"] = {
                    key.removeprefix("description_"): row.get(key, "")
                    for key in ("description_en", "description_he")
                    if row.get(key, "")
                }
                rows.append(row)
        return rows, {"format": "csv", "diagnostic_fields": "approved" in (rows[0] if rows else {})}

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)], {"format": "json"}
    if not isinstance(payload, dict):
        return [], {"format": "json"}
    candidates = payload.get("items") or payload.get("results") or payload.get("verdicts") or []
    rows = [row for row in candidates if isinstance(row, dict)]
    return rows, {
        "format": "json",
        "approved_only": payload.get("approved_only"),
        "diagnostic_fields": any("approved" in row or "ai_verdict" in row for row in rows),
    }


def _label(row: dict[str, Any]) -> str:
    labels = row.get("labels")
    if isinstance(labels, dict):
        return str(labels.get("he") or labels.get("en") or "")
    return str(row.get("label_he") or row.get("label_en") or "")


def _description_en(row: dict[str, Any]) -> str:
    descriptions = row.get("descriptions")
    if isinstance(descriptions, dict):
        return str(descriptions.get("en") or "")
    return str(row.get("description_en") or "")


def _statements(row: dict[str, Any]) -> list[dict[str, Any]]:
    values = row.get("statements") or row.get("claims") or []
    if isinstance(values, str):
        values = _json_cell(values)
    return (
        [value for value in values if isinstance(value, dict)]
        if isinstance(values, list)
        else []
    )


def _evidence(row: dict[str, Any]) -> list[dict[str, Any]]:
    values = row.get("work_candidate_evidence") or []
    if isinstance(values, str):
        values = _json_cell(values)
    return (
        [value for value in values if isinstance(value, dict)]
        if isinstance(values, list)
        else []
    )


def _check_row(row: dict[str, Any]) -> dict[str, Any] | None:
    entity_type = str(row.get("entity_type") or row.get("sub_type") or "")
    checks: list[str] = []
    statements = _statements(row)
    pids = {
        str(statement.get("property_id") or statement.get("property") or "")
        for statement in statements
    }
    if "approved" not in row:
        checks.append("export_missing_item_approval")
    if "ai_verdict" not in row and "ai_verdict_json" not in row:
        checks.append("export_missing_ai_verdict")

    if entity_type == "work":
        if not str(row.get("existing_qid") or row.get("qid") or ""):
            checks.append("work_missing_existing_qid")
        description_en = _description_en(row)
        author_signal = (
            "author recorded" in description_en.casefold()
            or re.search(r"\bby\s+", description_en.casefold()) is not None
        )
        if author_signal and not ({"P50", "P2093"} & pids):
            checks.append("work_missing_author_claim")
        if _HEBREW_RE.search(description_en):
            checks.append("english_description_contains_hebrew")
        label = _label(row)
        for item in _evidence(row):
            raw = str(item.get("raw_title") or item.get("title") or "")
            if _GERSHAYIM_RE.search(raw):
                raw_count = len(_GERSHAYIM_RE.findall(raw))
                if len(_GERSHAYIM_RE.findall(label)) < raw_count:
                    checks.append("hebrew_abbreviation_quote_lost")
                    break

    validation = row.get("validation_issues") or row.get("validation_issues_json") or []
    if isinstance(validation, str):
        validation = _json_cell(validation)
    if isinstance(validation, list):
        severities = {
            str(issue.get("severity") or "").lower()
            for issue in validation
            if isinstance(issue, dict)
        }
        if "error" in severities:
            checks.append("validation_error")
        if "warning" in severities:
            checks.append("validation_warning")

    if not checks:
        return None
    relevant: dict[str, Any] = {
        "local_id": row.get("local_id") or row.get("id"),
        "entity_type": entity_type,
        "label": _label(row),
        "checks": sorted(set(checks)),
    }
    if entity_type == "work":
        relevant["description_en"] = _description_en(row)
        relevant["existing_qid"] = row.get("existing_qid") or row.get("qid")
        relevant["author_properties"] = sorted(pid for pid in pids if pid in {"P50", "P2093"})
        relevant["work_evidence"] = [
            {
                key: item.get(key)
                for key in ("source_field", "reason", "raw_title", "source_text")
                if item.get(key)
            }
            for item in _evidence(row)[:2]
        ]
    return relevant


def audit(path: Path) -> dict[str, Any]:
    rows, metadata = _items(path)
    findings = [
        finding
        for row in rows
        if (finding := _check_row(row))
    ]
    counts: dict[str, int] = {}
    for finding in findings:
        for check in finding["checks"]:
            counts[check] = counts.get(check, 0) + 1
    return {
        "file": str(path),
        "items": len(rows),
        "export_mode": (
            "authority-approved-only+item-review"
            if metadata.get("approved_only") is True and metadata.get("diagnostic_fields")
            else "authority-approved-only"
            if metadata.get("approved_only") is True
            else "diagnostic" if metadata.get("diagnostic_fields") else "unknown"
        ),
        "finding_items": len(findings),
        "counts": dict(sorted(counts.items())),
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true", help="exit 1 when any finding exists")
    args = parser.parse_args()
    try:
        report = audit(args.export.expanduser())
    except (OSError, csv.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.expanduser().write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 1 if args.strict and report["finding_items"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
