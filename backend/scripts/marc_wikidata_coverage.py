"""MARC → Wikidata coverage harness (WP9).

Measures how much of a MARC corpus's mapped fields actually reach Wikidata,
prints a per-field table with the plan's target thresholds, and exits non-zero
when coverage regresses below a recorded baseline.

Three measurement sources, one table:

``--projected``
    Build items with ``WikidataItemBuilder`` over the TSV and measure the
    statements the *code* emits. This is how a work package's lift is priced
    before anything is uploaded.
``--claims PATH``
    Read a claims snapshot (``--write-claims`` produces one). Used by the CI
    test so it never touches the network.
``--live``
    Join real claims from ``query.wikidata.org`` by P3959.

Usage::

    cd backend && .venv/bin/python -m scripts.marc_wikidata_coverage \
        ../pipeline/data/tsvs/17th_century_samples.tsv --projected

    # against a recorded snapshot, gating on regression
    .venv/bin/python -m scripts.marc_wikidata_coverage corpus.tsv \
        --claims claims.json --baseline baseline.json

    # refresh the snapshot used by CI
    .venv/bin/python -m scripts.marc_wikidata_coverage corpus.tsv \
        --projected --write-claims tests/unit/fixtures/marc_coverage_claims.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from converter.wikidata.marc_field_map import (
    MEASURED_TARGETS,
    PROPERTY_TARGETS,
    MarcFieldTarget,
)

_CONTROL_HEADERS = ("_control_number", "control_number", "controlnumber", "001", "id")
_TAG_RE = re.compile(r"^(\d{3})\$([a-z0-9])$")

# Work-layer fields are measured on the item linked by P1574, not on the
# manuscript itself: the WikiProject Manuscripts model puts authorship on the
# work (plan WP1, Rule W-69).
WORK_LAYER_FIELDS = frozenset({"100$a", "100$d", "700$a", "245$c"})


# ── Loading ──────────────────────────────────────────────────────────────


def _strip_quotes(value: object) -> str:
    return str(value or "").strip().strip('"').strip()


def load_corpus(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Read the TSV as raw ``<tag>$<sub>`` rows plus the column order."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        columns = list(reader.fieldnames or [])
        rows = [{k: (v or "") for k, v in row.items()} for row in reader]
    return rows, columns


def control_number(row: dict[str, str]) -> str:
    for key in _CONTROL_HEADERS:
        if _strip_quotes(row.get(key)):
            return _strip_quotes(row[key])
    return ""


# ── Claims access ────────────────────────────────────────────────────────


class ClaimsIndex:
    """Claims keyed by control number, with work claims reachable by P1574."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @classmethod
    def from_json(cls, path: Path) -> ClaimsIndex:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def to_json(self) -> dict[str, Any]:
        return self._data

    def entry(self, control: str) -> dict[str, Any]:
        return self._data.get(control) or {}

    def claims_for(self, control: str) -> dict[str, list[str]]:
        return dict(self.entry(control).get("claims") or {})

    def effective_pids(self, control: str, field_tag: str) -> set[str]:
        """The PIDs a covered record must carry for ``field_tag``.

        Work-layer fields resolve through P1574: a record is covered when any
        work it exemplifies carries the property. Everything else reads the
        manuscript item directly.
        """
        if field_tag not in WORK_LAYER_FIELDS:
            return set(self.claims_for(control))
        merged: set[str] = set()
        for work_claims in entry_works(self.entry(control)):
            merged.update(work_claims)
        return merged


LOCAL_PREFIX = "__LOCAL:"


def work_key(value: str) -> str:
    """The key a work is filed under for a P1574 reference.

    A projected work reference is ``__LOCAL:<local_id>`` while the work object
    carries the bare ``<local_id>``; a live reference is a QID. Both must land
    on one key or the work-layer join silently matches nothing.
    """
    text = str(value or "").strip()
    return text[len(LOCAL_PREFIX):] if text.startswith(LOCAL_PREFIX) else text


def entry_works(entry: dict[str, Any]) -> list[dict[str, list[str]]]:
    """Work-item claims referenced by a corpus entry's P1574 values."""
    claims = entry.get("claims") or {}
    works_by_id = entry.get("works") or {}
    out: list[dict[str, list[str]]] = []
    for work_id in claims.get("P1574") or []:
        key = work_key(work_id)
        if key in works_by_id:
            out.append(dict(works_by_id[key]))
    return out


