"""Read-only repair inventory from the existing Studio read model."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin import require_admin
from app.auth.session import AuthContext
from app.db import get_session
from app.models.item_override import WikidataItemOverride
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.models.wikidata_studio_item_row import WikidataStudioItemRow
from app.routers.runs import _lookup_run_with_access

router = APIRouter(prefix="/runs", tags=["wikidata-studio"])


class RepairInventoryItem(BaseModel):
    local_id: str
    label: str
    existing_qid: str
    validation_codes: list[str]
    qid_edit_indices: list[str]
    approved: bool | None
    override_updated_at: datetime | None


class RepairInventoryPage(BaseModel):
    run_id: uuid.UUID
    build_id: uuid.UUID
    build_token: str
    built_at: datetime
    checked_at: datetime
    mode: Literal["read_only"] = "read_only"
    public_verification: Literal["not_checked"] = "not_checked"
    total_items: int
    items: list[RepairInventoryItem]
    next_cursor: str | None


def qid_edit_indices(edits: object) -> list[str]:
    """A QID edit needs review; its position does not prove its property."""
    if not isinstance(edits, dict):
        return []
    return [
        str(index)[:32]
        for index, patch in edits.items()
        if isinstance(patch, dict)
        and isinstance(patch.get("value"), str)
        and re.fullmatch(r"Q[1-9][0-9]*", patch["value"].strip())
    ][:50]


def validation_codes(issues: object) -> list[str]:
    if not isinstance(issues, list):
        return []
    return sorted(
        {
            issue["code"][:100]
            for issue in issues
            if isinstance(issue, dict) and isinstance(issue.get("code"), str)
        }
    )[:20]


@router.get("/{run_id}/wikidata-studio/repair-plan", response_model=RepairInventoryPage)
async def read_repair_plan(
    run_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
    cursor: Annotated[str, Query(max_length=256)] = "",
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    expected_build_token: Annotated[str | None, Query(pattern=r"^[a-f0-9]{64}$")] = None,
) -> RepairInventoryPage:
    await _lookup_run_with_access(db, run_id, auth, write=False)
    cache = (
        await db.execute(
            select(
                WikidataStudioCache.id,
                WikidataStudioCache.built_at,
                WikidataStudioCache.input_fingerprint,
            ).where(
                WikidataStudioCache.run_id == run_id,
                WikidataStudioCache.source == "canonical",
                WikidataStudioCache.approved_only.is_(True),
            )
        )
    ).one_or_none()
    if cache is None:
        raise HTTPException(409, "Build the canonical Studio items first.")
    token = hashlib.sha256(
        f"{cache.id}:{cache.built_at}:{cache.input_fingerprint}".encode()
    ).hexdigest()
    if expected_build_token is not None and expected_build_token != token:
        raise HTTPException(409, "The build changed. Refresh the repair plan.")

    item = WikidataStudioItemRow
    override = WikidataItemOverride
    scope = (
        item.run_id == run_id,
        item.source == "canonical",
        item.approved_only.is_(True),
    )
    total = int(
        (await db.execute(select(func.count()).select_from(item).where(*scope))).scalar_one()
    )
    rows = (
        await db.execute(
            select(
                item.local_id,
                item.label_en,
                item.label_he,
                item.existing_qid,
                item.payload["validation_issues"].label("issues"),
                override.statement_edits,
                override.approved,
                override.updated_at,
            )
            .outerjoin(
                override, and_(override.run_id == item.run_id, override.local_id == item.local_id)
            )
            .where(*scope, item.local_id > cursor)
            .order_by(item.local_id)
            .limit(page_size + 1)
        )
    ).all()
    # Check again so a concurrent rebuild cannot silently splice two pages.
    current = (
        await db.execute(
            select(
                WikidataStudioCache.id,
                WikidataStudioCache.built_at,
                WikidataStudioCache.input_fingerprint,
            ).where(
                WikidataStudioCache.run_id == run_id,
                WikidataStudioCache.source == "canonical",
                WikidataStudioCache.approved_only.is_(True),
            )
        )
    ).one_or_none()
    if current != cache:
        raise HTTPException(409, "The build changed. Refresh the repair plan.")
    return RepairInventoryPage(
        run_id=run_id,
        build_id=cache.id,
        build_token=token,
        built_at=cache.built_at,
        checked_at=datetime.now(UTC),
        total_items=total,
        items=[
            RepairInventoryItem(
                local_id=row.local_id,
                label=(row.label_en or row.label_he or row.local_id)[:600],
                existing_qid=row.existing_qid,
                validation_codes=validation_codes(row.issues),
                qid_edit_indices=qid_edit_indices(row.statement_edits),
                approved=row.approved,
                override_updated_at=row.updated_at,
            )
            for row in rows[:page_size]
        ],
        next_cursor=rows[page_size - 1].local_id if len(rows) > page_size else None,
    )
