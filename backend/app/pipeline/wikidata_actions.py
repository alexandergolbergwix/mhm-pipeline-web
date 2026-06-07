"""Prefab AI-agent actions for Wikidata Studio item review."""

from __future__ import annotations

from app.pipeline.agent_actions import AgentAction, ScopeKind, render_goal


_REGISTRY: dict[str, AgentAction] = {
    "audit_wikidata_item": AgentAction(
        id="audit_wikidata_item",
        label="Audit Wikidata item",
        description=(
            "Ask the AI judge whether each generated Wikidata item is "
            "upload-ready: labels, descriptions, entity type, existing QID, "
            "and statements must match the MARC and authority evidence."
        ),
        scope_kinds=("single", "selection", "all"),
        goal_template=(
            "Audit {n_candidates} generated Wikidata Studio item{plural}. "
            "For each item, judge whether its labels, descriptions, entity "
            "type, existing QID, and statements are supported by the MARC "
            "record and upstream authority/extraction evidence."
        ),
        evaluators=("wikidata_item",),
        min_candidates=1,
        rate_limit_rpm=60,
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
        "id":             action.id,
        "label":          action.label,
        "description":    action.description,
        "scope_kinds":    list(action.scope_kinds),
        "evaluators":     list(action.evaluators),
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
