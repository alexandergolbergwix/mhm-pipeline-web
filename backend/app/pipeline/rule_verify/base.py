"""Rule protocol, result shape, and states for the rule-based verifier."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as _dc_field
from typing import Any, Literal

# bandit S105 false positive: these are state names, not credentials.
STATE_PASS = "pass"  # noqa: S105
STATE_FAIL = "fail"  # noqa: S105
STATE_NOT_RELEVANT = "not_relevant"  # noqa: S105
STATE_ERROR = "error"  # noqa: S105

RULE_STATES = (STATE_PASS, STATE_FAIL, STATE_NOT_RELEVANT, STATE_ERROR)
PUBLIC_STATES = (STATE_PASS, STATE_FAIL, STATE_NOT_RELEVANT, STATE_ERROR)

# Worst-first ordering for entity rollups. ``error`` ranks between fail and
# pass in UI severity, but is reported separately so an API outage never
# reads as a failing item.
STATE_SEVERITY: dict[str, int] = {
    STATE_FAIL: 3,
    STATE_ERROR: 2,
    STATE_PASS: 1,
    STATE_NOT_RELEVANT: 0,
}

RULE_VERDICT_SCHEMA = "rule_verdict_v1"


@dataclass(frozen=True)
class RuleResult:
    """One rule's outcome for one entity."""

    rule_id: str
    state: str
    field: str = ""
    message: str = ""
    evidence: dict[str, Any] = _dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "state": self.state,
            "field": self.field,
            "message": self.message,
            "evidence": self.evidence,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> RuleResult:
        state = str(data.get("state") or "")
        if state not in RULE_STATES:
            state = STATE_ERROR
        return RuleResult(
            rule_id=str(data.get("rule_id") or ""),
            state=state,
            field=str(data.get("field") or ""),
            message=str(data.get("message") or ""),
            evidence=dict(data.get("evidence") or {}),
        )


def fail(
    rule_id: str, field: str, message: str, evidence: dict[str, Any] | None = None,
) -> RuleResult:
    return RuleResult(rule_id, STATE_FAIL, field, message, evidence or {})


def warn_as_pass(
    rule_id: str, message: str = "ok", evidence: dict[str, Any] | None = None,
) -> RuleResult:
    """A positive, informative result that is not a failure."""
    return RuleResult(rule_id, STATE_PASS, "", message, evidence or {})


def not_relevant(rule_id: str, reason: str = "") -> RuleResult:
    return RuleResult(rule_id, STATE_NOT_RELEVANT, "", reason, {})


def error(rule_id: str, message: str, evidence: dict[str, Any] | None = None) -> RuleResult:
    return RuleResult(rule_id, STATE_ERROR, "", message, evidence or {})


@dataclass(frozen=True)
class Rule:
    """One deterministic check. ``applies_to`` false → ``not_relevant``.

    ``run`` must never raise: implement :meth:`safe_run` semantics via the
    engine's wrapper. ``applies_to_api`` marks rules that need network
    evidence (Wikidata / Wikibase); the engine runs them only when the
    caller allows external I/O.
    """

    id: str
    title: str
    description: str
    channel: Literal["hmo", "wikidata"] = "hmo"
    uses_api: bool = False

    def applies_to(self, entity: dict[str, Any], ctx: Any) -> bool:  # noqa: ARG002
        return True

    def run(self, entity: dict[str, Any], ctx: Any) -> RuleResult:
        raise NotImplementedError


def worst_state(states: list[str]) -> str:
    """The worst of several rule states, for entity rollups."""
    if not states:
        return STATE_NOT_RELEVANT
    return max(states, key=lambda s: STATE_SEVERITY.get(s, 0))
