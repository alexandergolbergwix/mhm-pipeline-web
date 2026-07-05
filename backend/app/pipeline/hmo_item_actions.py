"""Prefab AI-agent actions for HMO Wikibase item review."""

from __future__ import annotations

from app.pipeline.agent_actions import AgentAction, ScopeKind, render_goal


_REGISTRY: dict[str, AgentAction] = {
    "audit_hmo_wikibase_item": AgentAction(
        id="audit_hmo_wikibase_item",
        label="Audit HMO Wikibase item",
        description=(
            "Ask the AI judge whether each resolved HMO Wikibase item is "
            "upload-ready: labels, descriptions, class, and claims must "
            "match the MARC record and upstream authority/NER evidence."
        ),
        scope_kinds=("single", "selection", "all"),
        goal_template=(
            "Audit {n_candidates} HMO Wikibase Studio item{plural}. "
            "For each item, judge whether its labels, descriptions, class, "
            "and claims are supported by the MARC record and upstream evidence."
        ),
        evaluators=("hmo_wikibase_item",),
        min_candidates=1,
        rate_limit_rpm=60,
    ),
    "autofix_hmo_wikibase_item": AgentAction(
        id="autofix_hmo_wikibase_item",
        label="Autofix from live Wikibase",
        description=(
            "For items that already have a live Wikibase QID, compare the "
            "live entity against the built projection and propose high-confidence "
            "fixes the curator can apply in one click."
        ),
        scope_kinds=("single", "selection", "all"),
        goal_template=(
            "Propose autofixes for {n_candidates} HMO Wikibase item{plural} "
            "that already map to a live Wikibase entity."
        ),
        evaluators=("hmo_wikibase_item_autofix",),
        min_candidates=1,
        rate_limit_rpm=30,
    ),
}


def list_actions(scope_kind: ScopeKind | None = None) -> list[AgentAction]:
    out = list(_REGISTRY.values())
    if scope_kind is not None:
        out = [a for a in out if scope_kind in a.scope_kinds]
    return out


def get_action(action_id: str) -> AgentAction | None:
    return _REGISTRY.get(action_id)


def to_dict(action: AgentAction) -> dict:
    return {
        "id": action.id,
        "label": action.label,
        "description": action.description,
        "scope_kinds": list(action.scope_kinds),
        "evaluators": list(action.evaluators),
        "min_candidates": action.min_candidates,
    }


__all__ = [
    "AgentAction",
    "ScopeKind",
    "get_action",
    "list_actions",
    "render_goal",
    "to_dict",
]
