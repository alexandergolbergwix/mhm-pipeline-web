"""Backfill the entity_event log from existing read-model rows.

After this script runs, every MARC record / ExtractionApproval /
AuthorityMatch / WikidataItemOverride has at least one event
(op='create', rev_no=1) recording its current state. Re-running is
a no-op because each entity is skipped if it already has events.

Invoke (from inside ``backend/``)::

    python -m scripts.backfill_versioning

Or on Heroku::

    heroku run --app mhm-pipeline-web -- \\
        bash -lc "cd backend && python -m scripts.backfill_versioning"
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


def _ensure_app_on_syspath() -> None:
    """Make ``app.*`` importable when invoked as a top-level script."""
    backend_dir = Path(__file__).resolve().parent.parent
    backend_str = str(backend_dir)
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)


_ensure_app_on_syspath()

from sqlalchemy import exists, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.models.event import (  # noqa: E402
    ENTITY_TYPE_AUTHORITY_MATCH,
    ENTITY_TYPE_EXTRACTION_ENTITY,
    ENTITY_TYPE_MARC_RECORD,
    ENTITY_TYPE_WIKIDATA_OVERRIDE,
    OP_CREATE,
    ProjectEvent,
)
from app.models.extraction_approval import ExtractionApproval  # noqa: E402
from app.models.item_override import WikidataItemOverride  # noqa: E402
from app.models.project import (  # noqa: E402
    PROJECT_ROLE_OWNER,
    Membership,
    Project,
)
from app.models.run import AuthorityMatch, Run, RunRecord  # noqa: E402
from app.versioning import apply_event  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill_versioning")

BATCH_SIZE = 500


async def _has_event(
    db: AsyncSession, entity_type: str, entity_id: str,
) -> bool:
    stmt = select(
        exists().where(
            ProjectEvent.entity_type == entity_type,
            ProjectEvent.entity_id == entity_id,
        )
    )
    return bool((await db.execute(stmt)).scalar())


async def _resolve_project_owner(
    db: AsyncSession, project_id: uuid.UUID,
) -> uuid.UUID | None:
    """Return an actor_id to attribute backfilled events to.

    Prefers an owner membership; falls back to the project's
    ``owner_id`` column.
    """

    stmt = (
        select(Membership.user_id)
        .where(
            Membership.project_id == project_id,
            Membership.role == PROJECT_ROLE_OWNER,
        )
        .limit(1)
    )
    owner = (await db.execute(stmt)).scalar_one_or_none()
    if owner is not None:
        return owner
    stmt2 = select(Project.owner_id).where(Project.id == project_id)
    return (await db.execute(stmt2)).scalar_one_or_none()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def backfill_marc_records(db: AsyncSession) -> int:
    """One OP_CREATE per (run_id, control_number) RunRecord."""

    stmt = (
        select(
            RunRecord.run_id,
            RunRecord.control_number,
            RunRecord.marc,
            Run.project_id,
            Run.created_by,
        )
        .join(Run, Run.id == RunRecord.run_id)
    )
    result = await db.execute(stmt)
    created = 0
    owner_cache: dict[uuid.UUID, uuid.UUID | None] = {}

    for run_id, control_number, marc, project_id, created_by in result.all():
        entity_id = f"{run_id}:{control_number}"
        if await _has_event(db, ENTITY_TYPE_MARC_RECORD, entity_id):
            continue
        if project_id not in owner_cache:
            owner_cache[project_id] = (
                await _resolve_project_owner(db, project_id)
            ) or created_by
        actor_id = owner_cache[project_id] or created_by

        state: dict[str, Any] = {
            "run_id": str(run_id),
            "control_number": control_number,
            "marc": marc,
        }
        await apply_event(
            db,
            project_id=project_id,
            entity_type=ENTITY_TYPE_MARC_RECORD,
            entity_id=entity_id,
            op=OP_CREATE,
            new_state=state,
            actor_id=actor_id,
            message="backfill",
        )
        created += 1
        if created % BATCH_SIZE == 0:
            await db.commit()
            logger.info("marc_record: committed %s rows", created)

    await db.commit()
    return created


async def backfill_extraction_approvals(db: AsyncSession) -> int:
    """One OP_CREATE per ExtractionApproval row."""

    stmt = (
        select(ExtractionApproval, Run.project_id, Run.created_by)
        .join(Run, Run.id == ExtractionApproval.run_id)
    )
    result = await db.execute(stmt)
    created = 0
    owner_cache: dict[uuid.UUID, uuid.UUID | None] = {}

    for approval, project_id, created_by in result.all():
        entity_id = str(approval.id)
        if await _has_event(db, ENTITY_TYPE_EXTRACTION_ENTITY, entity_id):
            continue
        if project_id not in owner_cache:
            owner_cache[project_id] = (
                await _resolve_project_owner(db, project_id)
            ) or created_by
        actor_id = (
            approval.approved_by
            or owner_cache[project_id]
            or created_by
        )

        state: dict[str, Any] = {
            "approved": approval.approved,
            "override_type": approval.override_type,
            "override_role": approval.override_role,
            "ai_verdict": approval.ai_verdict,
        }
        await apply_event(
            db,
            project_id=project_id,
            entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
            entity_id=entity_id,
            op=OP_CREATE,
            new_state=state,
            actor_id=actor_id,
            message="backfill",
        )
        created += 1
        if created % BATCH_SIZE == 0:
            await db.commit()
            logger.info("extraction_entity: committed %s rows", created)

    await db.commit()
    return created


async def backfill_authority_matches(db: AsyncSession) -> int:
    """One OP_CREATE per AuthorityMatch row."""

    stmt = (
        select(AuthorityMatch, Run.project_id, Run.created_by)
        .join(Run, Run.id == AuthorityMatch.run_id)
    )
    result = await db.execute(stmt)
    created = 0
    owner_cache: dict[uuid.UUID, uuid.UUID | None] = {}

    for match, project_id, created_by in result.all():
        entity_id = str(match.id)
        if await _has_event(db, ENTITY_TYPE_AUTHORITY_MATCH, entity_id):
            continue
        if project_id not in owner_cache:
            owner_cache[project_id] = (
                await _resolve_project_owner(db, project_id)
            ) or created_by
        actor_id = (
            match.approved_by
            or owner_cache[project_id]
            or created_by
        )

        state: dict[str, Any] = {
            "entity_text": match.entity_text,
            "entity_kind": match.entity_kind,
            "role": match.role,
            "matched_name": match.matched_name,
            "mazal_id": match.mazal_id,
            "viaf_id": match.viaf_id,
            "wikidata_qid": match.wikidata_qid,
            "confidence": match.confidence,
            "source": match.source,
            "payload": match.payload,
            "approved": match.approved,
        }
        await apply_event(
            db,
            project_id=project_id,
            entity_type=ENTITY_TYPE_AUTHORITY_MATCH,
            entity_id=entity_id,
            op=OP_CREATE,
            new_state=state,
            actor_id=actor_id,
            message="backfill",
        )
        created += 1
        if created % BATCH_SIZE == 0:
            await db.commit()
            logger.info("authority_match: committed %s rows", created)

    await db.commit()
    return created


async def backfill_wikidata_overrides(db: AsyncSession) -> int:
    """One OP_CREATE per WikidataItemOverride row."""

    stmt = (
        select(WikidataItemOverride, Run.project_id, Run.created_by)
        .join(Run, Run.id == WikidataItemOverride.run_id)
    )
    result = await db.execute(stmt)
    created = 0
    owner_cache: dict[uuid.UUID, uuid.UUID | None] = {}

    for override, project_id, created_by in result.all():
        entity_id = str(override.id)
        if await _has_event(db, ENTITY_TYPE_WIKIDATA_OVERRIDE, entity_id):
            continue
        if project_id not in owner_cache:
            owner_cache[project_id] = (
                await _resolve_project_owner(db, project_id)
            ) or created_by
        actor_id = (
            override.updated_by
            or owner_cache[project_id]
            or created_by
        )

        state: dict[str, Any] = {
            "labels": override.labels,
            "descriptions": override.descriptions,
            "aliases": override.aliases,
            "add_statements": override.add_statements,
            "remove_statements": override.remove_statements,
            "statement_edits": override.statement_edits,
        }
        await apply_event(
            db,
            project_id=project_id,
            entity_type=ENTITY_TYPE_WIKIDATA_OVERRIDE,
            entity_id=entity_id,
            op=OP_CREATE,
            new_state=state,
            actor_id=actor_id,
            message="backfill",
        )
        created += 1
        if created % BATCH_SIZE == 0:
            await db.commit()
            logger.info("wikidata_override: committed %s rows", created)

    await db.commit()
    return created


async def _main() -> None:
    async with session_scope() as db:
        c1 = await backfill_marc_records(db)
        c2 = await backfill_extraction_approvals(db)
        c3 = await backfill_authority_matches(db)
        c4 = await backfill_wikidata_overrides(db)
    print(
        f"[backfill] marc_record={c1} extraction_entity={c2} "
        f"authority_match={c3} wikidata_override={c4}"
    )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
