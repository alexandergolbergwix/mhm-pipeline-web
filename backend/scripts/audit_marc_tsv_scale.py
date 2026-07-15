"""Stream a large MARC TSV through normalization and a bounded build smoke test.

Usage::

    cd backend
    .venv/bin/python -m scripts.audit_marc_tsv_scale input.tsv \
        --sample-build 1000 --json /tmp/marc-scale-audit.json

The full TSV is never retained in memory. Every row is normalized; only a
reservoir sample is retained for the optional builder smoke test.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

from app.pipeline.marc_ingest import prepare_record_for_pipeline


_FACSIMILE_RE = re.compile(
    r"צלום|פקסימיל|photocopy|facsimile|reproduction|צילום", re.IGNORECASE
)
_NEGATIVE_PHOTO_RE = re.compile(
    r"אין צילום|לא נסרק|no (?:photo|image|scan)", re.IGNORECASE
)
_ILLUSTRATION_RE = re.compile(
    r"illustrat|illuminat|ציורים|איורים|מעוטר|מיניאטור", re.IGNORECASE
)


def _text(row: dict[str, Any], *keys: str) -> str:
    return " ".join(str(row.get(key) or "") for key in keys)


def _reservoir_add(
    sample: list[dict[str, Any]],
    row: dict[str, Any],
    seen: int,
    max_size: int,
    rng: random.Random,
) -> None:
    if len(sample) < max_size:
        sample.append(row)
        return
    index = rng.randrange(seen)
    if index < len(sample):
        sample[index] = row


def audit(path: Path, sample_size: int, seed: int) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    error_types: Counter[str] = Counter()
    examples: dict[str, list[dict[str, str]]] = {}
    sample: list[dict[str, Any]] = []
    rng = random.Random(seed)
    rows = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        headers = len(reader.fieldnames or [])
        for raw in reader:
            rows += 1
            row = dict(raw)
            if sample_size:
                _reservoir_add(sample, row, rows, sample_size, rng)
            control_number = str(row.get("001") or "").strip().strip('"')
            if not control_number:
                counts["missing_control_number"] += 1
            if not any(str(row.get(key) or "").strip().strip('"') for key in ("260$c", "264$c")):
                counts["missing_260_264_date"] += 1
            holder = str(row.get("710$a") or "")
            if "unknown library" in holder.casefold():
                counts["unknown_library_holder"] += 1
            text = _text(row, "245$a", "245$b", "245$c", "500$a", "533$a", "534$p", "534$c", "590$a", "595$a")
            if _ILLUSTRATION_RE.search(text):
                counts["illustration_text_signal"] += 1
            if _FACSIMILE_RE.search(text):
                counts["facsimile_text_signal"] += 1
                if _NEGATIVE_PHOTO_RE.search(text):
                    counts["negative_photo_signal"] += 1
            try:
                prepare_record_for_pipeline(row)
            except Exception as exc:  # noqa: BLE001 - audit must continue
                key = f"{type(exc).__name__}: {exc}"
                error_types[key] += 1
                examples.setdefault("normalization_errors", [])
                if len(examples["normalization_errors"]) < 20:
                    examples["normalization_errors"].append({
                        "control_number": control_number,
                        "error": key[:240],
                    })
    result: dict[str, Any] = {
        "records": rows,
        "columns": headers,
        "counts": dict(counts),
        "normalization_errors": sum(error_types.values()),
        "error_types": dict(error_types),
        "examples": examples,
        "sample_size": min(sample_size, rows),
        "sample_seed": seed,
    }
    if sample_size:
        from converter.wikidata.item_builder import WikidataItemBuilder

        builder = WikidataItemBuilder()
        build_errors: list[dict[str, str]] = []
        for raw in sample:
            try:
                builder.build_manuscript_item(prepare_record_for_pipeline(raw))
            except Exception as exc:  # noqa: BLE001 - audit must continue
                if len(build_errors) < 20:
                    build_errors.append({
                        "control_number": str(raw.get("001") or ""),
                        "error": f"{type(exc).__name__}: {exc}"[:240],
                    })
        result["build_errors"] = len(build_errors)
        result["build_error_examples"] = build_errors
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--sample-build", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=9061)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    result = audit(args.input, max(0, args.sample_build), args.seed)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json:
        args.json.write_text(output + "\n", encoding="utf-8")
    print(output)
    if result["normalization_errors"] or result.get("build_errors", 0):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
