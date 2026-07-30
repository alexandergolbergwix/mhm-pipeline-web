"""Resolve ``__LOCAL:`` statement targets against the built item set.

``__LOCAL:<local_id>`` is the two-pass upload's internal reference (Rule W-30):
pass 1 creates the items, pass 2 replaces the placeholders with real QIDs. That
only works while every placeholder names an item the build actually produced.

Works are legitimately dropped after the referring statement was written — a
thin title (Rule W-98) or a failed source-evidence gate (Rule W-68 / W-121) —
which left 38 `P1574` claims across 31 manuscripts pointing at items that exist
nowhere. The judge reported them as unresolved, and pass 2 would leave a
placeholder string on a live item.

Contained-text links degrade to `P1574 → Q234460` ("text") keeping the catalog
title as a `P1932` qualifier, which is what WikiProject Manuscripts prescribes
for an unidentified text. Any other property loses the claim outright rather
than asserting a relation we cannot name (Rule W-138).
"""

from __future__ import annotations

import logging
from typing import Any

from converter.wikidata.item_models import WikidataItem, WikidataStatement

logger = logging.getLogger(__name__)

LOCAL_PREFIX = "__LOCAL:"
Q_UNKNOWN_TEXT = "Q234460"
P_EXEMPLAR_OF = "P1574"
P_TITLE = "P1932"

# Properties whose local target can degrade to "some unidentified text".
_DEGRADABLE_PIDS = frozenset({P_EXEMPLAR_OF})


def _local_target(value: Any) -> str:
    text = str(value or "")
    return text.removeprefix(LOCAL_PREFIX) if text.startswith(LOCAL_PREFIX) else ""


def _has_title_qualifier(statement: WikidataStatement) -> bool:
    return any(
        str(q.get("property") or q.get("property_id") or "") == P_TITLE
        for q in statement.qualifiers or []
        if isinstance(q, dict)
    )


def _degrade(statement: WikidataStatement, target_id: str) -> WikidataStatement:
    statement.value = Q_UNKNOWN_TEXT
    statement.value_type = "item"
    if not _has_title_qualifier(statement):
        title = target_id.split(":", 1)[-1].replace("_", " ").strip()
        if title:
            statement.qualifiers = [
                *(statement.qualifiers or []),
                {"property": P_TITLE, "value": title, "value_type": "monolingualtext"},
            ]
    return statement


def resolve_local_references(items: list[WikidataItem]) -> dict[str, int]:
    """Rewrite or drop every ``__LOCAL:`` target that no built item provides.

    Returns a count per action for the build summary — a silent rewrite would
    hide a projection bug (Rule W-110's "no silent caps" reasoning).
    """
    known = {str(item.local_id or "") for item in items if item.local_id}
    degraded = 0
    dropped = 0

    for item in items:
        kept: list[WikidataStatement] = []
        for statement in item.statements or []:
            target = _local_target(statement.value)
            if not target or target in known:
                kept.append(statement)
                continue
            pid = str(statement.property_id or "")
            if pid in _DEGRADABLE_PIDS:
                kept.append(_degrade(statement, target))
                degraded += 1
                continue
            dropped += 1
            logger.warning(
                "dropping %s on %s — local target %r was never built",
                pid, item.local_id, target,
            )
        item.statements = kept

        # Qualifiers may also carry a local target (e.g. a role qualifier).
        for statement in item.statements:
            qualifiers = []
            for qualifier in statement.qualifiers or []:
                if not isinstance(qualifier, dict):
                    qualifiers.append(qualifier)
                    continue
                target = _local_target(qualifier.get("value"))
                if target and target not in known:
                    dropped += 1
                    continue
                qualifiers.append(qualifier)
            statement.qualifiers = qualifiers

    if degraded or dropped:
        logger.info(
            "resolved local references: %d degraded to %s, %d dropped",
            degraded, Q_UNKNOWN_TEXT, dropped,
        )
    return {"degraded": degraded, "dropped": dropped}


def dangling_local_references(items: list[Any]) -> list[str]:
    """Every remaining unresolved ``__LOCAL:`` target — used by the build gate."""
    known = {str(getattr(item, "local_id", "") or "") for item in items}
    out: list[str] = []
    for item in items:
        for statement in getattr(item, "statements", []) or []:
            values = [getattr(statement, "value", None)]
            values.extend(
                q.get("value")
                for q in getattr(statement, "qualifiers", None) or []
                if isinstance(q, dict)
            )
            for value in values:
                target = _local_target(value)
                if target and target not in known:
                    out.append(
                        f"{getattr(item, 'local_id', '')}:"
                        f"{getattr(statement, 'property_id', '')} -> {target}",
                    )
    return out
