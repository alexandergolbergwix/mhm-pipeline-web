"""Audit MARC→RDF→Wikidata field coverage on a large TSV corpus.

The audit is intentionally conservative: raw MARC columns are never treated as
public Wikidata claims merely because they survived normalization. It reports
which non-empty tags are structured, which are preserved only as raw evidence,
and which are currently unmapped. Only a bounded reservoir is built.

Usage::

    cd backend && .venv/bin/python -m scripts.audit_mapping_coverage input.tsv \
        --sample-build 1000 --json /tmp/mapping-coverage.json
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.pipeline.marc_ingest import prepare_record_for_pipeline

# Tags whose values are consumed by a structured ExtractedData field and then
# by GraphBuilder/Wikidata projections. The table is deliberately explicit so
# adding a handler requires updating this audit.
TAG_TARGETS: dict[str, tuple[str, ...]] = {
    "001": ("_control_number",), "130": ("title",), "240": ("title",),
    "245": ("title",), "246": ("variant_titles",), "247": ("variant_titles",),
    "740": ("variant_titles",), "100": ("authors",), "110": ("authors",),
    "111": ("authors",), "700": ("contributors",), "710": ("contributors",),
    "711": ("contributors",), "800": ("contributors",), "810": ("contributors",),
    "811": ("contributors",), "260": ("dates", "place"), "264": ("dates", "place"),
    "300": ("extent", "height_mm", "width_mm"), "340": ("materials",),
    "041": ("languages",), "336": ("content_types",), "337": ("media_types",),
    "338": ("carrier_types",), "546": ("languages", "has_vocalization", "has_cantillation"),
    "500": ("notes", "colophon_text", "scribal_interventions", "canonical_references", "contents"),
    "505": ("contents",), "510": ("catalog_references",), "561": ("provenance",),
    "563": ("binding_info",), "600": ("subjects",), "610": ("subjects",),
    "611": ("subjects",), "630": ("subjects",), "650": ("subjects",),
    "651": ("subjects", "related_places"), "655": ("genres",),
    "856": ("digital_url", "iiif_manifest_url"), "040": ("holding_institution",),
    "852": ("holding_institution", "shelfmark"), "090": ("shelfmark",),
    "094": ("shelfmark",),
    "091": ("shelfmark",), "093": ("shelfmark",), "099": ("shelfmark",),
    "520": ("summary",), "540": ("rights_statement", "restriction_url"),
    "250": ("notes",), "562": ("notes",),
    "939": ("usage_restriction", "restriction_url"), "773": ("volume_members",),
    "490": ("volume_members",), "541": ("acquisition_source", "provenance_events"),
    "542": ("copyright_notice",), "583": ("condition_notes", "provenance_events"),
    "730": ("related_works",), "751": ("related_places",), "752": ("related_places",),
}

# These tags are useful catalog/evidence metadata but are not yet projected to
# a dedicated ontology/Wikidata property. They are reported separately from
# genuinely unknown tags so the gap is actionable rather than silently lost.
EVIDENCE_ONLY_TAGS = {
    "000", "005", "006", "008", "024", "033", "034", "035", "036", "040",
    "046", "050", "090", "095", "350", "351", "430", "490", "501", "524",
    "530", "533", "534", "536", "544", "545", "550", "555", "580", "581",
    "585", "588", "590", "594", "595", "596", "597", "665", "698", "775",
    "830", "880", "903", "906", "907", "909", "914", "915", "920", "921",
    "924", "931", "933", "934", "935", "942", "943", "945", "951", "952",
    "953", "956", "957", "958", "959", "964", "965", "966", "967", "968",
    "970", "983", "984", "988", "999", "File", "date",
}


def _nonempty(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, str):
        return bool(value.strip().strip('"'))
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return bool(value)


def _tag(key: str) -> str:
    return key.lstrip("\ufeff").split("$", 1)[0]


def _reservoir(sample: list[dict[str, Any]], row: dict[str, Any], seen: int,
               size: int, rng: random.Random) -> None:
    if len(sample) < size:
        sample.append(row)
        return
    index = rng.randrange(seen)
    if index < size:
        sample[index] = row


def audit(path: Path, sample_size: int, seed: int) -> dict[str, Any]:
    raw_counts: Counter[str] = Counter()
    raw_examples: dict[str, list[dict[str, str]]] = defaultdict(list)
    mapped_counts: Counter[str] = Counter()
    normalization_errors: list[dict[str, str]] = []
    sample: list[dict[str, Any]] = []
    rng = random.Random(seed)
    records = 0
    columns = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        columns = len(reader.fieldnames or [])
        for raw in reader:
            records += 1
            row = dict(raw)
            if sample_size:
                _reservoir(sample, row, records, sample_size, rng)
            try:
                prepared = prepare_record_for_pipeline(row)
            except Exception as exc:  # noqa: BLE001 - audit continues
                if len(normalization_errors) < 20:
                    normalization_errors.append({
                        "control_number": str(row.get("001") or ""),
                        "error": f"{type(exc).__name__}: {exc}"[:240],
                    })
                continue
            for key, value in row.items():
                if not _nonempty(value):
                    continue
                tag = _tag(key)
                raw_counts[tag] += 1
                targets = TAG_TARGETS.get(tag, ())
                if any(_nonempty(prepared.get(target)) for target in targets):
                    mapped_counts[tag] += 1
                if len(raw_examples[tag]) < 3:
                    raw_examples[tag].append({
                        "control_number": str(row.get("001") or ""),
                        "column": key,
                        "value": str(value)[:240],
                    })
    statuses: dict[str, dict[str, Any]] = {}
    for tag, count in raw_counts.items():
        mapped = mapped_counts[tag]
        if tag in TAG_TARGETS:
            status = "structured" if mapped == count else "partially_structured"
        elif tag in EVIDENCE_ONLY_TAGS:
            status = "evidence_only"
        else:
            status = "unmapped"
        statuses[tag] = {
            "status": status, "records_nonempty": count,
            "records_with_structured_value": mapped,
            "mapping_rate_pct": round(100 * mapped / count, 1) if count else 0.0,
            "examples": raw_examples[tag],
        }
    result: dict[str, Any] = {
        "report_version": 1, "records": records, "columns": columns,
        "normalization_errors": len(normalization_errors),
        "normalization_error_examples": normalization_errors,
        "structured_tags": sum(1 for v in statuses.values() if v["status"] == "structured"),
        "partially_structured_tags": sum(1 for v in statuses.values() if v["status"] == "partially_structured"),
        "evidence_only_tags": sum(1 for v in statuses.values() if v["status"] == "evidence_only"),
        "unmapped_tags": sum(1 for v in statuses.values() if v["status"] == "unmapped"),
        "tags": dict(sorted(statuses.items(), key=lambda pair: (-pair[1]["records_nonempty"], pair[0]))),
        "sample_size": min(sample_size, records), "sample_seed": seed,
    }
    if sample_size:
        from converter.wikidata.item_builder import WikidataItemBuilder
        builder = WikidataItemBuilder()
        errors: list[dict[str, str]] = []
        properties: Counter[str] = Counter()
        for raw in sample:
            try:
                item = builder.build_manuscript_item(prepare_record_for_pipeline(raw))
                properties.update(str(statement.property_id) for statement in item.statements)
            except Exception as exc:  # noqa: BLE001 - audit continues
                if len(errors) < 20:
                    errors.append({"control_number": str(raw.get("001") or ""), "error": f"{type(exc).__name__}: {exc}"[:240]})
        result["sample_build_errors"] = len(errors)
        result["sample_build_error_examples"] = errors
        result["sample_wikidata_properties"] = dict(properties)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--sample-build", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=9061)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--fail-on-unmapped", action="store_true")
    args = parser.parse_args()
    result = audit(args.input, max(0, args.sample_build), args.seed)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json:
        args.json.write_text(output + "\n", encoding="utf-8")
    print(output)
    if result["normalization_errors"] or result.get("sample_build_errors", 0):
        raise SystemExit(1)
    if args.fail_on_unmapped and result["unmapped_tags"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
