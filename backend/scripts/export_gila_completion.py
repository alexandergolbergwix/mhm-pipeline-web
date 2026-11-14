"""Export the complete Gila acceptance inventory from saved, source-bound evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline.wikidata_gila_completion import build_gila_completion_packet


def read_object(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object.")
    return value


def write_csv(path: Path, rows: list[Mapping[str, object]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                    if key in fields
                }
            )


def export_packet(
    audit_path: Path, snapshot_path: Path, receipt_paths: list[Path], output: Path
) -> dict[str, object]:
    audit = read_object(audit_path)
    snapshot = read_object(snapshot_path)
    entities = snapshot.get("entities")
    if not isinstance(entities, Mapping):
        raise ValueError("The public snapshot needs an entities object.")
    receipts = []
    for path in receipt_paths:
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("A receipt file must contain an array of objects.")
        receipts.extend(rows)
    packet = build_gila_completion_packet(audit, entities, repair_receipts=receipts)
    output.mkdir(parents=True, exist_ok=True)
    (output / "completion.json").write_text(
        json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    cases = packet["cases"]
    checked = packet["checked_manuscripts"]
    points = packet["email_points"]
    assert isinstance(cases, list) and isinstance(checked, list) and isinstance(points, list)
    fields = [
        "case_id",
        "control_number",
        "qid",
        "points",
        "disposition",
        "property",
        "source_field",
        "field",
        "scope",
        "source_value",
        "expected_value",
        "expected_author",
        "expected_qualifiers",
        "live_status",
        "live_provenance",
        "classification",
        "reason",
        "live_reason",
        "completion_reason",
        "live_revision",
    ]
    write_csv(output / "all-cases.csv", cases, fields)
    write_csv(output / "source-passages.csv", packet["source_passages"],
              ["passage_id", "control_number", "qid", "source_key", "source_field", "source_text", "points", "status", "reason"])
    write_csv(
        output / "manual-review.csv",
        [row for row in cases if row["disposition"] == "manual_review"],
        fields,
    )
    write_csv(
        output / "supported-corrections.csv",
        [row for row in cases if row["disposition"] == "repair_required"],
        fields,
    )
    write_csv(
        output / "not-checked.csv",
        [row for row in cases if row["disposition"] == "not_checked"],
        fields,
    )
    write_csv(
        output / "absent-source.csv",
        [row for row in cases if row.get("classification") == "absent_in_source"],
        fields,
    )
    write_csv(
        output / "checked-manuscripts.csv",
        checked,
        ["control_number", "qid", "revision", "disposition"],
    )
    corrected = packet["corrected_qids"]
    assert isinstance(corrected, list)
    write_csv(output / "corrected-qids.csv", [{"qid": qid} for qid in corrected], ["qid"])
    table = "\n".join(f"| {row['point']} | {row['title']} | {row['status']} |" for row in points)
    (output / "README.md").write_text(
        "# Gila completion packet\n\n"
        f"The packet covers {packet['record_count']} source records.\n"
        f"All source manuscripts have a public read: {packet['all_records_read']}.\n"
        f"Every email requirement has verified proof: {packet['all_requirements_verified']}.\n\n"
        "| Point | Requirement | Status |\n|---|---|---|\n" + table + "\n\n"
        "The JSON packet retains every source fact and case.\n"
        "It also retains duplicate identities and historical repair proposals.\n"
        "The CSV files separate source absence, manual review, and supported corrections.\n"
        "A separate file lists unchecked evidence.\n"
        "An unknown identity is a manual case. It does not authorize a new work or person.\n"
        "A correction needs a verified receipt for the current public entity revision.\n"
        "The packet performs no network request, database write, or public edit.\n",
        encoding="utf-8",
    )
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "snapshot_started_at": snapshot.get("started_at"),
        "snapshot_completed_at": snapshot.get("completed_at"),
        "inputs": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [audit_path, snapshot_path, *receipt_paths]
        },
        "outputs": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(output.iterdir())
            if path.is_file() and path.name != "manifest.json"
        },
        "record_count": packet["record_count"],
        "disposition_counts": packet["disposition_counts"],
        "corrected_qids": corrected,
        "all_requirements_verified": packet["all_requirements_verified"],
        "executable": False,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--live-snapshot", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = export_packet(args.audit, args.live_snapshot, args.receipts, args.output_dir)
    print(
        json.dumps(
            {
                key: result[key]
                for key in [
                    "record_count",
                    "disposition_counts",
                    "corrected_qids",
                    "all_requirements_verified",
                    "executable",
                ]
            }
        )
    )


if __name__ == "__main__":
    main()
