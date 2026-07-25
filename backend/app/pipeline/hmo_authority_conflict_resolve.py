"""Resolve approved AuthorityMatch identifier collisions for HMO upload."""
from __future__ import annotations

import uuid
from typing import Any, Callable, Awaitable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import AuthorityMatch
from app.pipeline.hmo_authority_gate import build_authority_conflict_report


async def load_run_authority_matches(
    db: AsyncSession, run_id: uuid.UUID,
) -> list[AuthorityMatch]:
    result = await db.execute(
        select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
    )
    return list(result.scalars().all())


def plan_conflict_unapprovals(
    rows: list[AuthorityMatch],
    *,
    keep_match_ids: list[uuid.UUID],
    unapprove_match_ids: list[uuid.UUID],
) -> list[AuthorityMatch]:
    """Compute which approved rows must be unapproved for the requested resolve.

    For each conflict group, if a keep id is provided and belongs to that
    group, every other approved owner of the same identifier is unapproved.
    Explicit ``unapprove_match_ids`` are always included when approved.
    Invalid VIAF rows listed in ``unapprove_match_ids`` are included too.
    """
    report = build_authority_conflict_report(rows)
    by_id = {row.id: row for row in rows}
    to_unapprove: dict[uuid.UUID, AuthorityMatch] = {}

    keep_set = set(keep_match_ids)
    explicit = set(unapprove_match_ids)

    for conflict in report["conflicts"]:
        owner_ids = [
            uuid.UUID(str(o["match_id"]))
            for o in conflict["owners"]
            if o.get("match_id")
        ]
        keep_in_group = [mid for mid in owner_ids if mid in keep_set]
        if len(keep_in_group) > 1:
            raise ValueError(
                f"Keep at most one match for {conflict['kind']}="
                f"{conflict['identifier']}; got {len(keep_in_group)}"
            )
        if keep_in_group:
            keep_id = keep_in_group[0]
            for mid in owner_ids:
                if mid == keep_id:
                    continue
                row = by_id.get(mid)
                if row is not None and row.approved:
                    to_unapprove[mid] = row
        # Explicit unapproves inside this group (including "drop all").
        for mid in owner_ids:
            if mid in explicit:
                row = by_id.get(mid)
                if row is not None and row.approved:
                    to_unapprove[mid] = row

    for invalid in report["invalid"]:
        mid_raw = invalid.get("match_id")
        if not mid_raw:
            continue
        mid = uuid.UUID(str(mid_raw))
        if mid in explicit or mid in keep_set:
            # keep_set on an invalid row is treated as "leave approved and
            # do not clear" — curator must unapprove or fix VIAF separately.
            if mid in explicit:
                row = by_id.get(mid)
                if row is not None and row.approved:
                    to_unapprove[mid] = row

    # Explicit ids that are approved on this run but not in a conflict
    # (e.g. curator dropped a row manually from the list).
    for mid in explicit:
        row = by_id.get(mid)
        if row is not None and row.approved:
            to_unapprove[mid] = row

    # Never unapprove a keep id.
    for mid in keep_set:
        to_unapprove.pop(mid, None)

    return list(to_unapprove.values())


async def resolve_authority_conflicts(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    keep_match_ids: list[uuid.UUID],
    unapprove_match_ids: list[uuid.UUID],
    apply_approval: Callable[[AuthorityMatch, bool, uuid.UUID], None],
    record_event: Callable[..., Awaitable[None]],
) -> dict[str, Any]:
    rows = await load_run_authority_matches(db, run_id)
    before = build_authority_conflict_report(rows)
    if before["ready"] and not unapprove_match_ids:
        return {
            **before,
            "unapproved_match_ids": [],
            "message": "No authority conflicts to resolve.",
        }

    targets = plan_conflict_unapprovals(
        rows,
        keep_match_ids=keep_match_ids,
        unapprove_match_ids=unapprove_match_ids,
    )
    if not targets and (keep_match_ids or unapprove_match_ids):
        # Curator sent a plan that changed nothing — usually stale UI.
        after = build_authority_conflict_report(rows)
        return {
            **after,
            "unapproved_match_ids": [],
            "message": "No matching approved rows to unapprove.",
        }
    if not targets:
        return {
            **before,
            "unapproved_match_ids": [],
            "message": "Select one match to keep per conflict (or unapprove rows).",
        }

    unapproved_ids: list[str] = []
    for row in targets:
        apply_approval(row, False, actor_id)
        await record_event(
            db,
            project_id=project_id,
            actor_id=actor_id,
            row=row,
        )
        unapproved_ids.append(str(row.id))

    after_rows = await load_run_authority_matches(db, run_id)
    after = build_authority_conflict_report(after_rows)
    return {
        **after,
        "unapproved_match_ids": unapproved_ids,
        "message": (
            f"Unapproved {len(unapproved_ids)} colliding match(es). "
            + (
                "Authority gate is clear — rebuild items if RDF still carries "
                "old identity claims, then retry upload."
                if after["ready"]
                else "Some conflicts remain — resolve the rest, then retry."
            )
        ),
    }
