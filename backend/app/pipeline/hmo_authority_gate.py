"""Fail-closed authority checks required before HMO item creation."""
from __future__ import annotations
from collections import defaultdict
from typing import Any, Iterable

def validate_authority_rows(rows: Iterable[Any]) -> dict[str, Any]:
    owners: dict[tuple[str, str], set[str]] = defaultdict(set)
    invalid: list[dict[str, str]] = []
    for row in rows:
        if not bool(getattr(row, "approved", False)):
            continue
        text = str(getattr(row, "entity_text", "")).strip()
        for kind, value in (("wikidata", getattr(row, "wikidata_qid", "")), ("mazal", getattr(row, "mazal_id", "")), ("viaf", getattr(row, "viaf_id", ""))):
            identifier = str(value or "").strip()
            if not identifier:
                continue
            if kind == "viaf" and identifier.startswith("987007"):
                invalid.append({"entity_text": text, "kind": kind, "identifier": identifier, "reason": "NLI/Mazal identifier stored as VIAF"})
                continue
            owners[(kind, identifier)].add(text)
    conflicts = [{"kind": kind, "identifier": identifier, "owners": sorted(values)} for (kind, identifier), values in owners.items() if len(values) > 1]
    return {"conflicts": conflicts, "invalid": invalid, "ready": not conflicts and not invalid}
