"""Audit HMO exports for authority coverage and namespace collisions."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

QID_RE = re.compile(r"^Q\d+$")
WIKIDATA_URI_RE = re.compile(r"^https://www\.wikidata\.org/entity/(Q\d+)$")
VIAF_URI_RE = re.compile(r"^https://viaf\.org/viaf/(\d+)$")


def _claims(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [claim for claim in item.get("claims") or [] if isinstance(claim, dict)]


def _evidence(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in item.get("authority_evidence") or [] if isinstance(entry, dict)]


def audit(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("HMO export must contain an items array")

    by_qid: defaultdict[str, list[str]] = defaultdict(list)
    by_type: Counter[str] = Counter()
    authority_counts: Counter[str] = Counter()
    invalid: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        local_id = str(item.get("local_id") or "")
        source_uri = str(item.get("source_uri") or "")
        entity_type = str(item.get("entity_type") or "other")
        by_type[entity_type] += 1
        local_qid = str(item.get("wikibase_id") or "")
        if local_qid:
            by_qid[local_qid].append(source_uri)
            if not QID_RE.fullmatch(local_qid):
                invalid.append({"local_id": local_id, "issue": f"invalid local Wikibase id: {local_qid}"})
        for evidence in _evidence(item):
            kind = str(evidence.get("kind") or "").lower()
            if kind in {"wikidata", "viaf"}:
                authority_counts[kind] += 1
                if evidence.get("accepted") is False:
                    authority_counts[f"{kind}:withheld"] += 1

        for claim in _claims(item):
            value = claim.get("value")
            if not isinstance(value, str):
                continue
            wikidata = WIKIDATA_URI_RE.fullmatch(value)
            viaf = VIAF_URI_RE.fullmatch(value)
            if wikidata:
                authority_counts["wikidata"] += 1
            elif viaf:
                authority_counts["viaf"] += 1
            elif "wikidata.org/entity/" in value or "viaf.org/viaf/" in value:
                invalid.append({"local_id": local_id, "issue": f"malformed authority URI: {value}"})
        if entity_type in {"E53_Place", "E21_Person", "E74_Group"}:
            if any(WIKIDATA_URI_RE.fullmatch(str(c.get("value") or "")) for c in _claims(item)):
                authority_counts[f"{entity_type}:with_wikidata"] += 1
            if any(VIAF_URI_RE.fullmatch(str(c.get("value") or "")) for c in _claims(item)):
                authority_counts[f"{entity_type}:with_viaf"] += 1

    collisions = {
        qid: sources for qid, sources in by_qid.items()
        if len(set(sources)) > 1
    }
    return {
        "items": len(items),
        "entity_types": dict(by_type),
        "authority_counts": dict(authority_counts),
        "duplicate_local_wikibase_qids": collisions,
        "invalid_authority_values": invalid[:100],
        "invalid_authority_count": len(invalid),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("export", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    report = audit(args.export)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json:
        args.json.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
