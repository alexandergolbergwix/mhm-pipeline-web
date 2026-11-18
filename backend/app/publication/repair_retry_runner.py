"""Resume exact repairs only after their durable retry dates."""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.pipeline.wikidata_gila_execution import RepairGateway, SourceCheck
from app.pipeline.wikidata_retry_diagnostics import ApiRetryDiagnostics
from app.publication.historical_repairs import advance_repair_publication
from app.publication.reference_recovery import (
    LAG_PROBE_INTERVAL,
    LagProbeResult,
    LagProbeSchedule,
)
from app.publication.repository import PublicationRepository
from app.publication.types import PublicationSummary


@dataclass(frozen=True)
class RepairRetryWait:
    publication_id: str
    execution_id: str
    next_attempt_at: datetime
    seconds: float
    diagnostics: ApiRetryDiagnostics | None = None
    lag_probe: LagProbeResult | None = None
    next_probe_at: datetime | None = None


async def wait_for_repair_publication(
    plan,
    gateway: RepairGateway,
    source_check: SourceCheck,
    repository: PublicationRepository,
    *,
    actor_id: str,
    run_id: str,
    max_wait_seconds: float = 0,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    on_wait: Callable[[RepairRetryWait], None] | None = None,
    lag_probe: Callable[[], Awaitable[LagProbeResult]] | None = None,
    lag_probe_schedule: LagProbeSchedule | None = None,
) -> PublicationSummary:
    """Keep the same reviewed plan; stop on ambiguity or a finite time limit."""
    if not math.isfinite(max_wait_seconds) or max_wait_seconds < 0:
        raise ValueError("The repair wait limit must be finite and non-negative.")
    if plan.get("execution_mode") == "foreground_reference":
        raise ValueError("The automatic retry runner cannot accept a foreground plan.")
    plan = deepcopy(plan)
    now = clock or (lambda: datetime.now(UTC))
    deadline = monotonic() + max_wait_seconds
    if max_wait_seconds == 0:
        lag_probe = None
    summary = await advance_repair_publication(
        plan,
        gateway,
        source_check,
        repository,
        actor_id=actor_id,
        run_id=run_id,
        clock=now,
        probe_maxlag=lag_probe is not None,
        defer_existing_maxlag=lag_probe is not None,
    )
    schedule = lag_probe_schedule or LagProbeSchedule()
    while (
        max_wait_seconds > 0
        and summary.execution_status == "paused"
        and summary.execution_id
        and summary.execution_pre_send_retryable_count > 0
        and not summary.execution_outcome_unknown_count
        and not summary.execution_blocked_count
        and not summary.plan_blocked_count
    ):
        retry_intents = []
        for proposal in plan["proposals"]:
            intent = await repository.latest_write_intent(
                summary.execution_id,
                f"repair:{proposal['qid']}",
            )
            if intent is not None and intent.state == "pre_send_retryable":
                if intent.next_attempt_at is None or intent.next_attempt_at.tzinfo is None:
                    return summary
                retry_intents.append(intent)
        if len(retry_intents) != summary.execution_pre_send_retryable_count:
            return summary
        next_intent = min(retry_intents, key=lambda intent: intent.next_attempt_at)
        next_attempt = next_intent.next_attempt_at
        diagnostics = ApiRetryDiagnostics.from_detail(next_intent.detail)
        # Release the SQL transaction and its connection before the wait.
        await repository.checkpoint()
        probe = lag_probe if diagnostics and diagnostics.code == "maxlag" else None
        if probe is not None and diagnostics is not None:
            since_refusal = (now() - diagnostics.observed_at).total_seconds()
            schedule.next_monotonic = max(
                schedule.next_monotonic,
                monotonic() + max(5.0, diagnostics.retry_after_seconds or 0) - since_refusal,
            )
        # An earlier probe cannot cover a new refusal.
        last_probe = None
        while True:
            remaining = deadline - monotonic()
            delay = max(0.0, (next_attempt - now()).total_seconds())
            if remaining <= 0 or delay > remaining:
                return summary
            if probe is not None and monotonic() >= schedule.next_monotonic:
                last_probe = await probe()
                schedule.next_monotonic = monotonic() + max(
                    LAG_PROBE_INTERVAL, last_probe.retry_after
                )
                continue
            ready = probe is None or (last_probe is not None and last_probe.status == "ready")
            if delay <= 0 and ready:
                break
            probe_delay = (
                max(0.0, schedule.next_monotonic - monotonic()) if probe is not None else 0.0
            )
            seconds = min([30.0, remaining] + [d for d in (delay, probe_delay) if d > 0])
            if on_wait is not None:
                on_wait(
                    RepairRetryWait(
                        summary.publication_id,
                        summary.execution_id,
                        next_attempt,
                        seconds,
                        diagnostics,
                        last_probe,
                        now() + timedelta(seconds=probe_delay)
                        if probe is not None and probe_delay <= remaining else None,
                    )
                )
            await sleep(seconds)
        summary = await advance_repair_publication(
            plan,
            gateway,
            source_check,
            repository,
            actor_id=actor_id,
            run_id=run_id,
            clock=now,
            probe_maxlag=lag_probe is not None,
        )
    return summary
