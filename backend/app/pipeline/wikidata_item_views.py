"""Build merged Wikidata Studio item views for the review UI."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.item_override import WikidataItemOverride
from app.models.wikibase_cloud_write import CHANNEL_WIKIDATA_UPLOAD, TARGET_ITEM
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.pipeline.ai_verdict_cache_common import normalise_public_verdict
from app.pipeline.hmo_canonical_wikidata import filter_public_wikidata_items
from app.pipeline.marc_verify_context import load_run_marc_records
from app.pipeline.wikidata_item_merge import apply_wikidata_item_override, override_row_to_dict
from app.pipeline.wikidata_qid_ledger import (
    ledger_key_for_item,
    load_global_ledger,
    lookup_ledger_qid,
)
from app.pipeline.wikidata_verdict_cache import (
    attach_local_reference_targets,
    sanitise_stale_wikidata_verdict,
)
from app.pipeline.wikidata_verify_evidence import enrich_items_with_verify_evidence
from app.pipeline.wikidata_verify_fixture import slim_item_for_verdict_persist
from app.services.wikibase_audit import fetch_latest_wikibase_writes

logger = logging.getLogger(__name__)


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
    verdict_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    stable_items: dict[str, dict[str, Any]] = {}
    for raw in filter_public_wikidata_items(
        cache_row.result_items or [],
        source=source,
    ):
        local_id = str(raw.get("local_id") or "")
        ov_row = overrides_by_id.get(local_id)
        stable_items[local_id] = (
            apply_wikidata_item_override(raw, override_row_to_dict(ov_row))
            if ov_row else dict(raw)
        )
        row = _merge_one_wikidata_item(
            raw,
            ov_row=ov_row,
            ledger=ledger,
            latest_writes=latest_writes,
        )
        stored = row.get("ai_verdict")
        if stored:
            verdict_rows.append((row, stored))
        items.append(row)

    attach_local_reference_targets(items)
    stable_rows = list(stable_items.values())
    attach_local_reference_targets(stable_rows)
    # Verify fingerprints items after cached QID adoption (Rule W-168) and after
    # live value-label glosses (Rule W-80). The Studio cache may still be a CREATE
    # until the next rebuild, and list reads never glossed QIDs — so every
    # subset-verify verdict landed in overrides and then vanished as "unknown"
    # (Rule W-169).
    # Ledger QIDs are presentation data. Only probe adoptions belong in the
    # stable projection used to validate a stored verdict (Rule W-205).
    adopted_local_ids = await _adopt_cached_duplicate_qids(items) or set()
    _mirror_adopted_qids(items, stable_rows, adopted_local_ids)
    if verdict_rows:
        await _attach_live_labels_for_verdict_keys(items, stable_rows)
    stable_items = {
        str(item.get("local_id") or ""): slim_item_for_verdict_persist(item)
        for item in stable_rows
    }
    _sanitise_merged_verdicts(verdict_rows, items, marc_records, stable_items)
    return items


async def _adopt_cached_duplicate_qids(items: list[dict[str, Any]]) -> set[str]:
    """Apply Rule W-168 adoption from the probe cache onto review-table rows."""
    if not items:
        return set()
    from app.db import session_scope  # noqa: PLC0415
    from app.pipeline.wikidata_duplicate_probe import (  # noqa: PLC0415
        adopt_identifier_matched_duplicates,
        attach_cached_duplicate_evidence,
    )

    try:
        await attach_cached_duplicate_evidence(session_scope, items)
        adopted = adopt_identifier_matched_duplicates(items)
    except Exception:  # noqa: BLE001 — never fail a list read over an optimisation
        logger.exception(
            "duplicate-QID adoption on Studio merge failed; leaving items as CREATE",
        )
        return set()
    finally:
        for item in items:
            item.pop("_wikidata_existence", None)
    return {
        str(row.get("local_id") or "")
        for row in adopted
        if isinstance(row, dict) and str(row.get("local_id") or "")
    }


def _mirror_adopted_qids(
    items: list[dict[str, Any]],
    stable_rows: list[dict[str, Any]],
    adopted_local_ids: set[str],
) -> None:
    """Copy probe-adopted QIDs onto the pre-derived fingerprint rows."""
    by_id = {
        str(item.get("local_id") or ""): item
        for item in items
        if str(item.get("local_id") or "")
    }
    for row in stable_rows:
        local_id = str(row.get("local_id") or "")
        if local_id not in adopted_local_ids:
            continue
        src = by_id.get(local_id)
        if src is None:
            continue
        qid = str(src.get("existing_qid") or "").strip()
        if qid and not str(row.get("existing_qid") or "").strip():
            row["existing_qid"] = qid


async def _attach_live_labels_for_verdict_keys(
    items: list[dict[str, Any]],
    stable_rows: list[dict[str, Any]],
) -> None:
    """Gloss statement QIDs the same way verify does before fingerprinting."""
    from app.db import session_scope  # noqa: PLC0415
    from app.pipeline.wikidata_verify_evidence import attach_live_value_labels  # noqa: PLC0415

    try:
        await attach_live_value_labels(session_scope, items + stable_rows)
    except Exception:  # noqa: BLE001 — a gloss must never fail a list read
        logger.exception("live value-label attach on Studio merge failed")


def _sanitise_merged_verdicts(
    verdict_rows: list[tuple[dict[str, Any], dict[str, Any]]],
    items: list[dict[str, Any]],
    marc_records: list[dict[str, Any]],
    stable_items: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Drop verdicts whose fingerprint no longer matches the current item.

    The evidence packs (``local_reference_targets`` + ``verify_evidence``) are
    part of the fingerprint, so they MUST be attached exactly as the verify
    worker attaches them before any comparison (Rule W-136).
    """
    if not verdict_rows:
        return
    enrich_items_with_verify_evidence(items, marc_records)
    stable_by_id = stable_items or {}
    for row, stored in verdict_rows:
        row["ai_verdict"] = sanitise_stale_wikidata_verdict(
            row,
            stored,
            marc_context=row.get("_marc_context") or {},
            stable_item=stable_by_id.get(str(row.get("local_id") or "")),
        )
        if row["ai_verdict"] is None:
            row["ai_verdict_at"] = None


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


