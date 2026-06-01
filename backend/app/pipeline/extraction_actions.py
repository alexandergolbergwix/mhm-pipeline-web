"""Prefab AI-agent actions for AI Extraction NER review.

Mirrors :mod:`app.pipeline.agent_actions` but the actions here target
NER + classifier candidates (``ExtractionApproval`` rows) rather than
authority matches. The eval-agent's NER evaluators (``person_ner``,
``provenance_ner``, ``contents_ner``, ``genre_classifier``) live in
the sibling ``eval-agent`` repo's ``evaluators/`` registry and have
been there since the agentic harness landed (CLAUDE.md Rule 57).
Adding a new action is a Python dict entry; the UI never accepts
free text.
"""

from __future__ import annotations

from app.pipeline.agent_actions import AgentAction, ScopeKind, render_goal


# ── The registry ──────────────────────────────────────────────────────


_REGISTRY: dict[str, AgentAction] = {
    "audit_ner_extraction": AgentAction(
        id="audit_ner_extraction",
        label="Audit NER entities with AI agent",
        description=(
            "Ask the AI judge to score each extracted NER entity "
            "(person, provenance, contents, genre) against the MARC "
            "context. Per-candidate verdicts populate the inline 'AI "
            "verdict' column on the entity table."
        ),
        scope_kinds=("single", "selection", "all"),
        goal_template=(
            "Audit {n_candidates} NER candidate{plural} extracted from this "
            "run. For each, judge whether the entity text + type + role "
            "are correctly extracted given the MARC fields the entity "
            "came from."
        ),
        evaluators=("person_ner", "provenance_ner",
                    "contents_ner", "genre_classifier"),
        min_candidates=1,
        rate_limit_rpm=60,
    ),

    "check_ner_genre": AgentAction(
        id="check_ner_genre",
        label="Genre-only check",
        description=(
            "Run only the genre classifier evaluator. Cheap on big "
            "corpora when you've already verified the person / "
            "provenance / contents NER and just want to spot-check the "
            "ML-inferred genres."
        ),
        scope_kinds=("selection", "all"),
        goal_template=(
            "Check the genre classifier output for {n_candidates} "
            "record{plural}. Flag genre predictions that don't match the "
            "manuscript's MARC 245 + 500 evidence."
        ),
        evaluators=("genre_classifier",),
        min_candidates=1,
        rate_limit_rpm=60,
    ),
}


def list_actions(scope_kind: ScopeKind | None = None) -> list[AgentAction]:
    """Return every NER-scoped action, optionally filtered by scope kind."""
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
