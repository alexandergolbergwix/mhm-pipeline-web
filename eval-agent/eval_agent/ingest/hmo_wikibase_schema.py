"""Read the HMO Wikibase schema bootstrap report from disk.

The file is the eval-agent boundary for HMO Wikibase Studio schema
verification (Phase 3 of dev-docs/hmo-wikibase-studio-plan.md in the
web repo): each row is one ontology class/property the bootstrap
either created, skipped (already mapped), would create in a dry run,
or failed to write. All four statuses are judged — skipped rows are
the normal case once the schema is fully mapped and need auditing too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_JUDGEABLE_STATUSES = frozenset({"created", "would_create", "skipped", "failed"})


def load(path: Path) -> list[dict[str, Any]]:
    """Read a ``{dry_run, created, skipped, failed, entries: [...]}`` report."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw.get("entries") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        return []
    return [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("status") in _JUDGEABLE_STATUSES
    ]


def local_id(entry: dict[str, Any]) -> str:
    uri = str(entry.get("ontology_uri") or "")
    kind = str(entry.get("entity_kind") or "entity")
    return f"{kind}::{uri}" if uri else f"{kind}::{entry.get('label', '')}"
