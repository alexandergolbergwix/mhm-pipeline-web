"""Audit every holder name the corpus attests against the one audited table.

Read-only. Nothing is written to Wikidata, Postgres, or any cache.

`unknown` must be empty. A name in that bucket is one nobody has verified, and
until Rule W-161 it silently became "Jerusalem, NLI" in the item's own label —
an invented owner over the record's attested one. `abstained` is fine: it means a
human looked and recorded why the name cannot be linked.

    cd backend && .venv/bin/python -m scripts.audit_holding_institutions \
        --export "/path/to/run-…-wikidata-studio-items.json"
    cd backend && .venv/bin/python -m scripts.audit_holding_institutions --run <run_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from converter.wikidata.holding_institutions import (
    STATUS_ABSTAINED,
    STATUS_PLACEHOLDER,
    STATUS_RESOLVED,
    STATUS_UNKNOWN,
    resolve_holder,
)

# The export flattens MARC contributors to a pipe-joined string of JSON-ish rows.
_CONTRIB_RE = re.compile(r'"name":\s*"(?P<name>.*?)",\s*"role":\s*"(?P<role>.*?)",')


def _current_owner_names(marc: dict[str, Any]) -> list[str]:
    names: list[str] = []
    contributors = marc.get("contributors")
    if isinstance(contributors, str):
        for chunk in contributors.split(" | "):
            match = _CONTRIB_RE.search(chunk)
            if not match:
                continue
            role = match.group("role").replace('\\"', "").casefold()
            if "current owner" in role:
                names.append(match.group("name").replace('\\"', '"'))
    elif isinstance(contributors, list):
        for row in contributors:
            if not isinstance(row, dict):
                continue
            if "current owner" in str(row.get("role") or "").casefold():
                names.append(str(row.get("name") or ""))
    holding = marc.get("holding_institution")
    if holding:
        names.append(str(holding))
    return names


def _holder_names_from_export(path: Path) -> Counter[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    names: Counter[str] = Counter()
    for item in payload.get("items") or []:
        if item.get("entity_type") != "manuscript":
            continue
        marc = (item.get("verify_evidence") or {}).get("marc") or {}
        found = _current_owner_names(marc)
        names[found[0] if found else ""] += 1
    return names


async def _holder_names_from_run(run_id: str) -> Counter[str]:
    from app.db import session_scope  # noqa: PLC0415
    from app.pipeline.wikidata_studio_data import load_run_marc_records  # noqa: PLC0415

    async with session_scope() as db:
        records = await load_run_marc_records(db, run_id)
    names: Counter[str] = Counter()
    for record in records:
        found = _current_owner_names(dict(record))
        names[found[0] if found else ""] += 1
    return names


def _report(names: Counter[str]) -> int:
    buckets: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for name, count in names.most_common():
        resolution = resolve_holder(name)
        buckets[resolution.status].append((
            resolution.name or "<none attested>", count, resolution.reason,
        ))

    for status, title in (
        (STATUS_RESOLVED, "resolved — verified QID + label"),
        (STATUS_ABSTAINED, "abstained — reviewed, cannot be linked"),
        (STATUS_PLACEHOLDER, "no holder attested"),
        (STATUS_UNKNOWN, "UNKNOWN — unaudited, must be fixed"),
    ):
        rows = buckets.get(status) or []
        total = sum(count for _n, count, _r in rows)
        print(f"\n{title}  ({len(rows)} names, {total} records)")
        for name, count, reason in rows:
            suffix = f"   [{reason}]" if reason and status != STATUS_RESOLVED else ""
            print(f"  {count:4d}  {name}{suffix}")

    unknown = buckets.get(STATUS_UNKNOWN) or []
    if unknown:
        print(
            f"\nFAILED: {len(unknown)} holder name(s) are not in the audited table.\n"
            "Verify each QID live (wbsearchentities + wbgetentities), then add an\n"
            "_INSTITUTIONS entry with the fetched label — or an "
            "ABSTAINED_INSTITUTIONS row with the reason.",
        )
        return 1
    print("\nOK — every attested holder name is resolved or explicitly abstained.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--export", type=Path, help="Studio items export JSON")
    source.add_argument("--run", help="run id (reads MARC from Postgres)")
    args = parser.parse_args(argv)

    names = (
        _holder_names_from_export(args.export)
        if args.export
        else asyncio.run(_holder_names_from_run(args.run))
    )
    return _report(names)


if __name__ == "__main__":
    sys.exit(main())
