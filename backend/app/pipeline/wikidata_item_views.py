"""Build merged Wikidata Studio item views for the review UI."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.item_override import WikidataItemOverride
from app.models.wikibase_cloud_write import CHANNEL_WIKIDATA_UPLOAD, TARGET_ITEM
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.pipeline.marc_verify_context import load_run_marc_records
from app.pipeline.wikidata_item_merge import apply_wikidata_item_override, override_row_to_dict
from app.pipeline.wikidata_qid_ledger import (
    ledger_key_for_item,
    load_global_ledger,
    lookup_ledger_qid,
)
from app.pipeline.wikidata_verdict_cache import (
    attach_local_reference_targets,
    marc_context_for_wikidata_item,
    sanitise_stale_wikidata_verdict,
)
from app.services.wikibase_audit import fetch_latest_wikibase_writes


class StudioBuildMissingError(RuntimeError):
    def __init__(self, run_id: uuid.UUID) -> None:
        super().__init__(f"No Wikidata Studio build exists for run {run_id}.")


def item_has_blocking_validation(issues: list[dict[str, Any]]) -> bool:
    return any(str(i.get("severity") or "").lower() == "error" for i in issues)


async def fetch_merged_wikidata_items(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    approved_only: bool = True,
    source: str = "legacy",
) -> list[dict[str, Any]]:
    cache_row = (
        await db.execute(
            select(WikidataStudioCache).where(
                WikidataStudioCache.run_id == run_id,
                WikidataStudioCache.approved_only == approved_only,
                WikidataStudioCache.source == source,
            )
        )
    ).scalar_one_or_none()
    if cache_row is None:
        raise StudioBuildMissingError(run_id)

    override_rows = (
        await db.execute(
            select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
        )
    ).scalars().all()
    overrides_by_id = {r.local_id: r for r in override_rows}

    ledger = await load_global_ledger(db)
    latest_writes = await fetch_latest_wikibase_writes(
        db, run_id, channel=CHANNEL_WIKIDATA_UPLOAD, target_kind=TARGET_ITEM,
    )

    marc_records: list[dict[str, Any]] = []
    if any(row.ai_verdict for row in override_rows):
        marc_records = await load_run_marc_records(db, run_id)

    items: list[dict[str, Any]] = []
    for raw in cache_row.result_items or []:
        entity = dict(raw)
        local_id = str(entity.get("local_id") or "")
        ov_row = overrides_by_id.get(local_id)
        ov_dict = override_row_to_dict(ov_row) if ov_row else {}
        merged = apply_wikidata_item_override(entity, ov_dict) if ov_row else entity

        ledger_key = ledger_key_for_item(merged)
        ledger_qid = lookup_ledger_qid(ledger, ledger_key)
        existing_qid = merged.get("existing_qid") or ledger_qid
        on_wikidata = bool(existing_qid)

        last_write = latest_writes.get(local_id)
        validation_issues = list(merged.get("validation_issues") or [])

        ai_verdict = ov_row.ai_verdict if ov_row else None
        row: dict[str, Any] = {
            **merged,
            "local_id": local_id,
            "existing_qid": existing_qid,
            "on_wikidata": on_wikidata,
            "approved": ov_row.approved if ov_row else merged.get("approved"),
            "accept_foreign_modify": (
                bool(ov_row.accept_foreign_modify)
                if ov_row and ov_row.accept_foreign_modify is not None
                else False
            ),
            "accepted_foreign_qid": (
                ov_row.accepted_foreign_qid if ov_row else None
            ),
            "validation_issues": validation_issues,
            "has_blocking_validation": item_has_blocking_validation(validation_issues),
            "ai_verdict": ai_verdict,
            "ai_verdict_at": (
                ov_row.ai_verdict_at.isoformat()
                if ov_row and ov_row.ai_verdict_at else None
            ),
            "override_present": ov_row is not None,
            "override_id": str(ov_row.id) if ov_row else None,
            "upload_outcome": last_write.operation if last_write else None,
            "upload_message": last_write.outcome_message if last_write else "",
            "upload_at": (
                last_write.created_at.isoformat() if last_write else None
            ),
        }
        items.append(row)

    attach_local_reference_targets(items)
    for row in items:
        stored_verdict = row.get("ai_verdict")
        if stored_verdict:
            marc_ctx = marc_context_for_wikidata_item(row, marc_records)
            row["ai_verdict"] = sanitise_stale_wikidata_verdict(
                row,
                stored_verdict,
                marc_context=marc_ctx,
            )
            if row["ai_verdict"] is None:
                row["ai_verdict_at"] = None
    return items


async def fetch_validation_error_items(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    approved_only: bool = True,
    source: str = "legacy",
    on_wikidata_only: bool = False,
) -> list[dict[str, Any]]:
    items = await fetch_merged_wikidata_items(
        db, run_id, approved_only=approved_only, source=source,
    )
    blocked = [i for i in items if i.get("has_blocking_validation")]
    if on_wikidata_only:
        blocked = [i for i in blocked if i.get("on_wikidata")]
    return blocked


def item_label(item: dict[str, Any]) -> str:
    labels = item.get("labels")
    if isinstance(labels, dict):
        for key in ("en", "he"):
            value = labels.get(key)
            if value:
                return str(value)
        for value in labels.values():
            if value:
                return str(value)
    return str(item.get("local_id") or "")