def claims_from_projected(
    rows: list[dict[str, str]],
    prepared: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build items locally and snapshot the statements they carry.

    Works are rebuilt per record so the snapshot never depends on cross-record
    dedup. Each manuscript entry links the work claims it reaches through
    P1574, which is how work-layer authorship is measured (plan WP1).
    """
    from converter.wikidata.item_builder import WikidataItemBuilder  # noqa: PLC0415

    builder = WikidataItemBuilder()
    out: dict[str, Any] = {}

    for raw, record in zip(rows, prepared, strict=False):
        control = control_number(raw)
        if not control:
            continue
        item = builder.build_manuscript_item(record)
        claims: dict[str, list[str]] = defaultdict(list)
        for statement in item.statements:
            claims[statement.property_id].append(str(statement.value))

        works: dict[str, dict[str, list[str]]] = {}
        for work in getattr(builder, "_work_items", {}).values():
            work_claims: dict[str, list[str]] = defaultdict(list)
            for statement in work.statements:
                work_claims[statement.property_id].append(str(statement.value))
            works[work_key(work.local_id)] = dict(work_claims)

        linked = {
            work_key(work_id): works[work_key(work_id)]
            for work_id in claims.get("P1574") or []
            if work_key(work_id) in works
        }
        out[control] = {"claims": dict(claims), "works": linked}
    return out


def claims_from_live(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Join live Wikidata claims by P3959 via WDQS."""
    import urllib.parse  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    controls = [c for c in (control_number(row) for row in rows) if c]
    endpoint = "https://query.wikidata.org/sparql"
    out: dict[str, Any] = {}

    def run(query: str) -> list[dict[str, Any]]:
        url = endpoint + "?" + urllib.parse.urlencode(
            {"query": query, "format": "json"}
        )
        # S310: the scheme is fixed — ``endpoint`` above is a hardcoded
        # https WDQS URL, never a caller-supplied string.
        request = urllib.request.Request(  # noqa: S310
            url,
            headers={
                "User-Agent": (
                    "MHMPipeline/1.0 (coverage harness; alexandergo@wix.com)"
                )
            },
        )
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
        return payload["results"]["bindings"]

    chunk = 200
    for start in range(0, len(controls), chunk):
        block = controls[start : start + chunk]
        values = " ".join(f'"{c}"' for c in block)
        for binding in run(
            "SELECT ?item ?cid ?p ?work WHERE { "
            f"VALUES ?cid {{ {values} }} "
            "?item wdt:P3959 ?cid . "
            "?item ?p ?work . "
            "FILTER(STRSTARTS(STR(?p), 'http://www.wikidata.org/prop/direct/')) "
            "}"
        ):
            cid = binding["cid"]["value"]
            pid = binding["p"]["value"].rsplit("/", 1)[-1]
            target = binding["work"]["value"]
            entry = out.setdefault(cid, {"claims": defaultdict(list), "works": {}})
            entry["claims"][pid].append(target)

    for entry in out.values():
        entry["claims"] = dict(entry["claims"])
    return out


# ── Measurement ──────────────────────────────────────────────────────────


def measure(
    rows: list[dict[str, str]],
    index: ClaimsIndex,
    targets: tuple[MarcFieldTarget, ...] = MEASURED_TARGETS,
) -> dict[str, Any]:
    """Per-field coverage and per-property presence across the corpus."""
    total = len(rows)
    fields: list[dict[str, Any]] = []
    property_presence: dict[str, int] = defaultdict(int)
    matched_items = 0

    for row in rows:
        control = control_number(row)
        if not control or not index.claims_for(control):
            continue
        matched_items += 1
        entry = index.entry(control)
        # Count each PID at most once per record. A work is shared by many
        # manuscripts, so incrementing per work statement inflates the share
        # past 100% (P1476 read 167% before this).
        record_pids: set[str] = set()
        for pid, values in (entry.get("claims") or {}).items():
            if values:
                record_pids.add(pid)
        for work_claims in entry_works(entry):
            for pid, values in work_claims.items():
                if values:
                    record_pids.add(pid)
        for pid in record_pids:
            property_presence[pid] += 1

    for target in targets:
        with_field = 0
        covered = 0
        for row in rows:
            if not _strip_quotes(row.get(target.tag)):
                continue
            with_field += 1
            control = control_number(row)
            if not control:
                continue
            pids = index.effective_pids(control, target.tag)
            if set(target.pids) & pids:
                covered += 1
        coverage = (covered / with_field) if with_field else None
        fields.append(
            {
                "tag": target.tag,
                "layer": target.layer,
                "kind": target.kind,
                "pids": list(target.pids),
                "with_field": with_field,
                "covered": covered,
                "coverage": coverage,
                "target": target.target_coverage,
                "meets_target": (
                    True
                    if target.target_coverage is None or coverage is None
                    else coverage >= target.target_coverage - 1e-9
                ),
            }
        )

    properties: list[dict[str, Any]] = []
    for pid, floor in sorted(PROPERTY_TARGETS.items()):
        count = property_presence.get(pid, 0)
        share = (count / total) if total else 0.0
        properties.append(
            {
                "pid": pid,
                "items": count,
                "share": share,
                "target": floor,
                "meets_target": share >= floor - 1e-9,
            }
        )

    return {
        "records": total,
        "matched_items": matched_items,
        "fields": fields,
        "properties": properties,
    }


# ── Reporting ────────────────────────────────────────────────────────────


def _pct(value: float | None) -> str:
    return "  —  " if value is None else f"{value * 100:5.1f}%"


def render(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(
        f"MARC → Wikidata coverage — {result['records']} records, "
        f"{result['matched_items']} with claims"
    )
    lines.append("")
    header = (
        f"{'field':<10} {'layer':<12} {'pids':<26} "
        f"{'with':>6} {'cov':>6} {'coverage':>9} {'target':>8}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for row in result["fields"]:
        mark = "" if row["meets_target"] else "  << below target"
        target_text = "—" if row["target"] is None else f"{row['target'] * 100:.0f}%"
        lines.append(
            f"{row['tag']:<10} {row['layer']:<12} {','.join(row['pids']) or '—':<26} "
            f"{row['with_field']:>6} {row['covered']:>6} "
            f"{_pct(row['coverage']):>9} {target_text:>8}{mark}"
        )
    lines.append("")
    lines.append("Property share across the corpus")
    lines.append("-" * 72)
    for row in result["properties"]:
        mark = "" if row["meets_target"] else "  << below target"
        lines.append(
            f"  {row['pid']:<8} {row['items']:>5}  {row['share'] * 100:5.1f}%"
            f"  target {row['target'] * 100:.0f}%{mark}"
        )
    return "\n".join(lines)


def regressions(result: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Coverage rows that fell below a previously recorded baseline."""
    out: list[str] = []
    base_fields = {row["tag"]: row for row in baseline.get("fields", [])}
    for row in result["fields"]:
        base = base_fields.get(row["tag"])
        if not base:
            continue
        if base.get("coverage") is None or row["coverage"] is None:
            continue
        if row["coverage"] < base["coverage"] - 1e-9:
            out.append(
                f"{row['tag']}: {base['coverage'] * 100:.1f}% → {row['coverage'] * 100:.1f}%"
            )
    base_props = {row["pid"]: row for row in baseline.get("properties", [])}
    for row in result["properties"]:
        base = base_props.get(row["pid"])
        if not base:
            continue
        if row["share"] < base["share"] - 1e-9:
            out.append(
                f"{row['pid']}: {base['share'] * 100:.1f}% → {row['share'] * 100:.1f}%"
            )
    return out


# ── CLI ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--projected", action="store_true")
    source.add_argument("--claims", type=Path)
    source.add_argument("--live", action="store_true")
    parser.add_argument("--write-claims", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument(
        "--fail-below-target",
        action="store_true",
        help="exit 2 when any measured field or property is below its target",
    )
    args = parser.parse_args(argv)

    rows, _columns = load_corpus(args.corpus)

    if args.projected:
        from app.pipeline.marc_ingest import prepare_record_for_pipeline  # noqa: PLC0415

        prepared = [prepare_record_for_pipeline(row) for row in rows]
        data = claims_from_projected(rows, prepared)
    elif args.claims:
        data = json.loads(args.claims.read_text(encoding="utf-8"))
    else:
        data = claims_from_live(rows)

    if args.write_claims:
        args.write_claims.parent.mkdir(parents=True, exist_ok=True)
        args.write_claims.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    result = measure(rows, ClaimsIndex(data))
    print(render(result))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if args.write_baseline:
        args.write_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.write_baseline.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        drops = regressions(result, baseline)
        if drops:
            print("\nREGRESSION — coverage fell below baseline:")
            for line in drops:
                print(f"  {line}")
            return 1

    if args.fail_below_target:
        misses = [
            f"{row['tag']} ({row['coverage'] * 100:.1f}% < {row['target'] * 100:.0f}%)"
            for row in result["fields"]
            if not row["meets_target"] and row["coverage"] is not None
        ]
        misses += [
            f"{row['pid']} ({row['share'] * 100:.1f}% < {row['target'] * 100:.0f}%)"
            for row in result["properties"]
            if not row["meets_target"]
        ]
        if misses:
            print("\nBELOW TARGET:")
            for line in misses:
                print(f"  {line}")
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
