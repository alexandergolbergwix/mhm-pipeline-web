"""Source-scoped review. These routes never write to a public wiki."""

import uuid
from typing import Annotated

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers import wikidata_studio as studio

router = APIRouter(prefix="/runs", tags=["wikidata-studio"])


class SourceChoicePayload(BaseModel):
    model_config = {"extra": "forbid"}
    local_id: str = Field(min_length=1, max_length=500)
    control_number: str = Field(min_length=1, max_length=100)
    candidate_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_qid: str | None = Field(default=None, pattern=r"^Q[1-9][0-9]*$")
    evidence_uri: str = Field(default="", max_length=2000)
    discard: bool = False
    decision: str = Field(default="select", pattern="^(select|reject|document)$")
    reason: str = Field(default="", max_length=2000)


async def _source_item(db, run_id, local_id):
    item = None
    for approved_only in (True, False):
        try:
            item = await studio.fetch_merged_wikidata_item(
                db,
                run_id,
                local_id,
                approved_only=approved_only,
                source="canonical",
            )
        except studio.StudioBuildMissingError:
            continue
        if item is not None:
            break
    if item is None or item.get("entity_type") != "manuscript":
        raise HTTPException(409, "The current canonical manuscript is required.")
    return item


async def _records(db, run_id, local_id, *, item=None):
    from app.pipeline.marc_verify_context import load_run_marc_records_scoped

    if item is None:
        item = await _source_item(db, run_id, local_id)
    rows = await load_run_marc_records_scoped(
        db, run_id, set(item.get("records") or item.get("record_ids") or [])
    )
    return {str(row["_control_number"]): row for row in rows}


@router.get("/{run_id}/wikidata-studio/source-review")
async def get_source_review(
    run_id: uuid.UUID,
    local_id: str,
    auth: Annotated[studio.AuthContext, Depends(studio.current_auth)],
    db: Annotated[AsyncSession, Depends(studio.get_session)],
):
    from app.pipeline.wikidata_source_coverage import source_coverage

    await studio._lookup_run_with_access(db, run_id, auth, write=False)
    item = await _source_item(db, run_id, local_id)
    records = await _records(db, run_id, local_id, item=item)
    row = (
        await db.execute(
            select(studio.WikidataItemOverride).where(
                studio.WikidataItemOverride.run_id == run_id,
                studio.WikidataItemOverride.local_id == local_id,
            )
        )
    ).scalar_one_or_none()
    choices = row.source_review_choices if row else []
    return {
        "local_id": local_id,
        "records": [source_coverage(record, choices, item=item) for record in records.values()],
    }


@router.post("/{run_id}/wikidata-studio/source-choice")
async def choose_source(
    run_id: uuid.UUID,
    payload: SourceChoicePayload,
    auth: Annotated[studio.AuthContext, Depends(studio.current_auth)],
    db: Annotated[AsyncSession, Depends(studio.get_session)],
):
    from app.pipeline.wikidata_source_choices import load_target, make_choice, source_hash

    run = await studio._lookup_run_with_access(db, run_id, auth, write=True)
    records = await _records(db, run_id, payload.local_id)
    raw = records.get(payload.control_number)
    if raw is None or source_hash(raw) != payload.expected_source_hash:
        raise HTTPException(409, "The source changed or is outside this manuscript.")
    choice = None
    if not payload.discard:
        await db.commit()
        try:
            target = await load_target(payload.target_qid) if payload.target_qid else None
            choice = make_choice(
                raw,
                payload.candidate_key,
                payload.expected_source_hash,
                target=target,
                evidence_uri=payload.evidence_uri,
                decision=payload.decision,
                reason=payload.reason,
            )
        except (ValueError, requests.RequestException) as exc:
            raise HTTPException(409, str(exc)) from exc
        run = await studio._lookup_run_with_access(db, run_id, auth, write=True)
    from app.models.run import RunRecord

    await db.execute(
        select(RunRecord.control_number)
        .where(
            RunRecord.run_id == run_id,
            RunRecord.control_number.in_([payload.control_number, f'"{payload.control_number}"']),
        )
        .with_for_update()
    )
    current = await _records(db, run_id, payload.local_id)
    if (
        payload.control_number not in current
        or source_hash(current[payload.control_number]) != payload.expected_source_hash
    ):
        raise HTTPException(409, "The source changed during the public identity check.")
    row = (
        await db.execute(
            select(studio.WikidataItemOverride)
            .where(
                studio.WikidataItemOverride.run_id == run_id,
                studio.WikidataItemOverride.local_id == payload.local_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        row = studio.WikidataItemOverride(
            run_id=run_id, local_id=payload.local_id, updated_by=auth.user.id
        )
        db.add(row)
        await db.flush()
    choices = [
        entry
        for entry in row.source_review_choices or []
        if not (
            entry.get("candidate", {}).get("candidate_key") == payload.candidate_key
            and entry.get("candidate", {}).get("control_number") == payload.control_number
        )
    ]
    if choice:
        if choice["candidate"]["kind"] == "holding":
            choices = [
                entry
                for entry in choices
                if not (
                    entry.get("candidate", {}).get("kind") == "holding"
                    and entry["candidate"].get("control_number") == payload.control_number
                )
            ]
        choices.append(choice)
    await persist_source_choices(db, run, payload.local_id, auth, choices, row=row)
    return {
        "local_id": payload.local_id,
        "source_review_choices": choices,
        "rebuild_required": True,
    }


async def persist_source_choices(db, run, local_id, auth, choices, *, row=None):
    if row is None:
        row = (
            await db.execute(
                select(studio.WikidataItemOverride)
                .where(
                    studio.WikidataItemOverride.run_id == run.id,
                    studio.WikidataItemOverride.local_id == local_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
    if row is None:
        row = studio.WikidataItemOverride(run_id=run.id, local_id=local_id, updated_by=auth.user.id)
        db.add(row)
        await db.flush()
    state = {
        key: value
        for key, value in studio.override_row_to_dict(row).items()
        if key not in {"ai_verdict", "ai_verdict_at", "rule_verdict", "rule_verdict_at"}
    }
    state.update(source_review_choices=choices, approved=None)
    has_history = (
        await db.execute(
            select(studio.ProjectEvent.id)
            .where(
                studio.ProjectEvent.entity_type == studio.ENTITY_TYPE_WIKIDATA_OVERRIDE,
                studio.ProjectEvent.entity_id == str(row.id),
            )
            .limit(1)
        )
    ).scalar_one_or_none() is not None
    await studio.apply_event(
        db,
        project_id=run.project_id,
        entity_type=studio.ENTITY_TYPE_WIKIDATA_OVERRIDE,
        entity_id=str(row.id),
        op=studio.OP_PATCH if has_history else studio.OP_CREATE,
        new_state=state,
        actor_id=auth.user.id,
        message=f"review source occurrence ({local_id})",
    )
    row.source_review_choices = choices
    row.approved = None
    row.ai_verdict = row.ai_verdict_at = row.rule_verdict = row.rule_verdict_at = None
    row.updated_by = auth.user.id
    await db.commit()
