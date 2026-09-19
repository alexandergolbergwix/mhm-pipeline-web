"""Build merged HMO Wikibase item views for the review UI."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.hmo_studio_item_override import HmoStudioItemOverride
from app.models.wikibase_cloud_write import (
    CHANNEL_ITEM_UPLOAD,
    TARGET_ITEM,
    WikibaseCloudWrite,
)
from app.models.wikibase_entity_mapping import (
    ENTITY_KIND_INSTANCE,
    WikibaseEntityMapping,
)
from app.pipeline.ai_verdict_cache_common import normalise_public_verdict
from app.pipeline.hmo_item_merge import apply_hmo_item_override, override_row_to_dict
from app.pipeline.hmo_item_shacl import item_has_blocking_shacl
from app.pipeline.hmo_item_verdict_cache import sanitise_stale_hmo_item_verdict
from app.pipeline.marc_verify_context import (
    index_marc_records,
    load_run_marc_records,
    marc_context_for_item,
)
from app.pipeline.rule_verify.persist import compact_rule_verdict
from app.services.wikibase_audit import fetch_latest_wikibase_writes


class ItemBuildMissingError(RuntimeError):
    def __init__(self, run_id: uuid.UUID) -> None:
        super().__init__(f"No item build exists for run {run_id}. Call build-items first.")


async def hmo_items_fingerprint(db: AsyncSession, run_id: uuid.UUID) -> str:
    """Cheap staleness fingerprint for the merged items read model.

    The merged view changes only when the item cache, an override, an
    item-upload write, or an instance mapping changes — each is a cheap
    indexed max()/count() against a small table, versus the 50-100 MB
    JSONB deserialise + merge that :func:`fetch_merged_hmo_items` pays.
    The fingerprint keys a scoped cache entry, so a change always produces
    a new key (self-invalidating — no invalidation wiring, Rule W-239
    pattern) and an unchanged run serves the cached serialisation.
    """
    from sqlalchemy import func  # noqa: PLC0415

    built_at = await db.scalar(
        select(HmoStudioItemCache.built_at).where(
            HmoStudioItemCache.run_id == run_id,
        )
    )
    override_latest = await db.scalar(
        select(func.max(HmoStudioItemOverride.updated_at)).where(
            HmoStudioItemOverride.run_id == run_id,
        )
    )
    write_latest = await db.scalar(
        select(func.max(WikibaseCloudWrite.created_at)).where(
            WikibaseCloudWrite.run_id == run_id,
            WikibaseCloudWrite.channel == CHANNEL_ITEM_UPLOAD,
            WikibaseCloudWrite.target_kind == TARGET_ITEM,
        )
    )
    mapping_latest = await db.scalar(
        select(func.max(WikibaseEntityMapping.created_at)).where(
            WikibaseEntityMapping.run_id == run_id,
            WikibaseEntityMapping.entity_kind == ENTITY_KIND_INSTANCE,
        )
    )
    return "|".join(
        (x.isoformat() if x else "-")
        for x in (built_at, override_latest, write_latest, mapping_latest)
    )


async def fetch_merged_hmo_items_cached(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """``fetch_merged_hmo_items`` behind a fingerprint-keyed memory cache.

    Deliberately in-process, NOT the Redis scoped cache: the merged item
    list is a 50-100 MB payload, and pushing that to Redis on every
    override/build would evict other tenants' keys. WEB_CONCURRENCY is 1
    in production, so one process holds the entry.

    A repeat load of an unchanged run returns in milliseconds instead of
    re-merging 18k entities for ~30 s (2026-09-18 curator feedback: the
    review table took half a minute on every load).
    """
    fingerprint = await hmo_items_fingerprint(db, run_id)
    hit = _ITEMS_CACHE.get(run_id)
    if hit is not None and hit[0] == fingerprint:
        return hit[1]
    items = await fetch_merged_hmo_items(db, run_id)
    _ITEMS_CACHE.clear()  # one run at a time — the payload is huge
    _ITEMS_CACHE[run_id] = (fingerprint, items)
    return items


# {run_id: (fingerprint, items)} — single-entry working set (see docstring).
_ITEMS_CACHE: dict[uuid.UUID, tuple[str, list[dict[str, Any]]]] = {}


def invalidate_hmo_items_cache(run_id: uuid.UUID | None = None) -> None:
    """Drop the merged-items memory cache (tests / explicit invalidation)."""
    if run_id is None:
        _ITEMS_CACHE.clear()
    else:
        _ITEMS_CACHE.pop(run_id, None)


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

    marc_index: dict[str, dict[str, Any]] = {}
    if any(row.ai_verdict for row in override_rows):
        marc_records = await load_run_marc_records(db, run_id)
        marc_index = index_marc_records(marc_records)

    raw_entities = cache_row.resolved_entities or []
    for i, raw in enumerate(raw_entities):
        # Yield between entities: the 18k-entity merge is pure CPU on a
        # 50-100 MB JSONB payload — without a periodic yield the event
        # loop blocks, the job heartbeat dies, and the stale reap (which
        # lives on the same loop) can never fire (2026-09-18: the verify
        # wedged the whole dyno at "Loading Studio scope…", Rule W-245).
        if i % 500 == 0:
            await asyncio.sleep(0)
        entity = dict(raw)
        local_id = str(entity.get("local_id") or "")
        ov_row = overrides_by_id.get(local_id)
        ov_dict = override_row_to_dict(ov_row) if ov_row else {}
        merged = apply_hmo_item_override(entity, ov_dict) if ov_row else entity

        source_uri = str(merged.get("source_uri") or "")
        wikibase_id = uri_to_qid.get(source_uri)
        status = "created" if wikibase_id else "would_create"

        last_write = latest_writes.get(source_uri)

        ai_verdict = (
            normalise_public_verdict(ov_row.ai_verdict)
            if ov_row and isinstance(ov_row.ai_verdict, dict)
            else None
        )
        # Compact rollup only — the full 30-rule verdict bodies stay on the
        # override rows; shipping them here R14'd the web dyno (2026-09-19).
        rule_verdict = compact_rule_verdict(
            ov_row.rule_verdict if ov_row else None
        )
        shacl_issues = shacl_report.get(local_id) or []
        row = {
            **merged,
            "local_id": local_id,
            "status": status,
            "wikibase_id": wikibase_id,
            "approved": ov_row.approved if ov_row else None,
            "shacl_issues": shacl_issues,
            "has_blocking_shacl": item_has_blocking_shacl(shacl_issues),
            "ai_verdict": ai_verdict,
            "ai_verdict_at": (
                ov_row.ai_verdict_at.isoformat()
                if ov_row and ov_row.ai_verdict_at else None
            ),
            "rule_verdict": rule_verdict,
            "rule_verdict_at": (
                ov_row.rule_verdict_at.isoformat()
                if ov_row and ov_row.rule_verdict_at else None
            ),
            "override_present": ov_row is not None,
            "override_id": str(ov_row.id) if ov_row else None,
            "upload_outcome": last_write.operation if last_write else None,
            "upload_message": last_write.outcome_message if last_write else "",
            "upload_at": (
                last_write.created_at.isoformat() if last_write else None
            ),
        }
        if ai_verdict and marc_index:
            row["ai_verdict"] = sanitise_stale_hmo_item_verdict(
                row,
                marc_context=marc_context_for_item(row, marc_index),
            )
            if row["ai_verdict"] is None:
                row["ai_verdict_at"] = None
        items.append(row)
    return items


async def fetch_validation_error_items(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    on_wiki_only: bool = False,
) -> list[dict[str, Any]]:
    """Items with blocking SHACL issues — for cleanup / curator review."""
    items = await fetch_merged_hmo_items(db, run_id)
    blocked = [i for i in items if i.get("has_blocking_shacl")]
    if on_wiki_only:
        blocked = [i for i in blocked if i.get("status") == "created"]
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


# ── Rule-verify export (streamed, O(chunk) memory — Rule W-247) ────────

# Verdicts with pass results stripped and pass-count computed in SQL: the
# full 18k-row rule_verdict JSONB is ~318 MB as Python dicts (R15 on a
# 512 MB dyno). ``with_fail`` keeps only entities with at least one fail
# (scope=failures, 2218 rows on run 3494ebf5) instead of all 18464.
_VERDICT_EXPORT_SQL = text("""
    SELECT o.local_id AS local_id, o.approved AS approved,
           jsonb_build_object(
             'overall', o.rule_verdict->'overall',
             'results', COALESCE((
                 SELECT jsonb_agg(r)
                 FROM jsonb_array_elements(o.rule_verdict->'results') r
                 WHERE r->>'state' <> 'pass'), '[]'::jsonb),
             'pass_count', (
                 SELECT count(*)
                 FROM jsonb_array_elements(o.rule_verdict->'results') r
                 WHERE r->>'state' = 'pass')
           ) AS verdict
    FROM hmo_studio_item_overrides o
    WHERE o.run_id = :run_id
      AND o.rule_verdict ? 'results'
      AND jsonb_array_length(o.rule_verdict->'results') > 0
      AND (:with_fail = FALSE OR EXISTS (
          SELECT 1 FROM jsonb_array_elements(o.rule_verdict->'results') r
          WHERE r->>'state' = 'fail'))