_LIST_VIEW_DROP_KEYS = frozenset({
    "statements",
    "authority_evidence",
    "work_candidate_evidence",
    "local_reference_targets",
    "verify_evidence",
    "_marc_context",
    "wikidata_live",
})


def slim_ai_verdict_for_list(
    ai_verdict: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(ai_verdict, dict):
        return None
    fixes = ai_verdict.get("suggested_fixes")
    slim: dict[str, Any] = {}
    for key in ("overall", "reasoning", "model", "judged_at"):
        value = ai_verdict.get(key)
        if value not in (None, ""):
            slim[key] = value
    for key in ("verification_status", "verification_error", "judge_failure"):
        value = ai_verdict.get(key)
        if value not in (None, ""):
            slim[key] = value
    if isinstance(fixes, list) and fixes:
        slim["has_suggested_fixes"] = True
    return slim or None


def trim_studio_list_item(item: dict[str, Any]) -> dict[str, Any]:
    """Drop bulky Studio fields for paginated list GETs (Rule W-131)."""
    row = {k: v for k, v in item.items() if k not in _LIST_VIEW_DROP_KEYS}
    statements = item.get("statements")
    if isinstance(statements, list):
        row["statement_count"] = len(statements)
    elif item.get("statement_count") is not None:
        row["statement_count"] = item.get("statement_count")
    stored_verdict = item.get("ai_verdict")
    verdict = slim_ai_verdict_for_list(
        normalise_public_verdict(stored_verdict)
        if isinstance(stored_verdict, dict) else None,
    )
    if verdict is not None:
        row["ai_verdict"] = verdict
    else:
        row.pop("ai_verdict", None)
        row.pop("ai_verdict_at", None)
    return row


def _merge_one_wikidata_item(
    raw: dict[str, Any],
    *,
    ov_row: WikidataItemOverride | None,
    ledger: dict[str, str],
    latest_writes: dict[str, Any],
) -> dict[str, Any]:
    entity = dict(raw)
    local_id = str(entity.get("local_id") or "")
    ov_dict = override_row_to_dict(ov_row) if ov_row else {}
    merged = apply_wikidata_item_override(entity, ov_dict) if ov_row else entity

    ledger_key = ledger_key_for_item(merged)
    ledger_qid = lookup_ledger_qid(ledger, ledger_key)
    existing_qid = merged.get("existing_qid") or ledger_qid
    on_wikidata = bool(existing_qid)

    last_write = latest_writes.get(local_id)
    validation_issues = list(merged.get("validation_issues") or [])

    ai_verdict = (
        normalise_public_verdict(ov_row.ai_verdict)
        if ov_row and isinstance(ov_row.ai_verdict, dict)
        else None
    )
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
    return row


async def fetch_merged_wikidata_item(
    db: AsyncSession,
    run_id: uuid.UUID,
    local_id: str,
    *,
    approved_only: bool = True,
    source: str = "legacy",
) -> dict[str, Any] | None:
    items = await fetch_merged_wikidata_items(
        db, run_id, approved_only=approved_only, source=source,
    )
    for item in items:
        if str(item.get("local_id") or "") == local_id:
            return item
    return None
