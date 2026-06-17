"""Audit MARC field coverage: raw tag presence vs ``prepare_record_for_pipeline``.

Usage::

    cd backend && python scripts/audit_marc_coverage.py path/to/marc-input.json

Prints per-field fill rates and recoverable vs catalog-only gaps.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline.marc_ingest import prepare_record_for_pipeline  # noqa: E402


def _has_text(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_has_text(v) for v in value)
    if isinstance(value, dict):
        if value.get("title"):
            return _has_text(value.get("title"))
        return bool(value)
    return bool(value)


def _raw_tag_present(rec: dict[str, Any], tag_sub: str) -> bool:
    val = rec.get(tag_sub)
    if val is None:
        return False
    if isinstance(val, str):
        return bool(val.strip().strip('"'))
    return bool(val)


def _audit_record(raw: dict[str, Any]) -> dict[str, dict[str, bool]]:
    prepared = prepare_record_for_pipeline(raw)
    return {
        "provenance": {
            "raw_561": _raw_tag_present(raw, "561$a"),
            "raw_541": _raw_tag_present(raw, "541$a"),
            "after": _has_text(prepared.get("provenance")),
        },
        "genres": {
            "raw_655": _raw_tag_present(raw, "655$a"),
            "after": _has_text(prepared.get("genres")),
        },
        "place": {
            "raw_260a": _raw_tag_present(raw, "260$a"),
            "raw_264a": _raw_tag_present(raw, "264$a"),
            "raw_751a": _raw_tag_present(raw, "751$a"),
            "after": _has_text(prepared.get("place")),
        },
        "contents": {
            "raw_505": _raw_tag_present(raw, "505$a"),
            "raw_work_mentions": _has_text(prepared.get("work_mentions")),
            "after": _has_text(prepared.get("contents")),
        },
    }


def _rate(flags: list[bool]) -> tuple[int, int, float]:
    n = len(flags)
    filled = sum(1 for f in flags if f)
    pct = 100.0 * filled / n if n else 0.0
    return filled, n, pct


def audit_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)
    per_field: dict[str, dict[str, list[bool]]] = {
        "provenance": {"raw_561": [], "raw_541": [], "after": []},
        "genres": {"raw_655": [], "after": []},
        "place": {"raw_260a": [], "raw_264a": [], "raw_751a": [], "after": []},
        "contents": {"raw_505": [], "raw_work_mentions": [], "after": []},
    }
    for rec in records:
        row = _audit_record(rec)
        for field, metrics in row.items():
            for key, val in metrics.items():
                per_field[field][key].append(val)

    summary: dict[str, Any] = {"record_count": n, "fields": {}}
    for field, metrics in per_field.items():
        field_summary: dict[str, Any] = {}
        for key, flags in metrics.items():
            filled, total, pct = _rate(flags)
            field_summary[key] = {"filled": filled, "total": total, "pct": round(pct, 1)}
        after = field_summary["after"]["filled"]
        if field == "provenance":
            raw_max = max(
                field_summary["raw_561"]["filled"],
                field_summary["raw_541"]["filled"],
            )
        elif field == "place":
            raw_max = field_summary["raw_751a"]["filled"]
            raw_max = max(raw_max, field_summary["raw_260a"]["filled"])
            raw_max = max(raw_max, field_summary["raw_264a"]["filled"])
        elif field == "contents":
            raw_max = max(
                field_summary["raw_505"]["filled"],
                field_summary["raw_work_mentions"]["filled"],
            )
        else:
            raw_key = next(k for k in field_summary if k.startswith("raw_"))
            raw_max = field_summary[raw_key]["filled"]
        field_summary["recoverable_gap"] = max(0, raw_max - after)
        field_summary["catalog_only_gap"] = max(0, n - raw_max)
        summary["fields"][field] = field_summary
    return summary


def _print_summary(summary: dict[str, Any]) -> None:
    n = summary["record_count"]
    print(f"MARC coverage audit — {n} records\n")
    for field, metrics in summary["fields"].items():
        after = metrics["after"]
        print(f"## {field}")
        for key, stats in metrics.items():
            if key in ("recoverable_gap", "catalog_only_gap"):
                continue
            print(f"  {key:22s} {stats['filled']:3d}/{stats['total']} ({stats['pct']:5.1f}%)")
        print(f"  recoverable_gap        {metrics['recoverable_gap']} (raw present, after empty)")
        print(f"  catalog_only_gap       {metrics['catalog_only_gap']} (no raw tag in source)")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit MARC field coverage")
    parser.add_argument("input", type=Path, help="marc-input.json or similar export")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "records" in data:
        records = data["records"]
    elif isinstance(data, dict) and "marc_records" in data:
        raw_rows = data["marc_records"]
        records = []
        for row in raw_rows:
            if isinstance(row, dict) and isinstance(row.get("marc"), dict):
                marc = dict(row["marc"])
                if row.get("control_number") and "_control_number" not in marc:
                    marc["_control_number"] = row["control_number"]
                records.append(marc)
            elif isinstance(row, dict):
                records.append(row)
    elif isinstance(data, list):
        records = data
    else:
        raise SystemExit("Expected a JSON list or {\"records\": [...]} or {\"marc_records\": [...]}")

    summary = audit_records([dict(r) for r in records])
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        _print_summary(summary)


if __name__ == "__main__":
    main()
