"""Run journal writes (P1 observability, Rule W-274).

Every stage of a remote agent run lands here so a stalled or crashed run is
diagnosable from the debug endpoint instead of from guesswork.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.research_agent_run import ResearchAgentRunJournal


async def journal_stage(
    db: AsyncSession,
    *,
    run_id: str,
    project_id: str,
    thread_id: str | None,
    stage: str,
    ok: bool = True,
    code: str | None = None,
    detail: str = "",
) -> None:
    """Append one stage entry; terminal codes stamp the row."""
    try:
        row = (
            await db.execute(
                select(ResearchAgentRunJournal).where(
                    ResearchAgentRunJournal.run_id == str(run_id)[:64]
                )
            )
        ).scalar_one_or_none()
        entry = {
            "stage": stage,
            "at": datetime.now(timezone.utc).isoformat(),
            "ok": bool(ok),
            "code": code or "",
            "detail": (detail or "")[:400],
        }
        if row is None:
            row = ResearchAgentRunJournal(
                run_id=str(run_id)[:64],
                project_id=uuid.UUID(str(project_id)),
                thread_id=uuid.UUID(str(thread_id)) if thread_id else None,
                stages=[entry],
                terminal_code=code if stage.startswith("terminal") else None,
            )
            db.add(row)
        else:
            row.stages = [*row.stages, entry][-200:]
            if stage.startswith("terminal"):
                row.terminal_code = code
        await db.commit()
    except Exception:  # noqa: BLE001 — observability must never break the run
        await db.rollback()
