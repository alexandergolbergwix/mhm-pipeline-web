"""Build merged HMO Wikibase item views for the review UI."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.hmo_studio_item_override import HmoStudioItemOverride
from app.models.wikibase_cloud_write import CHANNEL_ITEM_UPLOAD, TARGET_ITEM
from app.models.wikibase_entity_mapping import ENTITY_KIND_INSTANCE, WikibaseEntityMapping
from app.pipeline.hmo_item_merge import apply_hmo_item_override, override_row_to_dict
from app.services.wikibase_audit import fetch_latest_wikibase_writes


class ItemBuildMissingError(RuntimeError):
    def __init__(self, run_id: uuid.UUID) -> None:
        super().__init__(f"No item build exists for run {run_id}. Call build-items first.")


async def fetch_merged_hmo_items(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    cache_row = (
        await db.execute(
            select(HmoStudioItemCache).where(HmoStudioItemCache.run_id == run_id)
        )
    ).scalar_one_or_none()
    if cache_row is None:
        raise ItemBuildMissingError(run_id)

    override_rows = (
        await db.execute(
            select(HmoStudioItemOverride).where(HmoStudioItemOverride.run_id == run_id)
        )
    ).scalars().all()
    overrides_by_id = {r.local_id: r for r in override_rows}

    mapping_rows = (
        await db.execute(
            select(
                WikibaseEntityMapping.ontology_uri,
                WikibaseEntityMapping.wikibase_id,
            ).where(
                WikibaseEntityMapping.run_id == run_id,
                WikibaseEntityMapping.entity_kind == ENTITY_KIND_INSTANCE,
            )
        )
    ).all()
    uri_to_qid = {uri: qid for uri, qid in mapping_rows}

    latest_writes = await fetch_latest_wikibase_writes(
        db, run_id, channel=CHANNEL_ITEM_UPLOAD, target_kind=TARGET_ITEM,
    )

    shacl_report = cache_row.shacl_report or {}
    items: list[dict[str, Any]] = []

    for raw in cache_row.resolved_entities or []:
        entity = dict(raw)
        local_id = str(entity.get("local_id") or "")
        ov_row = overrides_by_id.get(local_id)
        ov_dict = override_row_to_dict(ov_row) if ov_row else {}
        merged = apply_hmo_item_override(entity, ov_dict) if ov_row else entity

        source_uri = str(merged.get("source_uri") or "")
        wikibase_id = uri_to_qid.get(source_uri)
        status = "created" if wikibase_id else "would_create"

        last_write = latest_writes.get(source_uri)

        ai_verdict = ov_row.ai_verdict if ov_row else None
        items.append({
            **merged,
            "local_id": local_id,
            "status": status,
            "wikibase_id": wikibase_id,
            "approved": ov_row.approved if ov_row else None,
            "shacl_issues": shacl_report.get(local_id) or [],
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
        })
    return items


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
