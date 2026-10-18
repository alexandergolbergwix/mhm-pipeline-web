"""Deterministic-first answering (P2): fact questions never need an LLM.

Every number this module reports is (a) exact and (b) CITED: the answer
carries a markdown link to the real source (the publication audit / the
upload records), because an uncited number is not evidence (2026-10-18).
Counts distinguish DISTINCT ITEMS from WRITE OPERATIONS — the two were
conflated once and the chat showed "wrong data".
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

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
    evidence: list[dict[str, str]] = field(default_factory=list)


async def _wikidata_stats(db: AsyncSession, *, project_id: str) -> dict[str, object]:
    """Distinct items and write operations, plus the source links to cite."""
    project_uuid = uuid.UUID(str(project_id))
    rows = (
        await db.execute(
            select(
                PublicationExecutionAction.action,
                PublicationExecutionAction.state,
                PublicationExecutionAction.result_qid,
                Publication.id,
                Publication.run_id,
            )
            .join(
                PublicationExecution,
                PublicationExecution.id == PublicationExecutionAction.execution_id,
            )
            .join(Publication, Publication.id == PublicationExecution.publication_id)
            .join(RunRow, RunRow.id == Publication.run_id)
            .where(RunRow.project_id == project_uuid)
        )
    ).all()
    succeeded = [r for r in rows if r[1] == "succeeded"]
    write_ops = len(succeeded)
    distinct_items = len({r[2] for r in succeeded if r[2]})
    created_ops = sum(1 for r in succeeded if r[0] == "create")
    updated_ops = sum(1 for r in succeeded if r[0] == "update")
    latest = max(rows, key=lambda r: str(r[4])) if rows else None
    run_id = str(latest[4]) if latest else None
    publication_id = str(latest[3]) if latest else None
    return {
        "write_ops": write_ops,
        "distinct_items": distinct_items,
        "created_ops": created_ops,
        "updated_ops": updated_ops,
        "run_id": run_id,
        "publication_id": publication_id,
    }


def _evidence(stats: dict[str, object], web_base: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if stats["run_id"] and stats["publication_id"]:
        items.append({
            "label": "publication audit (every write, per item)",
            "url": (
                f"{web_base}/runs/{stats['run_id']}/wikidata-studio"
                f"/publications/{stats['publication_id']}/audit"
            ),
        })
        items.append({
            "label": "Wikidata Studio upload records",
            "url": f"{web_base}/runs/{stats['run_id']}/wikidata-studio",
        })
    return items


def _with_evidence(text: str, evidence: list[dict[str, str]]) -> str:
    if not evidence:
        return text
    sources = " · ".join(f"[{e['label']}]({e['url']})" for e in evidence)
    return f"{text}\n\nSources: {sources}"


async def deterministic_answer(
    question: str,
    db: AsyncSession,
    *,
    project_id: str,
    web_base: str = "https://mhm-pipeline.org",
) -> DeterministicAnswer | None:
    """Answer a fact question from the DB with citations, or None."""
    text = (question or "").strip()
    if not text:
        return None
    if _WIKIDATA_COUNT.search(text) or _STATUS.search(text):
        stats = await _wikidata_stats(db, project_id=project_id)
        evidence = _evidence(stats, web_base.rstrip("/"))
        body = (
            f"{stats['distinct_items']} distinct items are on Wikidata from "
            f"this project, produced by {stats['write_ops']} write operations "
            f"({stats['created_ops']} creates, {stats['updated_ops']} updates). "
            "Distinct items and write operations differ: one item may be "
            "created in one pass and updated in a later pass. "
            "(Counted live from the publication execution records — exact, "
            "no AI estimate.)"
        )
        return DeterministicAnswer(
            kind="wikidata_counts",
            text=_with_evidence(body, evidence),
            evidence=evidence,
        )
    return None
