"""Write + lazily backfill ``hmo_studio_item_rows`` (SQL review read-model)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.hmo_studio_item_row import HmoStudioItemRow

logger = logging.getLogger(__name__)

INSERT_CHUNK = 500


def _labels_of(entity: dict[str, Any]) -> dict[str, str]:
    labels = entity.get("labels")
    return {k: str(v) for k, v in (labels or {}).items() if v} if isinstance(labels, dict) else {}


def row_values_from_entity(
    entity: dict[str, Any],
    *,
    ord_: int,
    shacl_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    """The SQL columns derived from one resolved entity + SHACL bucket."""
    labels = _labels_of(entity)
    label_en = labels.get("en")
    label_he = labels.get("he")
    local_id = str(entity.get("local_id") or "")
    label_sort = (label_en or label_he or local_id).lower()
    control_numbers = [str(cn) for cn in entity.get("control_numbers") or [] if cn]
    blocking = any(
        str(i.get("severity") or "") in ("Violation", "Error") for i in shacl_issues
    )
    return {
        "local_id": local_id,
        "ord": ord_,
        "entity": entity,
        "label_en": label_en,
        "label_he": label_he,
        "label_sort": label_sort[:650],
        "class_qid": str(entity.get("class_qid") or ""),
        "entity_type": str(entity.get("entity_type") or ""),
        "source_uri": str(entity.get("source_uri") or ""),
        "control_number": control_numbers[0] if control_numbers else "",
        "shacl_issues": shacl_issues,
        "has_blocking_shacl": blocking,
    }


async def replace_run_rows(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    entities: list[dict[str, Any]],
    shacl_report: dict[str, list[dict[str, Any]]] | None,
) -> int:
    """Rewrite every row for the run (build path + backfill path)."""
    from sqlalchemy import delete

    await db.execute(delete(HmoStudioItemRow).where(HmoStudioItemRow.run_id == run_id))
    report = shacl_report or {}
    written = 0
    for start in range(0, len(entities), INSERT_CHUNK):
        chunk = entities[start : start + INSERT_CHUNK]
        for offset, entity in enumerate(chunk):
            local_id = str(entity.get("local_id") or "")
            db.add(HmoStudioItemRow(
                run_id=run_id,
                **row_values_from_entity(
                    entity, ord_=start + offset, shacl_issues=report.get(local_id) or [],
                ),
            ))
        await db.commit()
        written += len(chunk)
    return written


async def count_run_rows(db: AsyncSession, run_id: uuid.UUID) -> int:
    from sqlalchemy import func, select

    return int(await db.scalar(
        select(func.count()).select_from(HmoStudioItemRow).where(
            HmoStudioItemRow.run_id == run_id,
        )
    ) or 0)


# Runs with a backfill in flight (per process; WEB_CONCURRENCY=1).
_BACKFILLING: set[uuid.UUID] = set()


def items_rows_ready(db: AsyncSession, run_id: uuid.UUID) -> bool | None:
    """True (rows present) / False (backfill started) / raise if no build."""
    return None


async def ensure_rows_backfilled(db: AsyncSession, run_id: uuid.UUID) -> bool:
    """True when rows exist; else start a one-time background backfill.

    Reads the build-cache blob ONCE in the background and writes per-item
    rows; afterwards every review-table read is pure SQL. Runs built after
    migration 0046 get rows written at build time and never backfill.
    """
    import asyncio  # noqa: PLC0415

    cache_exists = await db.scalar(
        select(HmoStudioItemCache.run_id).where(HmoStudioItemCache.run_id == run_id)
    )
    if cache_exists_check(cache_exists=cache_exists) is False:
        raise LookupError(f"no item build for run {run_id}")
    if await count_run_rows(db, run_id) > 0:
        return True
    if run_id in _BACKFILLING:
        return False
    _BACKFILLING.add(run_id)

    async def _backfill() -> None:
        try:
            from app.db import session_scope  # noqa: PLC0415

            async with session_scope() as warm_db:
                cache = (
                    await warm_db.execute(
                        select(HmoStudioItemCache).where(
                            HmoStudioItemCache.run_id == run_id,
                        )
                    )
                ).scalar_one_or_none()
                if cache is None:
                    return
                await replace_run_rows(
                    warm_db,
                    run_id=run_id,
                    entities=list(cache.resolved_entities or []),
                    shacl_report=cache.shacl_report,
                )
            logger.info("hmo item rows backfilled for run %s", run_id)
        except Exception:  # noqa: BLE001 — surfaced via 409 retry loop
            logger.warning(
                "hmo item rows backfill failed for run %s", run_id, exc_info=True,
            )
        finally:
            _BACKFILLING.discard(run_id)

    asyncio.get_running_loop().create_task(_backfill())
    return False


def cache_exists_check(*, cache_exists: Any) -> bool | None:
    return cache_exists is not None
