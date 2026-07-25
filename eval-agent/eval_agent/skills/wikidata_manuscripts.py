"""WikiProject Manuscripts skill pack for Studio AI verify.

Loads ``config/skills/wikidata_manuscripts/skill.json`` and builds a
compact, entity-aware context block for Wikidata Studio and HMO Wikibase
item judges. The full wiki pages are NOT dumped into prompts — only the
curated slices (Rule W-104).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal

Channel = Literal["wikidata", "hmo"]

_SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "skills"
    / "wikidata_manuscripts"
    / "skill.json"
)

_HMO_MANUSCRIPT_TYPES = frozenset({
    "Manuscript",
    "Codicological_Unit",
    "Paleographical_Unit",
    "F4_Manifestation_Singleton",
    "manuscript",
})
_HMO_PERSON_TYPES = frozenset({"E21_Person", "person"})
_HMO_ORG_TYPES = frozenset({"E74_Group", "organization"})
_HMO_WORK_TYPES = frozenset({
    "F1_Work",
    "F2_Expression",
    "work",
    "expression",
})
_HMO_PLACE_TYPES = frozenset({"E53_Place", "place"})
_HMO_EVENT_TYPES = frozenset({
    "E12_Production",
    "E52_Time-Span",
    "F27_Work_Creation",
    "TransmissionWitness",
    "TextTradition",
})
_HMO_STRUCTURAL_TYPES = frozenset({
    "CatalogStep",
    "EvidenceStep",
    "EvidenceChain",
    "Evidence",
    "PhilologicalView",
    "BibliographicParadigm",
    "PhilologicalParadigm",
    "ViewType",
    "ParadigmBridge",
})


@lru_cache(maxsize=1)
def load_skill() -> dict[str, Any]:
    data = json.loads(_SKILL_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("wikidata_manuscripts skill.json must be an object")
    return data


def skill_version() -> str:
    return str(load_skill().get("version") or "")


def _norm_pid(raw: Any) -> str | None:
    text = str(raw or "").strip().upper()
    if text.startswith("P") and text[1:].isdigit():
        return text
    return None


def collect_claim_pids(payload: dict[str, Any]) -> list[str]:
    """Extract property ids from Wikidata ``statements`` or HMO ``claims``."""
    seen: list[str] = []
    found: set[str] = set()
    rows = payload.get("statements") or payload.get("claims") or []
    if not isinstance(rows, list):
        return seen
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = _norm_pid(row.get("property") or row.get("property_id"))
        if pid and pid not in found:
            found.add(pid)
            seen.append(pid)
    return seen


def _entity_keys(
    *,
    channel: Channel,
    entity_type: str,
    semantic_type: str = "",
) -> list[str]:
    et = (entity_type or "").strip()
    st = (semantic_type or "").strip().lower()
    keys: list[str] = []

    if channel == "wikidata":
        low = et.lower()
        if low in {"manuscript", "item"} or st in {"printed_facsimile", "manuscript"}:
            if low == "manuscript" or st:
                keys.append("manuscript")
        if low == "person":
            keys.append("person")
        if low == "work":
            keys.append("work")
        if low == "place":
            keys.append("place")
        if not keys and low:
            # Unknown Studio type — still give manuscript-carrier rules as
            # the safest WPM default when statements look manuscript-like.
            keys.append("manuscript")
        return keys

    if et in _HMO_STRUCTURAL_TYPES:
        return ["hmo_structural"]
    if et in _HMO_EVENT_TYPES:
        return ["hmo_event"]
    if et in _HMO_MANUSCRIPT_TYPES:
        return ["manuscript"]
    if et in _HMO_PERSON_TYPES:
        return ["person"]
    if et in _HMO_ORG_TYPES:
        return ["person"]  # org/person role hygiene lives in the person slice
    if et in _HMO_WORK_TYPES:
        return ["work"]
    if et in _HMO_PLACE_TYPES:
        return ["place"]
    return ["hmo_structural"]


def skill_context_for(
    *,
    channel: Channel,
    entity_type: str,
    semantic_type: str = "",
    claim_pids: Iterable[str] | None = None,
    max_chars: int = 4500,
) -> str:
    """Compact WPM skill block for one evaluated item."""
    skill = load_skill()
    lines: list[str] = [
        "════════════════════════════════════════",
        f"SKILL: {skill.get('title')} (v{skill.get('version')})",
        f"Channel: {channel}",
        f"Entity: {entity_type or '(unknown)'}"
        + (f" / semantic={semantic_type}" if semantic_type else ""),
        f"Goal: {skill.get('goal')}",
        "",
        "Core WikiProject Manuscripts invariants (always apply):",
    ]
    for rule in skill.get("always") or []:
        lines.append(f"  • {rule}")

    guide = (skill.get("context_guide") or {}).get(channel) or []
    if guide:
        lines.append("")
        lines.append("What to put into context for THIS evaluation:")
        for tip in guide:
            lines.append(f"  • {tip}")

    slices = skill.get("entity_slices") or {}
    for key in _entity_keys(
        channel=channel,
        entity_type=entity_type,
        semantic_type=semantic_type,
    ):
        rules = slices.get(key) or []
        if not rules:
            continue
        lines.append("")
        lines.append(f"Entity slice [{key}]:")
        for rule in rules:
            lines.append(f"  • {rule}")

    triggers = skill.get("claim_triggers") or {}
    pids = [_norm_pid(p) for p in (claim_pids or [])]
    pids = [p for p in pids if p]
    matched = [p for p in pids if p in triggers]
    if matched:
        lines.append("")
        lines.append("Claim-triggered WPM checks (present on this item):")
        for pid in matched:
            lines.append(f"  • {pid}: {triggers[pid]}")

    if channel == "hmo":
        checklist = skill.get("hmo_to_wikidata_checklist") or []
        if checklist:
            lines.append("")
            lines.append("HMO Wikibase → public Wikidata projection checklist:")
            for row in checklist:
                lines.append(f"  • {row}")

    lines.append("════════════════════════════════════════")
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[: max_chars - 20].rstrip() + "\n… [skill truncated]\n"
    return text


def skill_context_from_payload(
    *,
    channel: Channel,
    payload: dict[str, Any],
) -> str:
    return skill_context_for(
        channel=channel,
        entity_type=str(payload.get("entity_type") or ""),
        semantic_type=str(payload.get("semantic_type") or ""),
        claim_pids=collect_claim_pids(payload),
    )
