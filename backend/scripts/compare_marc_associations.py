"""Read-only comparison of saved holding associations and the current parser."""

import argparse
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

from app.pipeline import marc_occurrences
from app.pipeline.marc_ingest import prepare_record_for_pipeline

FIELDS = ("holding_942_name", "holding_942_shelfmark", "holding_942_city", "holding_942_country")


def compare_records(records):
    rows = []
    counts = Counter()
    for raw in records:
        source = deepcopy(raw)
        marc_occurrences.attach(source)
        expected = [
            {
                "source_key": marc_occurrences.source_key(row),
                "values": [
                    " ".join(marc_occurrences.values(row, code)) for code in ("a", "z", "x", "c")
                ],
            }
            for row in marc_occurrences.occurrences(source, "942")
        ]
        fresh = prepare_record_for_pipeline(raw)
        before = [str(raw.get(key) or "") for key in FIELDS]
        after = [str(fresh.get(key) or "") for key in FIELDS]
        independent_match = (
            after in [row["values"] for row in expected] if expected and any(after) else None
        )
        status = "original_occurrences" if expected else "original_occurrences_unavailable"
        counts[status] += 1
        saved_projection = any(key in raw for key in FIELDS)
        counts["saved_projection_available"] += saved_projection
        counts["changed_saved_associations"] += saved_projection and before != after
        counts["new_derived_associations"] += not saved_projection and any(after)
        counts["changed"] += before != after
        if before != after or fresh.get("holding_subfield_reviews") or independent_match is False:
            rows.append(
                {
                    "control_number": fresh["_control_number"],
                    "before": before,
                    "after": after,
                    "changed": before != after,
                    "source_status": status,
                    "expected_holding_occurrences": expected,
                    "independent_match": independent_match,
                    "reviews": fresh.get("holding_subfield_reviews", []),
                }
            )
    return {
        "mode": "read_only_local_parser_comparison",
        "record_count": len(records),
        "counts": dict(counts),
        "fields": list(FIELDS),
        "cases": rows,
        "public_writes": 0,
        "production_effect_count": "not_established",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source = json.loads(args.source.read_text())
    records = (
        source.get("records")
        if isinstance(source, dict) and "records" in source
        else list(source.values())
        if isinstance(source, dict)
        else source
    )
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError("The source must contain MARC record objects.")
    report = compare_records(records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {key: report[key] for key in ("mode", "record_count", "counts", "public_writes")}
        )
    )


if __name__ == "__main__":
    main()