""")

# One entity dict per row off a server-side cursor — the ~45 MB array is
# never materialised in Python.
_ENTITY_EXPORT_SQL = text("""
    SELECT el AS entity
    FROM hmo_studio_item_cache c
    CROSS JOIN LATERAL jsonb_array_elements(c.resolved_entities) AS el
    WHERE c.run_id = :run_id
      AND el->>'local_id' = ANY(:ids)
""").bindparams(bindparam("ids"))

_OVERRIDE_EXPORT_SQL = text("""
    SELECT local_id, labels, descriptions, aliases,
           add_statements, remove_statements, statement_edits
    FROM hmo_studio_item_overrides
    WHERE run_id = :run_id AND local_id = ANY(:ids)
""").bindparams(bindparam("ids"))

_SHACL_EXPORT_SQL = text("""
    SELECT s.key, s.value
    FROM hmo_studio_item_cache c
    CROSS JOIN LATERAL jsonb_each(COALESCE(c.shacl_report, '{}'::jsonb)) AS s
    WHERE c.run_id = :run_id AND s.key = ANY(:ids)
""").bindparams(bindparam("ids"))


def _light_override_dict(row: Any) -> dict[str, Any]:
    """Only the fields apply_hmo_item_override consumes — no AI/rule bodies."""
    return {
        "labels": dict(row.labels or {}),
        "descriptions": dict(row.descriptions or {}),
        "aliases": dict(row.aliases or {}),
        "add_statements": list(row.add_statements or []),
        "remove_statements": list(row.remove_statements or []),
        "statement_edits": dict(row.statement_edits or {}),
    }


def _rule_verify_export_row(
    entity: dict[str, Any],
    verdict: dict[str, Any],
    ov_dict: dict[str, Any],
    uri_to_qid: dict[str, str],
    shacl_issue: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    merged = apply_hmo_item_override(entity, ov_dict) if ov_dict else entity
    source_uri = str(merged.get("source_uri") or "")
    wikibase_id = uri_to_qid.get(source_uri)
    return {
        "local_id": str(entity.get("local_id") or ""),
        "label": item_label(merged),
        "class_qid": merged.get("class_qid"),
        "entity_type": merged.get("entity_type"),
        "source_uri": merged.get("source_uri"),
        "wikibase_id": wikibase_id,
        "status": "created" if wikibase_id else "would_create",
        "approved": verdict["approved"],
        "overall": verdict["overall"],
        "pass_count": verdict["pass_count"],
        "results": verdict["results"],
        "entity": {
            "labels": merged.get("labels") or {},
            "descriptions": merged.get("descriptions") or {},
            "aliases": merged.get("aliases") or {},
            "claims": merged.get("claims") or [],
            "control_numbers": merged.get("control_numbers") or [],
            "authority_evidence": merged.get("authority_evidence") or [],
            "skipped_statements": merged.get("skipped_statements") or [],
            "shacl_issues": shacl_issue or [],
        },
    }


async def iter_rule_verify_export_rows(
    db: AsyncSession,
    run_id: uuid.UUID,
    scope: str,
) -> AsyncIterator[dict[str, Any]]:
    """Stream rule-verify export rows with O(chunk) memory (Rule W-247).

    The full merged view costs ~1 GB RSS on an 18k run — a Basic 512 MB
    dyno R15s mid-request. Verdicts are stripped + scope-filtered in SQL,
    entity dicts stream off a server-side cursor, and the same
    override/mapping merge runs per entity. SQLite (tests) keeps the
    in-memory fallback: the JSONB SQL is Postgres-only.
    """
    is_postgres = db.get_bind().dialect.name == "postgresql"
    if not is_postgres:
        items = await fetch_merged_hmo_items(db, run_id)
        # The merged view ships only the compact verdict (no per-rule
        # bodies) — the full verdicts come from the override rows (tiny
        # on the SQLite test fixtures).
        verdict_rows = (
            await db.execute(
                select(
                    HmoStudioItemOverride.local_id,
                    HmoStudioItemOverride.rule_verdict,
                ).where(HmoStudioItemOverride.run_id == run_id)
            )
        ).all()
        verdicts = {
            str(local_id): verdict
            for local_id, verdict in verdict_rows
            if isinstance(verdict, dict) and verdict.get("results")
        }
        for item in items:
            local_id = str(item.get("local_id") or "")
            full_verdict = verdicts.get(local_id)
            if full_verdict is None:
                continue
            results = [r for r in full_verdict["results"] if isinstance(r, dict)]
            if scope == "failures" and not any(
                r.get("state") == "fail" for r in results
            ):
                continue
            yield _rule_verify_export_row(
                item,
                {
                    "approved": item.get("approved"),
                    "overall": full_verdict.get("overall"),
                    "pass_count": sum(
                        1 for r in results if r.get("state") == "pass"
                    ),
                    "results": [r for r in results if r.get("state") != "pass"],
                },
                {},
                {},
                None,
            )
        return

    verdict_rows = (
        await db.execute(
            _VERDICT_EXPORT_SQL,
            {"run_id": run_id, "with_fail": scope == "failures"},
        )
    ).all()
    verdicts: dict[str, dict[str, Any]] = {
        str(row.local_id): {
            "approved": row.approved,
            "overall": row.verdict.get("overall"),
            "pass_count": int(row.verdict.get("pass_count") or 0),
            "results": row.verdict.get("results") or [],
        }
        for row in verdict_rows
    }
    if not verdicts:
        return
    ids = list(verdicts)

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
    shacl = {
        str(key): value
        for key, value in (
            await db.execute(_SHACL_EXPORT_SQL, {"run_id": run_id, "ids": ids})
        ).all()
    }
    overrides_by_id = {
        str(row.local_id): _light_override_dict(row)
        for row in (
            await db.execute(_OVERRIDE_EXPORT_SQL, {"run_id": run_id, "ids": ids})
        ).all()
    }

    result = await db.stream(
        _ENTITY_EXPORT_SQL,
        {"run_id": run_id, "ids": ids},
        execution_options={"yield_per": 500},
    )
    async for partition in result.partitions():
        for (entity,) in partition:
            entity = dict(entity)
            verdict = verdicts.get(str(entity.get("local_id") or ""))
            if verdict is None:
                continue
            yield _rule_verify_export_row(
                entity,
                verdict,
                overrides_by_id.get(str(entity.get("local_id") or "")),
                uri_to_qid,
                shacl.get(str(entity.get("local_id") or "")),
            )
