"""Prefab AI-agent actions.

The curator picks an **action** from a fixed registry — never types a
prompt. Each action is a server-side template that knows:

* its label + description (what to show in the dropdown)
* which scope kinds it applies to (single candidate, selection, all)
* how to render the agent's goal sentence from the scope
* which eval-agent evaluator(s) to invoke
* whether it needs multiple candidates to be meaningful

The registry is the only place a new prompt can be introduced. The UI
never accepts free text — adding an action is a Python diff.

The eval-agent's per-candidate rubrics already exist in the sibling
project (``config/rubrics/*.md``). The action's job here is just
selecting which evaluator to run and how to title the work for the
curator's review log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ScopeKind = Literal["single", "selection", "all"]


@dataclass(frozen=True)
class AgentAction:
    """One prefab verification action."""

    id:          str
    label:       str
    description: str
    # Which entry points show this action.
    scope_kinds: tuple[ScopeKind, ...]
    # Goal sentence handed to the agent — rendered with {n_candidates}.
    goal_template: str
    # Which eval-agent evaluators to run when this action fires. The
    # agentic loop's per-candidate rubric is fixed by the evaluator;
    # this is what the existing ``eval-agent run --evaluators X,Y``
    # flag consumes.
    evaluators:  tuple[str, ...]
    # Minimum scope size for the action to be meaningful. find_duplicates
    # on a single row, for example, makes no sense.
    min_candidates: int = 1
    # Rate-limit override for this action's Gemini calls.
    rate_limit_rpm: int = 60


# ── The registry ──────────────────────────────────────────────────────


_REGISTRY: dict[str, AgentAction] = {
    "review_publication_blocked": AgentAction(
        id="review_publication_blocked", label="AI review blocked items",
        description="Compare proposed changes with existing Wikidata items before curator consent.",
        scope_kinds=("selection",),
        goal_template="Review {n_candidates} blocked Publication entities for identity and supported changes.",
        evaluators=("wikidata_publication_review",),
    ),
    "audit_match": AgentAction(
        id="audit_match",
        label="Audit this match",
        description=(
            "Does the AI judge agree with the assigned confidence + the "
            "matched authority record, given the MARC context?"
        ),
        scope_kinds=("single", "selection", "all"),
        goal_template=(
            "Audit {n_candidates} authority match{plural} from this run. "
            "For each, judge whether the matched authority record is the "
            "correct real-world entity for the MARC entity heading, given "
            "the manuscript's date, language, and contextual notes."
        ),
        evaluators=("authority",),
        min_candidates=1,
        rate_limit_rpm=60,
    ),

    "find_duplicates": AgentAction(
        id="find_duplicates",
        label="Find duplicates",
        description=(
            "Surface candidates likely pointing at the same real-world "
            "person but matched to different authority records."
        ),
        # Single-candidate doesn't make sense — duplicates need >= 2.
        scope_kinds=("selection", "all"),
        goal_template=(
            "Review {n_candidates} authority matches and flag any pairs "
            "that look like they describe the same real-world person but "
            "were resolved to different authority records (cross-cluster "
            "duplicates). Use the matched names, birth/death years, and "
            "MARC role to decide."
        ),
        evaluators=("authority",),
        min_candidates=2,
        rate_limit_rpm=60,
    ),

    "birth_death_check": AgentAction(
        id="birth_death_check",
        label="Birth/death sanity check",
        description=(
            "For matches where Authority Enrichment already resolved candidate years, "
            "is the life plausibly compatible with the MS year + role?"
        ),
        scope_kinds=("single", "selection", "all"),
        goal_template=(
            "For {n_candidates} authority match{plural}, judge whether the "
            "candidate's birth + death years are compatible with the "
            "manuscript's date AND the role the candidate plays (author "
            "must predate the MS; scribe can't post-date their own "
            "death; subjects can long predate)."
        ),
        evaluators=("authority",),
        min_candidates=1,
        rate_limit_rpm=60,
    ),
}


def list_actions(scope_kind: ScopeKind | None = None) -> list[AgentAction]:
    """Return every action, optionally filtered to those applicable for
    a given entry point's scope kind."""
    out = list(_REGISTRY.values())
    if scope_kind is not None:
        out = [a for a in out if scope_kind in a.scope_kinds]
    return out


def get_action(action_id: str) -> AgentAction | None:
    return _REGISTRY.get(action_id)


def render_goal(action: AgentAction, *, n_candidates: int) -> str:
    """Render the goal sentence for a scope of the given size."""
    plural = "es" if n_candidates != 1 else ""
    return action.goal_template.format(
        n_candidates=n_candidates, plural=plural,
    )


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
