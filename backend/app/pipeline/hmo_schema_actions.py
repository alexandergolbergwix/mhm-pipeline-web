"""Prefab AI-agent actions for HMO Wikibase schema bootstrap review."""

from __future__ import annotations

from app.pipeline.agent_actions import AgentAction, ScopeKind, render_goal


_REGISTRY: dict[str, AgentAction] = {
    "audit_schema_entry": AgentAction(
        id="audit_schema_entry",
        label="Audit schema entry",
        description=(
            "Ask the AI judge whether each ontology class/property mapping "
            "is semantically correct for the HMO Wikibase: label, datatype, "
            "and Wikibase id must align with the Hebrew Manuscripts ontology."
        ),
        scope_kinds=("single", "selection", "all"),
        goal_template=(
            "Audit {n_candidates} HMO Wikibase schema bootstrap entr{plural}. "
            "For each class or property, judge whether its label, datatype, "
            "and planned Wikibase id are correct for the HMO ontology."
        ),
        evaluators=("hmo_wikibase_schema",),
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
