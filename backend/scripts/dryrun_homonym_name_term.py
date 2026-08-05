"""How many authority match decisions would the name term flip?

Read-only. Re-scores the matches a run already made and reports what WOULD change.
Nothing is written to Postgres, to Wikidata, or to any cache.

`homonym_scoring` had no name-similarity term at all, and a lone candidate was
accepted with zero checks — the branch that matched the wrong Gabbai scribe
(Rule W-166). Adding a name term changes *matching*, which feeds person-link
evidence, date suppression and the W-155 person drop, so the blast radius is
measured before it is trusted.

    cd backend && .venv/bin/python -m scripts.dryrun_homonym_name_term \
        --export "/path/to/run-…-wikidata-studio-items.json"
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app.pipeline.homonym_scoring import pick_mazal_candidate


@contextlib.contextmanager
def _name_term_forced_on():
    """Score with the name term on regardless of the environment default."""
    previous = os.environ.get("AUTHORITY_HOMONYM_NAME_TERM")
    os.environ["AUTHORITY_HOMONYM_NAME_TERM"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("AUTHORITY_HOMONYM_NAME_TERM", None)
        else:
            os.environ["AUTHORITY_HOMONYM_NAME_TERM"] = previous


def _candidate_from_match(match: dict[str, Any]) -> dict[str, Any]:
    payload = match.get("payload") if isinstance(match.get("payload"), dict) else {}
    return {
        "mazal_id": match.get("mazal_id") or payload.get("mazal_id"),
        "dates": match.get("dates") or payload.get("dates"),
        "main_marc_tag": match.get("main_marc_tag") or payload.get("main_marc_tag") or "100",
        "preferred_name_heb": (
            match.get("preferred_name_heb") or payload.get("preferred_name_heb")
        ),
        "preferred_name_lat": (
            match.get("preferred_name_lat") or payload.get("preferred_name_lat")
        ),
        "_fuzzy": bool(match.get("_fuzzy") or payload.get("_fuzzy")),
    }


_CONTRIB_RE = re.compile(r'"name":\s*"(?P<name>.*?)",\s*"role":\s*"(?P<role>.*?)",')


def _marc_headings_by_role(item: dict[str, Any]) -> dict[str, list[str]]:
    """MARC contributor/author headings keyed by casefolded role.

    The export does not store the heading beside the authority row, but the same
    contributor rows the linker read are in the item's own MARC slice.
    """
    marc = (item.get("verify_evidence") or {}).get("marc") or {}
    out: dict[str, list[str]] = {}
    for field in ("contributors", "authors"):
        blob = marc.get(field)
        if not isinstance(blob, str):
            continue
        for chunk in blob.split(" | "):
            match = _CONTRIB_RE.search(chunk)
            if not match:
                continue
            name = match.group("name").replace('\\"', '"').strip(' "')
            role = match.group("role").replace('\\"', "").strip().casefold()
            if name:
                out.setdefault(role, []).append(name)
    return out


def _authority_rows(items: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """(marc heading, authority row) for every person match the export carries."""
    out: list[tuple[str, dict[str, Any]]] = []
    for item in items:
        if item.get("entity_type") != "person":
            continue
        headings = _marc_headings_by_role(item)
        for row in item.get("authority_evidence") or []:
            if not isinstance(row, dict):
                continue
            if not (row.get("mazal_id") or row.get("preferred_name_heb")):
                continue
            # STRICT pairing only. Falling back to "any heading on this record"
            # pairs an institution's 710 with a person's authority row and reports
            # a flip that is purely an artifact of the pairing.
            role = str(row.get("role") or "").strip().casefold()
            candidates = [
                name for r, names in headings.items()
                if role and (r == role or role in r or r in role)
                for name in names
            ]
            if len(candidates) == 1:
                out.append((candidates[0], row))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument(
        "--i-know-the-export-pairing-is-unreliable",
        action="store_true",
        help="print the export-based numbers anyway, as a smoke test of the term",
    )
    args = parser.parse_args(argv)

    if not getattr(args, "i_know_the_export_pairing_is_unreliable", False):
        print(
            "REFUSING to report a number from an export.\n\n"
            "The export does not record WHICH MARC heading each authority row was\n"
            "matched against — only the row's own preferred_name_heb. Any pairing\n"
            "reconstructed from it is a guess, and on run 48ba6c13 it pairs an\n"
            "institution's 710 with a person's row and an author with a scribe,\n"
            "reporting 35 of 37 decisions as flipped when that is an artifact.\n\n"
            "Measure this against live authority data instead, then enable with\n"
            "AUTHORITY_HOMONYM_NAME_TERM=1. Pass\n"
            "--i-know-the-export-pairing-is-unreliable to see the export numbers\n"
            "as a smoke test of the scoring code only.",
        )
        return 2

    payload = json.loads(args.export.read_text(encoding="utf-8"))
    rows = _authority_rows(payload.get("items") or [])
    if not rows:
        print(
            "No person authority rows carried a MARC heading in this export, so the\n"
            "name term cannot be re-scored from it. Re-run against a run id once the\n"
            "authority tables are reachable.",
        )
        return 0

    outcomes: Counter[str] = Counter()
    flipped: list[str] = []
    for heading, row in rows:
        candidate = _candidate_from_match(row)
        before = pick_mazal_candidate([candidate], marc_dates=row.get("marc_dates"))
        with _name_term_forced_on():
            after = pick_mazal_candidate(
                [candidate], marc_name=heading, marc_dates=row.get("marc_dates"),
            )
        key = f"{before.reason} -> {after.reason}"
        outcomes[key] += 1
        if bool(before.winner) != bool(after.winner):
            flipped.append(
                f"  {heading!r} vs {candidate.get('preferred_name_heb')!r} "
                f"({before.reason} -> {after.reason})",
            )

    print(f"re-scored {len(rows)} authority row(s)\n")
    for key, count in outcomes.most_common():
        print(f"  {count:5d}  {key}")
    print(f"\ndecisions that would flip: {len(flipped)}")
    for line in flipped[:40]:
        print(line)
    if len(flipped) > 40:
        print(f"  … and {len(flipped) - 40} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
