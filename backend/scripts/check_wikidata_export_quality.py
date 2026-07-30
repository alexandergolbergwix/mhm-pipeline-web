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


_LABEL_SHELFMARK_RE = re.compile(r"([A-Z]{1,3}\.?\s?\d[\d\-/. ]*)$")
_CATALOG_NOTE_RE = (
    re.compile(r"MARC 33[678]"),
    re.compile(r"content type:|media type:|carrier type:", re.I),
    re.compile(r"^\s*(f|ff)\.?\s*\d+[ab]?\s*[:.]", re.I),
    re.compile(r"^\s*(According to|Related material|Formerly|Bound with)\b", re.I),
    re.compile(r"^\s*(דף|דפים|בסוף|בראש|בתוך)\s"),
)


def _values(statements: list[dict[str, Any]], pid: str) -> list[str]:
    out: list[str] = []
    for statement in statements:
        prop = str(statement.get("property_id") or statement.get("property") or "")
        if prop != pid:
            continue
        value = str(statement.get("value") or "").strip()
        if value:
            out.append(value)
    return out


def _norm_shelfmark(text: str) -> str:
    return re.sub(r"[\s.]+", "", str(text or "")).upper()


def _looks_like_catalog_note(text: str) -> bool:
    cleaned = str(text or "").strip()
    if not cleaned:
        return False
    if len(cleaned) > 180:
        return True
    return any(pattern.search(cleaned) for pattern in _CATALOG_NOTE_RE)


def _description_any(row: dict[str, Any]) -> list[str]:
    raw = row.get("descriptions") or row.get("descriptions_json") or {}
    if isinstance(raw, str):
        raw = _json_cell(raw)
    if isinstance(raw, dict):
        return [str(value or "") for value in raw.values()]
    return []


def _check_row(row: dict[str, Any]) -> dict[str, Any] | None:
    entity_type = str(row.get("entity_type") or row.get("sub_type") or "")
    checks: list[str] = []
    statements = _statements(row)
    pids = {
        str(statement.get("property_id") or statement.get("property") or "")
        for statement in statements
    }
    if statements and not any(pids - {""}):
        checks.append("export_missing_property_ids")

    evidence_pack = row.get("verify_evidence")
    if isinstance(evidence_pack, str):
        evidence_pack = _json_cell(evidence_pack)
    if not isinstance(evidence_pack, dict):
        evidence_pack = {}
    marc_slice = evidence_pack.get("marc") if isinstance(evidence_pack.get("marc"), dict) else {}
    claim_sources = (
        evidence_pack.get("claim_sources")
        if isinstance(evidence_pack.get("claim_sources"), dict) else {}
    )

    # Rule W-138 — a claim the judge cannot trace reads as unsupported.
    if claim_sources and (pids - {""}) - set(claim_sources):
        checks.append("claim_without_provenance_row")
    if any(
        isinstance(source, dict) and not source.get("supported")
        for source in claim_sources.values()
    ):
        checks.append("claim_without_evidence")

    local_targets = set(evidence_pack.get("local_reference_targets") or {})
    for statement in statements:
        value = str(statement.get("value") or "")
        if value.startswith("__LOCAL:") and value.removeprefix("__LOCAL:") not in local_targets:
            checks.append("dangling_local_reference")
            break

    if entity_type == "person":
        descriptions = " ".join(_description_any(row))
        if re.search(r"\(\d{3,4}\s*[-–]\s*\d{0,4}\)", descriptions):
            has_dates = any(
                isinstance(item, dict) and (item.get("birth_year") or item.get("death_year"))
                for item in _evidence(row)
            )
            if not has_dates:
                checks.append("person_dates_without_authority_evidence")

    if entity_type == "work":
        if len(_values(statements, "P1476")) > 1:
            checks.append("work_multiple_p1476")
        if str(row.get("descriptions", {}).get("en") if isinstance(row.get("descriptions"), dict) else "") == (
            "Work preserved in a Hebrew manuscript"
        ):
            checks.append("work_boilerplate_description")

    if entity_type == "manuscript":
        language_slice = str(marc_slice.get("languages") or "")
        if language_slice and "P407" not in pids:
            checks.append("missing_p407_with_language_evidence")
        elif language_slice.count("|") >= 1 and len(_values(statements, "P407")) < 2:
            checks.append("p407_drops_secondary_language")
        extent_slice = str(marc_slice.get("extent") or "")
        dimensions = _values(statements, "P2048") + _values(statements, "P2049")
        if dimensions and extent_slice:
            marc_numbers = {
                n.rstrip("0").rstrip(".")
                for n in re.findall(r"\d+(?:\.\d+)?", extent_slice)
            }
            emitted = {str(v).rstrip("0").rstrip(".") for v in dimensions}
            if marc_numbers and not (emitted & marc_numbers):
                checks.append("dimension_contradicts_extent")

    if entity_type == "manuscript":
        # Rule W-137 — cross-record contamination and note-as-description.
        catalog_ids = _values(statements, "P3959")
        shelfmarks = _values(statements, "P217")
        if len(catalog_ids) > 1:
            checks.append("manuscript_multiple_catalog_ids")
        if len(shelfmarks) > 1:
            checks.append("manuscript_multiple_shelfmarks")
        records = row.get("records") or row.get("record_ids") or []
        if isinstance(records, str):
            records = _json_cell(records)
        if isinstance(records, list) and len(records) > 1:
            checks.append("manuscript_multiple_source_records")
        match = _LABEL_SHELFMARK_RE.search(_label(row))
        if match and shelfmarks:
            wanted = _norm_shelfmark(match.group(1))
            have = {_norm_shelfmark(s) for s in shelfmarks}
            if wanted and not any(wanted in s or s in wanted for s in have):
                checks.append("label_shelfmark_mismatch")
        if any(_looks_like_catalog_note(text) for text in _description_any(row)):
            checks.append("description_is_catalog_note")
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


