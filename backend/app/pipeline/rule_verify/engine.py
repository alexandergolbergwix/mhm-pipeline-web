"""Rule engine: run a rule catalog over a scope of entities.

CPU rules run in chunks with cooperative yields (the 18k-entity lesson,
Rule W-245). API rules run only when the context allows external I/O —
in the sharded Modal path each container runs the CPU rules and ONE
container runs the API pass, so the Action API stays a single throttle
domain (Rule W-139).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.pipeline.rule_verify.base import (
    RULE_STATES,
    STATE_ERROR,
    STATE_FAIL,
    STATE_NOT_RELEVANT,
    STATE_PASS,
    Rule,
    RuleResult,
    worst_state,
)
from app.pipeline.rule_verify.context import RuleContext, context_for_item, run_rule_safely

logger = logging.getLogger(__name__)

ProgressCb = Callable[[int, int], None]

# Chunk size for cooperative yields over a large scope (Rule W-245).
CHUNK_SIZE = 250


def _empty_tally() -> dict[str, int]:
    return {state: 0 for state in RULE_STATES}


class RuleEngine:
    """Runs rules over entities; collects per-entity RuleResults."""

    def __init__(self, rules: list[Rule], ctx: RuleContext) -> None:
        self.rules = rules
        self.ctx = ctx

    def run_entity(self, entity: dict[str, Any]) -> list[RuleResult]:
        view = context_for_item(self.ctx, entity)
        out: list[RuleResult] = []
        for rule in self.rules:
            if rule.uses_api:
                continue
            if not rule.applies_to(entity, view):
                from app.pipeline.rule_verify.base import not_relevant

                out.append(not_relevant(rule.id, "rule does not apply to this entity"))
                continue
            out.append(run_rule_safely(rule, entity, view))
        self.ctx.results_so_far[str(entity.get("local_id") or "")] = {
            r.rule_id: r for r in out
        }
        return out

    def run_entity_api(self, entity: dict[str, Any]) -> list[RuleResult]:
        """The API-backed rules for one entity (requires ctx.api_enabled)."""
        view = context_for_item(self.ctx, entity)
        out: list[RuleResult] = []
        for rule in self.rules:
            if not rule.uses_api:
                continue
            if not rule.applies_to(entity, view):
                from app.pipeline.rule_verify.base import not_relevant

                out.append(not_relevant(rule.id, "rule does not apply to this entity"))
                continue
            out.append(run_rule_safely(rule, entity, view))
        stored = self.ctx.results_so_far.setdefault(
            str(entity.get("local_id") or ""), {},
        )
        for r in out:
            stored[r.rule_id] = r
        return out

    def run_scope(
        self,
        items: list[dict[str, Any]],
        *,
        on_progress: ProgressCb | None = None,
        should_cancel: Callable[[], bool] | None = None,
        include_api: bool = False,
    ) -> dict[str, list[RuleResult]]:
        """Run every rule over every entity. Returns local_id → results."""
        # API-backed rules ALWAYS run when include_api is requested: with
        # no fetcher / api_enabled they produce an explicit ``error``
        # result ("did not execute") instead of silently passing — the
        # abstain contract, fail closed.
        run_api = include_api and include_api_rules(self.rules)
        results: dict[str, list[RuleResult]] = {}
        total = len(items)
        done = 0
        for start in range(0, total, CHUNK_SIZE):
            if should_cancel is not None and should_cancel():
                break
            chunk = items[start : start + CHUNK_SIZE]
            for entity in chunk:
                key = entity_key(entity)
                cpu_results = self.run_entity(entity)
                if run_api:
                    results[key] = cpu_results + self.run_entity_api(entity)
                else:
                    results[key] = cpu_results
            done += len(chunk)
            if on_progress is not None:
                on_progress(done, total)
        return results


def entity_key(entity: dict[str, Any]) -> str:
    return str(entity.get("local_id") or "")


def include_api_rules(rules: list[Rule]) -> bool:
    return any(r.uses_api for r in rules)


def summarise(results: dict[str, list[RuleResult]]) -> dict[str, Any]:
    """Per-rule tallies + per-entity rollups for the summary view."""
    per_rule: dict[str, dict[str, int]] = {}
    per_entity: dict[str, dict[str, Any]] = {}
    for local_id, rule_results in results.items():
        states = [r.state for r in rule_results]
        failing = [r.rule_id for r in rule_results if r.state == STATE_FAIL]
        errors = [r.rule_id for r in rule_results if r.state == STATE_ERROR]
        passed = [r.rule_id for r in rule_results if r.state == STATE_PASS]
        per_entity[local_id] = {
            "overall": worst_state(states),
            "failing_rules": failing,
            "error_rules": errors,
            "pass_count": len(passed),
            "not_relevant_count": sum(1 for s in states if s == STATE_NOT_RELEVANT),
            "checked": len(rule_results),
        }
        for r in rule_results:
            tally = per_rule.setdefault(r.rule_id, _empty_tally())
            tally[r.state] = tally.get(r.state, 0) + 1
    return {"per_rule": per_rule, "per_entity": per_entity}
