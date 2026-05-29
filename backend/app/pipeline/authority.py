"""Authority matcher — pluggable, sync-only for the MVP.

The desktop pipeline carries five real adapters (Mazal, VIAF, Wikidata,
KIMA, ALA-LC). The web port ships only the *interface* + a deterministic
heuristic placeholder so the run lifecycle, persistence, and review UI
all work end-to-end. Drop a real adapter in by:

1. Implementing :class:`AuthorityMatcher` for that source.
2. Replacing the default in :func:`get_default_matcher` below.

The desktop ``converter/authority/mazal_matcher.py`` is the canonical
reference; its query is "match this name against the Mazal SQLite index,
return up to N candidates with sources/dates". A swap looks like::

    class MazalMatcher(AuthorityMatcher):
        def __init__(self, db_path: pathlib.Path) -> None: ...
        async def match(self, entity, marc_record): ...
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Candidate:
    """One authority candidate returned for an entity."""

    matched_name: str
    confidence: str = "low"     # high | medium | low
    source: str = "heuristic"   # mazal | viaf | wikidata | heuristic | …
    mazal_id: str = ""
    viaf_id: str = ""
    wikidata_qid: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class AuthorityMatcher(ABC):
    """Implement this to wire a new authority source."""

    @abstractmethod
    async def match(
        self, entity: dict[str, str], marc_record: dict[str, Any],
    ) -> list[Candidate]:
        ...


# ── Heuristic placeholder ────────────────────────────────────────────────


class HeuristicMatcher(AuthorityMatcher):
    """Returns deterministic placeholder candidates for an entity.

    Replace with the real Mazal/VIAF/Wikidata adapters when ready. The
    placeholder lets us flesh out the review UI, history, and collab
    layers without blocking on the heavy adapter port.
    """

    async def match(
        self, entity: dict[str, str], marc_record: dict[str, Any],
    ) -> list[Candidate]:
        text = entity.get("text", "").strip()
        if not text:
            return []

        # Stable ids derived from the name so repeated runs are
        # idempotent for diff comparisons.
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
        confidence = (
            "high" if len(text) > 18 else "medium" if len(text) > 8 else "low"
        )
        return [
            Candidate(
                matched_name=text,
                confidence=confidence,
                source="heuristic",
                mazal_id=f"H-{h.upper()}",
                payload={
                    "note": "Placeholder match — replace HeuristicMatcher in app.pipeline.authority.",
                    "marc_field": entity.get("field", ""),
                    "entity_role": entity.get("role", ""),
                },
            ),
        ]


def get_default_matcher() -> AuthorityMatcher:
    """Used by :mod:`app.pipeline.run` to resolve authorities. Override
    at boot time to swap in a real adapter."""
    return HeuristicMatcher()
