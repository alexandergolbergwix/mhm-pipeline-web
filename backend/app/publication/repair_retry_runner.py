"""Resume exact repairs only after their durable retry dates."""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime

from app.pipeline.wikidata_gila_execution import RepairGateway, SourceCheck
from app.publication.historical_repairs import advance_repair_publication
from app.publication.repository import PublicationRepository
from app.publication.types import PublicationSummary


@dataclass(frozen=True)
class RepairRetryWait:
    publication_id: str
    execution_id: str
    next_attempt_at: datetime
    seconds: float


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
) -> PublicationSummary:
    """Keep the same reviewed plan; stop on ambiguity or a finite time limit."""
    if not math.isfinite(max_wait_seconds) or max_wait_seconds < 0:
        raise ValueError("The repair wait limit must be finite and non-negative.")
    plan = deepcopy(plan)
    now = clock or (lambda: datetime.now(UTC))
    deadline = monotonic() + max_wait_seconds
    summary = await advance_repair_publication(
        plan,
        gateway,
        source_check,
        repository,
        actor_id=actor_id,
        run_id=run_id,
        clock=now,
    )
    while (
        max_wait_seconds > 0
        and summary.execution_status == "paused"
        and summary.execution_id
        and summary.execution_pre_send_retryable_count > 0
        and not summary.execution_outcome_unknown_count
        and not summary.execution_blocked_count
        and not summary.plan_blocked_count
    ):
        retry_dates = []
        for proposal in plan["proposals"]:
            intent = await repository.latest_write_intent(
                summary.execution_id,
                f"repair:{proposal['qid']}",
            )
            if intent is not None and intent.state == "pre_send_retryable":
                if intent.next_attempt_at is None or intent.next_attempt_at.tzinfo is None:
                    return summary
                retry_dates.append(intent.next_attempt_at)
        if len(retry_dates) != summary.execution_pre_send_retryable_count:
            return summary
        next_attempt = min(retry_dates)
        if next_attempt <= now():
            return summary
        # Release the SQL transaction and its connection before the wait.
        await repository.checkpoint()
        while True:
            remaining = deadline - monotonic()
            delay = max(0.0, (next_attempt - now()).total_seconds())
            if remaining <= 0 or delay > remaining:
                return summary
            if delay <= 0:
                break
            seconds = min(delay, 30.0, remaining)
            if on_wait is not None:
                on_wait(
                    RepairRetryWait(
                        summary.publication_id,
                        summary.execution_id,
                        next_attempt,
                        seconds,
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
        )
    return summary
