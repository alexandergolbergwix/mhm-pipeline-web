"""Shared context handed to every rule: items, MARC, and API evidence."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.pipeline.rule_verify.base import RuleResult

logger = logging.getLogger(__name__)

# Injected network hook — tests pass a stub; production uses the real
# throttled Action API / Wikibase clients. Kept behind a callable so no
# rule ever opens a socket directly.
Fetcher = Any


@dataclass
class RuleContext:
    """Per-run context. Built once per scope; read-only for rules.

    ``marc_index`` powers the grounding rules. ``fetcher`` is the network
    seam for API rules (Wikidata Action API / live Wikibase); ``None``
    means API rules must report ``error`` (fail closed, never pass).
    ``in_run_labels`` supports the within-run duplicate rule.
    """

    run_id: str
    marc_index: dict[str, dict[str, Any]] = field(default_factory=dict)
    fetcher: Fetcher | None = None
    api_enabled: bool = False
    wikibase_endpoint: str = ""
    # Set by the engine before each entity's rules run.
    current_entity: dict[str, Any] = field(default_factory=dict)
    # local_id -> rule_id -> RuleResult, filled progressively so an API rule
    # can read a CPU rule's answer (e.g. reuse duplicate candidates).
    results_so_far: dict[str, dict[str, RuleResult]] = field(default_factory=dict)
    # Per-job counters shared across rules (probe budgets, progress ticks).
    counters: dict[str, int] = field(default_factory=dict)
    on_progress: Any = None
    # (class_qid, normalised label, control number) → local_ids with >1
    # member — precomputed per scope for the within-run duplicate rule.
    in_run_dup_index: dict[tuple[str, str, str], list[str]] = field(default_factory=dict)


def context_for_item(ctx: RuleContext, item: dict[str, Any]) -> RuleContext:
    """A shallow view of the shared context scoped to one entity."""
    return RuleContext(
        run_id=ctx.run_id,
        marc_index=ctx.marc_index,
        fetcher=ctx.fetcher,
        api_enabled=ctx.api_enabled,
        wikibase_endpoint=ctx.wikibase_endpoint,
        current_entity=item,
        results_so_far=ctx.results_so_far,
        counters=ctx.counters,
        on_progress=ctx.on_progress,
        in_run_dup_index=ctx.in_run_dup_index,
    )


def run_rule_safely(rule: Any, entity: dict[str, Any], ctx: RuleContext) -> RuleResult:
    """Run one rule, converting an unexpected exception into ``error``."""
    from app.pipeline.rule_verify.base import error as err_result

    try:
        return rule.run(entity, ctx)
    except Exception as exc:  # noqa: BLE001 — a broken rule must not kill the run
        logger.warning("rule %s raised on %s: %s", rule.id, entity.get("local_id"), exc)
        return err_result(rule.id, f"rule crashed: {type(exc).__name__}: {exc}"[:300])
