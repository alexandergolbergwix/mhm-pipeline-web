"""Read the HMO Wikibase schema bootstrap report from disk.

The file is the eval-agent boundary for HMO Wikibase Studio schema
verification (Phase 3 of dev-docs/hmo-wikibase-studio-plan.md in the
web repo): each row is one ontology class/property the bootstrap
either created, skipped (already mapped), or would create/skip in a
dry run. Only ``"created"``/``"would_create"`` rows are worth judging —
a ``"skipped"`` row was already verified in an earlier bootstrap pass
and a ``"failed"`` row has no live entity to evaluate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_JUDGEABLE_STATUSES = frozenset({"created", "would_create"})


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
