"""Orchestrate an HMO item build (authority refresh → RDF → export).

Used by ``hmo_item_build`` background jobs so the curator never waits on
``POST /build-items`` inside Heroku's HTTP timeout.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extraction_approval import ExtractionApproval
from app.models.run import AuthorityMatch, Run, RunRecord, RdfTripleOverride
from app.pipeline import hmo_item_build
from app.pipeline.rdf_build import (
    build_rdf_graph,
    ensure_ttl_on_disk,
    normalise_matches,
    rdf_output_path_for_run,
    upsert_rdf_artifact,
)
from converter.wikibase.resolved_models import UnmappedOntologyUriError

logger = logging.getLogger(__name__)

ProgressCb = Callable[..., Awaitable[None]]
CancelCb = Callable[[], Awaitable[bool]]


class HmoItemBuildError(Exception):
    """Curator-facing build failure (maps to job error / HTTP 409)."""

    def __init__(self, message: str, *, conflict: bool = False) -> None:
        super().__init__(message)
        self.conflict = conflict


@dataclass(frozen=True)
class HmoItemBuildJobResult:
    from_cache: bool
    entity_count: int
    deferred_link_count: int
    skipped_statement_count: int
    refreshed_authority: bool
    rebuilt_rdf: bool


async def execute_hmo_item_build(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    force_rebuild: bool = False,
    refresh_authority: bool = True,
    on_progress: ProgressCb | None = None,
    should_cancel: CancelCb | None = None,
    rdf_resume: dict[str, Any] | None = None,
    rdf_checkpoint: dict[str, Any] | None = None,
) -> HmoItemBuildJobResult:
    """Run the full HMO item build pipeline inside an open session.

    ``rdf_resume``/``rdf_checkpoint`` carry the streaming RDF build's
    resume state (job-service R26): the checkpoint dict is mutated in
    place by the build and surfaced to the caller through ``on_progress``
    payloads under ``payload["checkpoint"]`` so the job row can persist
    it and pass it back after a crash.
    """
    run = await db.get(Run, run_id)
    if run is None:
        raise HmoItemBuildError(f"run {run_id} not found", conflict=True)

    async def progress(
        phase: str,
        processed: int,
        total: int,
        message: str,
        *,
        sub_processed: int | None = None,
        sub_total: int | None = None,
        sub_unit: str | None = None,
        sub_message: str | None = None,
    ) -> None:
        if on_progress:
            await on_progress(
                phase,
                processed,
                total,
                message,
                sub_processed=sub_processed,
                sub_total=sub_total,
                sub_unit=sub_unit,
                sub_message=sub_message,
            )

    async def cancelled() -> bool:
        return bool(should_cancel and await should_cancel())

    # ── Idempotence short-circuit (Rule W-239) ─────────────────────────
    # "Build items" on an unchanged run must be instant. When a cached
    # item build exists and no input changed since it was built
    # (authority matches, approvals, NER rows, overrides), serve the
    # cache directly instead of re-running the 3-step pipeline.
    if not force_rebuild:
        from app.models.hmo_studio_item_cache import (  # noqa: PLC0415
            HmoStudioItemCache,
        )
        from sqlalchemy import func as _func  # noqa: PLC0415

        cache_row = (
            await db.execute(
                select(HmoStudioItemCache).where(
                    HmoStudioItemCache.run_id == run_id,
                )
            )
        ).scalar_one_or_none()
        if cache_row is not None:
            built_at = cache_row.built_at
            match_latest = await db.scalar(
                select(
                    _func.max(
                        _func.coalesce(
                            AuthorityMatch.approved_at,
                            AuthorityMatch.created_at,
                        )
                    )
                ).where(AuthorityMatch.run_id == run_id)
            )
            ner_latest = await db.scalar(
                select(_func.max(ExtractionApproval.updated_at)).where(
                    ExtractionApproval.run_id == run_id,
                )
            )
            override_latest = await db.scalar(
                select(_func.max(RdfTripleOverride.created_at)).where(
                    RdfTripleOverride.run_id == run_id,
                )
            )
            candidates = [t for t in (match_latest, ner_latest, override_latest) if t is not None]
            latest_change = max(candidates) if candidates else None
            if latest_change is None or latest_change <= built_at:
                await progress(
                    "done", 3, 3,
                    f"Cached build from {built_at:%Y-%m-%d %H:%M} — nothing changed since.",
                )
                return HmoItemBuildJobResult(
                    from_cache=True,
                    entity_count=cache_row.entity_count,
                    deferred_link_count=cache_row.deferred_link_count,
                    skipped_statement_count=cache_row.skipped_statement_count,
                    refreshed_authority=False,
                    rebuilt_rdf=False,
                )

    ttl_path = rdf_output_path_for_run(str(run_id))
    force_rdf_rebuild = False
    refreshed_authority = False

    if refresh_authority:
        await progress("authority", 1, 3, "Step 1 of 3: Refreshing authority evidence…")
        if await cancelled():
            raise HmoItemBuildError("cancelled", conflict=False)
        from app.pipeline import authority as authority_pipeline  # noqa: PLC0415
        from app.pipeline import authority_re_enrich  # noqa: PLC0415
        from app.pipeline.authority_re_enrich import re_enrich_run  # noqa: PLC0415

        records = (
            await db.execute(select(RunRecord).where(RunRecord.run_id == run_id))
        ).scalars().all()
        matches = (
            await db.execute(select(AuthorityMatch).where(AuthorityMatch.run_id == run_id))
        ).scalars().all()

        async def authority_sub(processed: int, total: int, message: str) -> None:
            await progress(
                "authority",
                1,
                3,
                "Step 1 of 3: Refreshing authority evidence…",
                sub_processed=processed,
                sub_total=total,
                sub_unit="entities",
                sub_message=message,
            )

        try:
            enrich_stats = await re_enrich_run(
                db,
                run,
                authority_pipeline.get_default_matcher(),
                # Cached lookups (shared Postgres inference cache) on the normal
                # path: a Modal preemption restart replays cached entities in
                # seconds instead of re-querying VIAF/KIMA 5k times. Fresh
                # lookups only on the explicit Rebuild (skip cache).
                skip_cache=force_rebuild,
                skip_fresh_enriched=not force_rebuild,
                records=list(records),
                existing_rows=list(matches),
                on_progress=authority_sub,
                should_cancel=cancelled,
            )
        except authority_re_enrich.ReEnrichCancelled:
            # Cancel observed mid-sweep/gather (checked on every progress
            # emit) — map to the runner's cancelled path. Partial work is
            # already committed per entity and resumes via skip-fresh.
            raise HmoItemBuildError("cancelled")
        await db.commit()
        # Recycle the pooled connection: the next phases are long CPU-bound
        # stretches during which an idle Postgres connection gets dropped
        # server-side — that is what killed step 3 with
        # "cannot call Transaction.rollback(): connection is closed"
        # (2026-09-16). expire_on_commit=False keeps loaded rows usable.
        await db.close()
        # Rebuild RDF + items only when enrichment actually changed a row
        # (Rule W-238): an unchanged refresh must hit the item fingerprint
        # cache instead of burning a full rebuild every click. An explicit
        # force_rebuild (Rebuild (skip cache)) always rebuilds.
        force_rdf_rebuild = bool(force_rebuild) or bool(
            (enrich_stats or {}).get("content_changed")
        )
        refreshed_authority = True

    if not force_rdf_rebuild:
        await ensure_ttl_on_disk(ttl_path, run_id, db)

    rebuilt_rdf = False
    if force_rdf_rebuild or not Path(ttl_path).exists():
        await progress("rdf", 2, 3, "Step 2 of 3: Rebuilding RDF graph from MARC + approvals…")
        if await cancelled():
            raise HmoItemBuildError("cancelled", conflict=False)
        records = (
            await db.execute(
                select(RunRecord)
                .where(RunRecord.run_id == run_id)
                .order_by(RunRecord.control_number.asc())
            )
        ).scalars().all()
        if not records:
            raise HmoItemBuildError(
                "Run has no MARC records; ingest records before HMO creation.",
                conflict=True,
            )
        approved_matches = (
            await db.execute(
                select(AuthorityMatch).where(
                    AuthorityMatch.run_id == run_id,
                    AuthorityMatch.approved.is_(True),
                )
            )
        ).scalars().all()
        ner_rows = (
            await db.execute(
                select(ExtractionApproval).where(
                    ExtractionApproval.run_id == run_id,
                    ExtractionApproval.approved.is_(True),
                )
            )
        ).scalars().all()
        entities_by_cn: dict[str, list[dict[str, Any]]] = {}
        for row in ner_rows:
            entities_by_cn.setdefault(row.control_number, []).append({
                "text": row.override_text or row.text,
                "type": (row.override_type or row.type or "").upper(),
                "role": (row.override_role or row.role or "").upper(),
                "source": row.source,
                "start": int(row.start or 0),
                "end": int(row.end or 0),
                "confidence": row.confidence,
                "model_confidence": row.model_confidence,
            })

        async def rdf_sub(payload: dict[str, Any]) -> None:
            await progress(
                "rdf",
                2,
                3,
                "Step 2 of 3: Rebuilding RDF graph from MARC + approvals…",
                sub_processed=int(payload.get("processed") or 0),
                sub_total=int(payload.get("total") or 0),
                sub_unit="records",
                sub_message=str(payload.get("message") or payload.get("current_control_number") or ""),
            )

        if rdf_resume:
            await progress(
                "rdf",
                2,
                3,
                "Step 2 of 3: Resuming RDF graph rebuild…",
                sub_processed=int(rdf_resume.get("record_index") or 0),
            )

        try:
            rdf_result = await build_rdf_graph(
                marc_records=[dict(row.marc) for row in records],
                authority_matches=normalise_matches(approved_matches),
                entities_by_cn=entities_by_cn,
                output_path=ttl_path,
                on_progress=rdf_sub,
                resume=rdf_resume,
                checkpoint=rdf_checkpoint,
            )
            await upsert_rdf_artifact(
                db,
                run_id,
                Path(ttl_path).read_text(encoding="utf-8"),
                triples_count=rdf_result.triples_count,
                manuscripts_count=rdf_result.manuscripts_count,
            )
            await db.commit()
            # Recycle again before the export (same idle-drop risk as above).
            await db.close()
            rebuilt_rdf = True
        except Exception as exc:
            logger.exception("Internal HMO RDF source build failed for run %s", run_id)
            raise HmoItemBuildError(
                "HMO internal source build failed",
                conflict=False,
            ) from exc

    if not Path(ttl_path).exists():
        raise HmoItemBuildError(
            "No RDF graph for this run yet. Build the RDF (RDF Graph) "
            "before building HMO Wikibase items.",
            conflict=True,
        )

    await progress("export", 3, 3, "Step 3 of 3: Exporting Wikibase item drafts…")
    if await cancelled():
        raise HmoItemBuildError("cancelled", conflict=False)

    async def export_sub(processed: int, total: int, message: str) -> None:
        await progress(
            "export",
            3,
            3,
            "Step 3 of 3: Exporting Wikibase item drafts…",
            sub_processed=processed,
            sub_total=total,
            sub_unit="steps",
            sub_message=message,
        )

    try:
        result = await hmo_item_build.build_items_for_run(
            db, run_id, Path(ttl_path),
            force_rebuild=force_rebuild or force_rdf_rebuild,
            on_progress=export_sub,
        )
    except UnmappedOntologyUriError as exc:
        raise HmoItemBuildError(
            "The RDF graph references ontology classes/properties with "
            "no live Wikibase mapping yet. Run the schema bootstrap "
            f"first. Missing: {', '.join(exc.missing_uris[:10])}",
            conflict=True,
        ) from exc

    await progress(
        "done",
        3,
        3,
        f"Built {result.entity_count} entities"
        + (" (cached)" if result.from_cache else ""),
    )
    return HmoItemBuildJobResult(
        from_cache=result.from_cache,
        entity_count=result.entity_count,
        deferred_link_count=result.deferred_link_count,
        skipped_statement_count=result.skipped_statement_count,
        refreshed_authority=refreshed_authority,
        rebuilt_rdf=rebuilt_rdf,
    )
