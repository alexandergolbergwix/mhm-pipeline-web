"""Audit DATE entities in an extraction-all JSON export.

Parses each NER ``DATE`` span to a Gregorian year (when possible), flags
suspicious or duplicate rows, and optionally cross-checks against prepared
MARC (``ms_year``, ``colophon_year``, catalog century dates).

Usage::

    cd backend
    python -m scripts.check_extraction_dates \\
        ~/Downloads/run-48ba6c13-...-extraction-all.json

    # Cross-check against a project export that includes marc_records:
    python -m scripts.check_extraction_dates \\
        extraction-all.json \\
        --marc-export ~/Downloads/marc-input.json

    # Machine-readable report:
    python -m scripts.check_extraction_dates extraction-all.json --json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline.date_entity_normalize import normalize_date_entity_year
from app.pipeline.marc_date_sources import manuscript_year_provenance
from app.pipeline.marc_ingest import prepare_record_for_pipeline


@dataclass
class ParsedDate:
    text: str
    gregorian_year: int | None = None
    hebrew_year: int | None = None
    parse_method: str = "unparsed"
    warnings: list[str] = field(default_factory=list)


@dataclass
class DateRow:
    control_number: str
    text: str
    source: str
    confidence: float | None
    ai_verdict: str | None
    approved: bool
    entity_count: int
    parsed: ParsedDate
    marc_ms_year: int | None = None
    marc_catalog_year: int | None = None
    marc_colophon_year: int | None = None
    marc_ms_year_source: str | None = None
    flags: list[str] = field(default_factory=list)


def normalize_control_number(value: str | None) -> str:
    return str(value or "").strip().strip("\"'")


def parse_date_entity_text(text: str) -> ParsedDate:
    """Best-effort CE / Hebrew year from a provenance-NER DATE span."""
    raw = (text or "").strip()
    out = ParsedDate(text=raw)
    if not raw:
        out.warnings.append("empty_text")
        return out

    year = normalize_date_entity_year(raw)
    if year is not None:
        out.gregorian_year = year
        out.hebrew_year = year + 3760 if 100 < year < 2100 else None
        out.parse_method = "normalized_yyyy"
        return out

    out.warnings.append("unparsed")
    return out


def load_extraction_entities(path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "entities" in data:
        return data.get("run_id"), list(data["entities"])
    if isinstance(data, list):
        return None, data
    raise ValueError(f"Unrecognised extraction JSON shape in {path}")


def load_marc_by_control_number(
    path: Path | None,
    *,
    run_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    if isinstance(data, dict) and "marc_records" in data:
        records = list(data["marc_records"])
    elif isinstance(data, list):
        records = data
    else:
        raise ValueError(f"Unrecognised MARC JSON shape in {path}")

    out: dict[str, dict[str, Any]] = {}
    for row in records:
        if run_id and str(row.get("run_id") or "") != run_id:
            continue
        cn = normalize_control_number(row.get("control_number"))
        marc = row.get("marc")
        if isinstance(marc, dict):
            out[cn] = marc
        elif isinstance(row, dict):
            out[cn] = row
    return out


def _flag_row(row: DateRow) -> list[str]:
    flags: list[str] = []
    parsed = row.parsed

    if row.entity_count > 1:
        flags.append("duplicate_entity_rows")

    if parsed.gregorian_year is None:
        flags.append("unparsed")
    elif parsed.gregorian_year < 800 or parsed.gregorian_year > 2100:
        flags.append("year_out_of_range")

    if parsed.warnings:
        flags.extend(parsed.warnings)

    if parsed.gregorian_year is not None and parsed.gregorian_year < 1000:
        flags.append("suspicious_short_year")

    if row.ai_verdict == "fail":
        flags.append("ai_verdict_fail")

    if row.marc_ms_year is not None and parsed.gregorian_year is not None:
        if abs(row.marc_ms_year - parsed.gregorian_year) > 50:
            flags.append("mismatch_vs_marc_ms_year")

    if (
        row.marc_colophon_year is not None
        and parsed.gregorian_year is None
    ):
        flags.append("marc_colophon_year_not_extracted")

    if (
        row.marc_colophon_year is not None
        and parsed.gregorian_year is not None
        and row.marc_colophon_year != parsed.gregorian_year
        and row.marc_ms_year_source == "colophon"
    ):
        flags.append("mismatch_vs_colophon_year")

    return flags


def audit_extraction_dates(
    entities: list[dict[str, Any]],
    marc_by_cn: dict[str, dict[str, Any]] | None = None,
) -> list[DateRow]:
    date_entities = [e for e in entities if str(e.get("type") or "").upper() == "DATE"]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for ent in date_entities:
        cn = normalize_control_number(ent.get("control_number"))
        text = str(ent.get("text") or "").strip()
        grouped.setdefault((cn, text), []).append(ent)

    rows: list[DateRow] = []
    for (cn, text), group in sorted(grouped.items()):
        first = group[0]
        parsed = parse_date_entity_text(text)
        marc_ms_year = marc_catalog_year = marc_colophon_year = None
        marc_ms_year_source = None
        if marc_by_cn and cn in marc_by_cn:
            prepared = prepare_record_for_pipeline(dict(marc_by_cn[cn]))
            prov = manuscript_year_provenance(prepared)
            marc_ms_year = prov.get("ms_year")
            marc_catalog_year = prov.get("catalog_year")
            marc_colophon_year = prov.get("colophon_year")
            marc_ms_year_source = prov.get("ms_year_source")

        row = DateRow(
            control_number=cn,
            text=text,
            source=str(first.get("source") or ""),
            confidence=first.get("confidence"),
            ai_verdict=first.get("ai_verdict_overall"),
            approved=bool(first.get("approved")),
            entity_count=len(group),
            parsed=parsed,
            marc_ms_year=marc_ms_year,
            marc_catalog_year=marc_catalog_year,
            marc_colophon_year=marc_colophon_year,
            marc_ms_year_source=marc_ms_year_source,
        )
        row.flags = _flag_row(row)
        rows.append(row)
    return rows


def _print_table(rows: list[DateRow]) -> None:
    print(f"{'control_number':<22} {'parsed_ce':>9}  {'ms_year':>7}  {'colophon':>8}  flags")
    print("-" * 90)
    for row in rows:
        ce = row.parsed.gregorian_year
        ce_s = str(ce) if ce is not None else "—"
        ms = str(row.marc_ms_year) if row.marc_ms_year is not None else "—"
        col = str(row.marc_colophon_year) if row.marc_colophon_year is not None else "—"
        flag_s = ", ".join(row.flags) if row.flags else "ok"
        text_short = row.text if len(row.text) <= 36 else row.text[:33] + "…"
        print(f"{row.control_number:<22} {ce_s:>9}  {ms:>7}  {col:>8}  {flag_s}")
        print(f"  text: {text_short!r}  source={row.source}  n={row.entity_count}  ai={row.ai_verdict}")


def _summary(rows: list[DateRow]) -> dict[str, Any]:
    flagged = [r for r in rows if r.flags]
    unparsed = [r for r in rows if "unparsed" in r.flags]
    return {
        "date_entity_groups": len(rows),
        "date_entity_rows": sum(r.entity_count for r in rows),
        "flagged_groups": len(flagged),
        "unparsed_groups": len(unparsed),
        "ai_fail_groups": sum(1 for r in rows if r.ai_verdict == "fail"),
        "with_marc_ms_year": sum(1 for r in rows if r.marc_ms_year is not None),
        "with_colophon_year": sum(1 for r in rows if r.marc_colophon_year is not None),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extraction_json", type=Path, help="extraction-all.json export")
    parser.add_argument(
        "--marc-export",
        type=Path,
        default=None,
        help="Project export JSON with marc_records (optional cross-check)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Filter marc_records to this run_id when using --marc-export",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON report on stdout")
    args = parser.parse_args()

    run_id, entities = load_extraction_entities(args.extraction_json)
    marc_run_id = args.run_id or run_id
    marc_by_cn = load_marc_by_control_number(args.marc_export, run_id=marc_run_id)
    rows = audit_extraction_dates(entities, marc_by_cn or None)
    summary = _summary(rows)

    if args.json:
        payload = {
            "extraction_file": str(args.extraction_json),
            "marc_export": str(args.marc_export) if args.marc_export else None,
            "run_id": run_id,
            "summary": summary,
            "rows": [
                {
                    **{k: v for k, v in asdict(row).items() if k != "parsed"},
                    "parsed": asdict(row.parsed),
                }
                for row in rows
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"Extraction: {args.extraction_json}")
    if args.marc_export:
        print(f"MARC export: {args.marc_export} ({len(marc_by_cn)} records)")
    print(
        f"DATE groups: {summary['date_entity_groups']} "
        f"({summary['date_entity_rows']} raw rows, "
        f"{summary['flagged_groups']} flagged, "
        f"{summary['unparsed_groups']} unparsed)"
    )
    print()
    _print_table(rows)


if __name__ == "__main__":
    main()