# Expected states, not defects: a CREATE work legitimately has no QID yet
# (Rule W-68 / W-121). Everything else must reach zero before a judge run.
_INFORMATIONAL_CHECKS = frozenset({"work_missing_existing_qid", "validation_warning"})


def _shared_identity_findings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Manuscripts that ship the same label + shelfmark are one item, twice."""
    groups: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        if str(row.get("entity_type") or row.get("sub_type") or "") != "manuscript":
            continue
        shelfmarks = _values(_statements(row), "P217")
        label = _label(row)
        if not label or not shelfmarks:
            continue
        key = (label.casefold(), _norm_shelfmark(shelfmarks[0]))
        groups.setdefault(key, []).append(str(row.get("local_id") or row.get("id") or ""))
    return [
        {
            "local_id": ", ".join(sorted(local_ids)),
            "entity_type": "manuscript",
            "label": label,
            "checks": ["manuscript_shared_identity"],
            "shelfmark": shelfmark,
        }
        for (label, shelfmark), local_ids in groups.items()
        if len(local_ids) > 1
    ]


def audit(path: Path) -> dict[str, Any]:
    rows, metadata = _items(path)
    findings = [
        finding
        for row in rows
        if (finding := _check_row(row))
    ]
    findings.extend(_shared_identity_findings(rows))
    counts: dict[str, int] = {}
    for finding in findings:
        for check in finding["checks"]:
            counts[check] = counts.get(check, 0) + 1
    blocking = {
        check: count for check, count in counts.items()
        if check not in _INFORMATIONAL_CHECKS
    }
    return {
        "file": str(path),
        "items": len(rows),
        "blocking_counts": dict(sorted(blocking.items())),
        "blocking_total": sum(blocking.values()),
        "informational_counts": {
            check: count for check, count in sorted(counts.items())
            if check in _INFORMATIONAL_CHECKS
        },
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
