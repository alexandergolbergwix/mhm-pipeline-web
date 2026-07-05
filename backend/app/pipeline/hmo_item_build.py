"""Full HMO item export wired to the live schema (Phase 4 of HMO
Wikibase Studio — see dev-docs/hmo-wikibase-studio-plan.md).

Resolves a run's exported RDF instances (manuscripts, persons, places,
etc.) against the live schema mapping (Phase 3) into
``ResolvedWikibaseEntity`` objects, cached per-run in
``hmo_studio_item_cache`` keyed by a fingerprint over the RDF bytes AND
the schema-mapping version — so a schema bootstrap invalidates every
run's cached build, and re-running on unchanged inputs is a cache hit.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.wikibase_entity_mapping import WikibaseEntityMapping
from app.pipeline.hmo_item_shacl import build_shacl_report_for_items
from converter.wikibase.hmo_exporter import HmoWikibaseExporter, resolve_against_mappings
from converter.wikibase.resolved_models import ResolvedWikibaseEntity, SchemaMappingEntry


@dataclass(frozen=True)
class HmoItemBuildResult:
    entities: list[ResolvedWikibaseEntity]
    entity_count: int
    deferred_link_count: int
    skipped_statement_count: int
    from_cache: bool
    built_at: str | None = None
    shacl_report: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


async def compute_hmo_build_fingerprint(db: AsyncSession, ttl_path: Path) -> str:
    """SHA-256 over the RDF TTL bytes AND the schema-mapping version."""
    ttl_hash = await run_in_threadpool(_hash_file, ttl_path)
    schema_version = await _schema_mapping_version(db)
    return hashlib.sha256(f"{ttl_hash}:{schema_version}".encode()).hexdigest()


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _schema_mapping_version(db: AsyncSession) -> str:
    """A cheap version stamp for the current schema-mapping state.

    Count + latest created_at over schema-level (``run_id IS NULL``)
    mapping rows — bumps whenever the bootstrap creates a new class or
    property, which is exactly when previously-resolved builds may now
    resolve differently (or newly succeed where they used to fail).
    """
    count, max_created = (
        await db.execute(
            select(
                func.count(WikibaseEntityMapping.id),
                func.max(WikibaseEntityMapping.created_at),
            ).where(WikibaseEntityMapping.run_id.is_(None))
        )
    ).one()
    return f"{count}:{max_created.isoformat() if max_created else 'none'}"


async def _load_schema_mappings(db: AsyncSession) -> dict[str, SchemaMappingEntry]:
    rows = (
        await db.execute(
            select(
                WikibaseEntityMapping.ontology_uri,
                WikibaseEntityMapping.wikibase_id,
                WikibaseEntityMapping.datatype,
            ).where(WikibaseEntityMapping.run_id.is_(None))
        )
    ).all()
    return {
        uri: SchemaMappingEntry(wikibase_id=wid, datatype=dtype)
        for uri, wid, dtype in rows
    }


async def build_items_for_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    ttl_path: Path,
    *,
    force_rebuild: bool = False,
) -> HmoItemBuildResult:
    """Build (or return the cached) resolved item set for one run.

    Raises :class:`converter.wikibase.resolved_models.UnmappedOntologyUriError`
    when the RDF graph references a class/property the schema bootstrap
    hasn't created yet — callers should surface this as a 409 pointing
    the curator at the schema bootstrap, not swallow it.
    """
    fingerprint = await compute_hmo_build_fingerprint(db, ttl_path)

    if not force_rebuild:
        cached = (
            await db.execute(
                select(HmoStudioItemCache).where(HmoStudioItemCache.run_id == run_id)
            )
        ).scalar_one_or_none()
        if cached is not None and cached.input_fingerprint == fingerprint:
            return HmoItemBuildResult(
                entities=[
                    ResolvedWikibaseEntity.from_dict(e) for e in cached.resolved_entities
                ],
                entity_count=cached.entity_count,
                deferred_link_count=cached.deferred_link_count,
                skipped_statement_count=cached.skipped_statement_count,
                from_cache=True,
                built_at=cached.built_at.isoformat(),
                shacl_report=cached.shacl_report or {},
            )

    schema_mappings = await _load_schema_mappings(db)
    drafts = await run_in_threadpool(HmoWikibaseExporter().from_ttl, ttl_path)
    resolved = await run_in_threadpool(resolve_against_mappings, drafts, schema_mappings)

    deferred_count = sum(len(e.deferred_links) for e in resolved)
    skipped_count = sum(len(e.skipped_statements) for e in resolved)
    resolved_dicts = [e.to_dict() for e in resolved]
    shacl_report = await build_shacl_report_for_items(ttl_path, resolved_dicts)
    await _upsert_cache(
        db,
        run_id=run_id,
        fingerprint=fingerprint,
        entities=resolved,
        deferred_count=deferred_count,
        skipped_count=skipped_count,
        shacl_report=shacl_report,
    )

    return HmoItemBuildResult(
        entities=resolved,
        entity_count=len(resolved),
        deferred_link_count=deferred_count,
        skipped_statement_count=skipped_count,
        from_cache=False,
        shacl_report=shacl_report,
    )


async def _upsert_cache(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    fingerprint: str,
    entities: list[ResolvedWikibaseEntity],
    deferred_count: int,
    skipped_count: int,
    shacl_report: dict[str, list[dict[str, Any]]] | None = None,
) -> None:
    existing = (
        await db.execute(
            select(HmoStudioItemCache).where(HmoStudioItemCache.run_id == run_id)
        )
    ).scalar_one_or_none()

    resolved_entities = [e.to_dict() for e in entities]
    if existing is None:
        db.add(
            HmoStudioItemCache(
                run_id=run_id,
                input_fingerprint=fingerprint,
                resolved_entities=resolved_entities,
                entity_count=len(entities),
                deferred_link_count=deferred_count,
                skipped_statement_count=skipped_count,
                shacl_report=shacl_report,
            )
        )
    else:
        existing.input_fingerprint = fingerprint
        existing.resolved_entities = resolved_entities
        existing.entity_count = len(entities)
        existing.deferred_link_count = deferred_count
        existing.skipped_statement_count = skipped_count
        existing.shacl_report = shacl_report
    await db.commit()
