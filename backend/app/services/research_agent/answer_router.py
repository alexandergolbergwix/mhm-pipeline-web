"""Deterministic-first answering (P2): fact questions never need an LLM.

A production research agent answers count/status facts from the database and
sends the LLM only for synthesis. "How many entities we've added to
Wikidata" is one COUNT query — it must survive every model outage (and it
does not need a model to be exact).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.publication import (
    Publication,
    PublicationExecution,
    PublicationExecutionAction,
)
from app.models.run import Run as RunRow

_WIKIDATA_COUNT = re.compile(
    r"how many (entities|items|records)?.{0,40}"
    r"(added|created|uploaded|published|written).{0,20}wikidata",
    re.I | re.S,
)
_MS_COUNT = re.compile(r"how many manuscripts", re.I)
_STATUS = re.compile(r"(what|which) (was|were|is|are) (added|uploaded|published) to wikidata", re.I)


@dataclass(frozen=True)
class DeterministicAnswer:
    text: str
    kind: str


async def _wikidata_counts(
    db: AsyncSession, *, project_id: str, run_id: str | None = None
) -> tuple[int, int, int]:
    """(written, created, updated) across the project's publication executions."""
    query = (
        select(
            PublicationExecutionAction.action,
            PublicationExecutionAction.state,
        )
        .join(
            PublicationExecution,
            PublicationExecution.id == PublicationExecutionAction.execution_id,
        )
        .join(Publication, Publication.id == PublicationExecution.publication_id)
        .join(RunRow, RunRow.id == Publication.run_id)
        .where(RunRow.project_id == uuid.UUID(str(project_id)))
    )
    if run_id:
        query = query.where(Publication.run_id == uuid.UUID(str(run_id)))
    rows = (await db.execute(query)).all()
    written = sum(1 for action, state in rows if state == "succeeded")
    created = sum(1 for action, state in rows if state == "succeeded" and action == "create")
    updated = sum(1 for action, state in rows if state == "succeeded" and action == "update")
    return written, created, updated


async def deterministic_answer(
    question: str,
    db: AsyncSession,
    *,
    project_id: str,
    run_id: str | None = None,
) -> DeterministicAnswer | None:
    """Answer a fact question from the DB, or None to route to the agent."""
    text = (question or "").strip()
    if not text:
        return None
    if _WIKIDATA_COUNT.search(text):
        written, created, updated = await _wikidata_counts(
            db, project_id=project_id, run_id=run_id,
        )
        return DeterministicAnswer(
            kind="wikidata_counts",
            text=(
                f"So far this run has written {written} items to Wikidata: "
                f"{created} created and {updated} updated. "
                "(Counted from the publication execution records — exact, "
                "no AI estimate.)"
            ),
        )
    if _STATUS.search(text):
        written, created, updated = await _wikidata_counts(
            db, project_id=project_id, run_id=run_id,
        )
        return DeterministicAnswer(
            kind="wikidata_counts",
            text=(
                f"{created} items were created and {updated} updated on "
                f"Wikidata in this run ({written} writes in total)."
            ),
        )
    return None
