"""Smoke tests for ``app.pipeline.agent_actions``.

The action registry is the only place a new AI-verify prompt can be
introduced — the UI never accepts free text. These tests pin the
contract so a regression that drops an action or breaks the goal
template surfaces immediately.
"""

from __future__ import annotations

import pytest

from app.pipeline import agent_actions


class TestRegistryShape:
    """The registry exposes exactly the three documented actions, each
    with the fields ``to_dict()`` promises."""

    def test_three_actions_registered(self) -> None:
        actions = agent_actions.list_actions()
        assert len(actions) == 3

    def test_registry_includes_canonical_ids(self) -> None:
        ids = {a.id for a in agent_actions.list_actions()}
        assert ids == {"audit_match", "find_duplicates", "birth_death_check"}

    @pytest.mark.parametrize(
        "action_id", ["audit_match", "find_duplicates", "birth_death_check"],
    )
    def test_get_action_returns_each(self, action_id: str) -> None:
        action = agent_actions.get_action(action_id)
        assert action is not None
        assert action.id == action_id

    def test_get_action_unknown_returns_none(self) -> None:
        assert agent_actions.get_action("nope") is None

    def test_to_dict_carries_every_documented_field(self) -> None:
        d = agent_actions.to_dict(agent_actions.get_action("audit_match"))
        assert set(d) == {
            "id", "label", "description", "scope_kinds", "evaluators",
            "min_candidates",
        }


class TestScopeFiltering:
    """list_actions(scope_kind=...) filters to actions that apply for
    the given entry-point scope kind."""

    def test_single_excludes_find_duplicates(self) -> None:
        ids = {a.id for a in agent_actions.list_actions(scope_kind="single")}
        assert "find_duplicates" not in ids
        assert "audit_match" in ids
        assert "birth_death_check" in ids

    def test_selection_includes_find_duplicates(self) -> None:
        ids = {a.id for a in agent_actions.list_actions(scope_kind="selection")}
        assert "find_duplicates" in ids

    def test_all_includes_find_duplicates(self) -> None:
        ids = {a.id for a in agent_actions.list_actions(scope_kind="all")}
        assert "find_duplicates" in ids

    def test_unfiltered_returns_full_set(self) -> None:
        assert len(agent_actions.list_actions(scope_kind=None)) == 3


class TestRenderGoal:
    """render_goal interpolates n_candidates + pluralisation."""

    def test_singular_no_es_suffix(self) -> None:
        action = agent_actions.get_action("audit_match")
        text = agent_actions.render_goal(action, n_candidates=1)
        assert "Audit 1 authority match " in text
        # Singular: "match" (no -es). Stricter than the count alone.
        assert "match " in text
        assert "matches" not in text

    def test_plural_adds_es_suffix(self) -> None:
        action = agent_actions.get_action("audit_match")
        text = agent_actions.render_goal(action, n_candidates=12)
        assert "Audit 12 authority matches" in text

    def test_zero_renders_as_plural(self) -> None:
        # n_candidates=0 is a curator-error case (the router rejects
        # empty scopes), but the templating must still resolve
        # rather than raise KeyError.
        action = agent_actions.get_action("audit_match")
        text = agent_actions.render_goal(action, n_candidates=0)
        assert "0 authority matches" in text


class TestMinCandidates:
    """find_duplicates requires >=2 candidates because a duplicate by
    definition needs a pair to compare."""

    def test_find_duplicates_needs_two(self) -> None:
        assert agent_actions.get_action("find_duplicates").min_candidates == 2

    def test_audit_match_works_on_single(self) -> None:
        assert agent_actions.get_action("audit_match").min_candidates == 1

    def test_birth_death_check_works_on_single(self) -> None:
        assert agent_actions.get_action("birth_death_check").min_candidates == 1


class TestEvaluatorWiring:
    """Every action routes through the ``authority`` evaluator in the
    sibling eval-agent project. The router's subprocess CLI consumes
    this via ``--evaluators X,Y``.
    """

    @pytest.mark.parametrize(
        "action_id", ["audit_match", "find_duplicates", "birth_death_check"],
    )
    def test_each_action_routes_to_authority_evaluator(self, action_id: str) -> None:
        action = agent_actions.get_action(action_id)
        assert "authority" in action.evaluators
