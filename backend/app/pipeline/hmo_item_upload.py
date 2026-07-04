"""Upload resolved HMO items to the Wikibase Cloud (Phase 5 of HMO
Wikibase Studio — see dev-docs/hmo-wikibase-studio-plan.md).

Create-only, two-pass:

* **Pass 1** creates every not-yet-uploaded instance (identified by its
  RDF source URI, unique per ``(ontology_uri, run_id)`` in
  ``wikibase_entity_mappings``) with its non-deferred claims, recording
  a mapping row immediately per success.
* **Pass 2** resolves each entity's ``deferred_links`` (item -> item
  claims that needed both ends to have live QIDs) now that pass 1 (or a
  prior run) may have created the target — entries whose target never
  got created are reported as ``unresolved``, never silently dropped.

v1 is create-only: an already-mapped instance is skipped, never
diffed/edited. See the plan's "Residual Risks" for what that defers.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.wikibase_entity_mapping import ENTITY_KIND_INSTANCE, WikibaseEntityMapping
from app.models.wikibase_cloud_write import (
    OPERATION_CREATE,
    OPERATION_FAILED,
    OPERATION_SKIP,
    TARGET_CLAIM,
    TARGET_ITEM,
)
from app.services.wikibase_audit import record_wikibase_write
from converter.wikibase.resolved_models import ResolvedClaim, ResolvedWikibaseEntity

if TYPE_CHECKING:
    from app.services.wikibase_audit import WikibaseAuditContext


class ItemBuildMissingError(RuntimeError):
    """Raised when no (or a stale) build cache exists for the run."""

    def __init__(self, run_id: uuid.UUID) -> None:
        super().__init__(
            f"No item build exists for run {run_id}. Call build-items first."
        )


@dataclass(frozen=True)
class HmoItemUploadOutcome:
    local_id: str
    source_uri: str
    status: str  # "created" | "skipped" | "would_create" | "failed"
    wikibase_id: str | None = None
    message: str = ""


@dataclass(frozen=True)
class HmoDeferredLinkOutcome:
    source_local_id: str
    property_id: str
    target_local_id: str
    status: str  # "linked" | "would_link" | "unresolved" | "failed"
    message: str = ""


@dataclass(frozen=True)
class HmoItemUploadResult:
    dry_run: bool
    created: int
    skipped: int
    failed: int
    linked: int
    unresolved_links: int
    outcomes: list[HmoItemUploadOutcome] = field(default_factory=list)
    link_outcomes: list[HmoDeferredLinkOutcome] = field(default_factory=list)
    cancelled: bool = False


async def upload_items_for_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    writer: Any | None,
    dry_run: bool = True,
    audit_ctx: WikibaseAuditContext | None = None,
    on_progress: Callable[[int, int, str], Awaitable[None]] | None = None,
    should_cancel: Callable[[], Awaitable[bool]] | None = None,
) -> HmoItemUploadResult:
    """Upload the run's most recent item build. Raises
    :class:`ItemBuildMissingError` if ``build-items`` hasn't run yet.

    ``on_progress(processed, total, message)`` is awaited after every
    item create (pass 1) and deferred-link write (pass 2), with ``total``
    covering both passes. ``should_cancel()`` is polled before each write
    for cooperative cancellation — a partial result with
    ``cancelled=True`` is returned, never an exception.
    """
    cache_row = (
        await db.execute(
            select(HmoStudioItemCache).where(HmoStudioItemCache.run_id == run_id)
        )
    ).scalar_one_or_none()
    if cache_row is None:
        raise ItemBuildMissingError(run_id)

    entities = [ResolvedWikibaseEntity.from_dict(e) for e in cache_row.resolved_entities]
    existing = await _load_run_instance_mappings(db, run_id)
    local_id_to_source_uri = {e.local_id: e.source_uri for e in entities}
    total = len(entities) + sum(len(e.deferred_links) for e in entities)

    outcomes, created_this_call, created, skipped, failed, cancelled = await _pass_one_create(
        db, run_id, entities, existing, writer=writer, dry_run=dry_run,
        audit_ctx=audit_ctx,
        on_progress=on_progress, should_cancel=should_cancel, total=total,
    )
    known_qids = {**existing, **created_this_call}

    link_outcomes: list[HmoDeferredLinkOutcome] = []
    linked = link_failed = unresolved = 0
    if not cancelled:
        link_outcomes, linked, link_failed, unresolved, cancelled = await _pass_two_link(
            db, entities, local_id_to_source_uri, known_qids,
            writer=writer, dry_run=dry_run,
            audit_ctx=audit_ctx,
            on_progress=on_progress, should_cancel=should_cancel,
            total=total, processed_offset=len(entities),
        )

    return HmoItemUploadResult(
        dry_run=dry_run,
        created=created,
        skipped=skipped,
        failed=failed + link_failed,
        linked=linked,
        unresolved_links=unresolved,
        outcomes=outcomes,
        link_outcomes=link_outcomes,
        cancelled=cancelled,
    )


async def _pass_one_create(
    db: AsyncSession,
    run_id: uuid.UUID,
    entities: list[ResolvedWikibaseEntity],
    existing: dict[str, str],
    *,
    writer: Any | None,
    dry_run: bool,
    audit_ctx: WikibaseAuditContext | None = None,
    on_progress: Callable[[int, int, str], Awaitable[None]] | None = None,
    should_cancel: Callable[[], Awaitable[bool]] | None = None,
    total: int = 0,
) -> tuple[list[HmoItemUploadOutcome], dict[str, str], int, int, int, bool]:
    outcomes: list[HmoItemUploadOutcome] = []
    created_this_call: dict[str, str] = {}
    created = skipped = failed = 0
    cancelled = False

    for idx, entity in enumerate(entities):
        if not dry_run and should_cancel is not None and await should_cancel():
            cancelled = True
            break
        if not dry_run and on_progress is not None:
            await on_progress(idx + 1, total, f"{idx + 1}/{len(entities)} items uploaded")
        if entity.source_uri in existing:
            outcomes.append(
                HmoItemUploadOutcome(
                    entity.local_id, entity.source_uri, "skipped",
                    existing[entity.source_uri],
                )
            )
            skipped += 1
            if audit_ctx is not None and not dry_run:
                await record_wikibase_write(
                    db, audit_ctx,
                    operation=OPERATION_SKIP,
                    target_kind=TARGET_ITEM,
                    target_key=entity.source_uri,
                    wikibase_id=existing[entity.source_uri],
                )
            continue

        if dry_run:
            outcomes.append(
                HmoItemUploadOutcome(entity.local_id, entity.source_uri, "would_create")
            )
            # Placeholder id: pass 2 only needs to know "this entity is
            # resolvable" to report would_link vs. unresolved — the
            # value itself is never used for a real write in dry-run.
            created_this_call[entity.source_uri] = "Q_PENDING"
            created += 1
            continue

        wbi_claims = [_build_wbi_claim(c) for c in entity.claims]
        result = await asyncio.to_thread(
            writer.create_item,
            labels=entity.labels,
            descriptions=entity.descriptions,
            claims=wbi_claims,
        )
        if result.entity_id is None:
            outcomes.append(
                HmoItemUploadOutcome(
                    entity.local_id, entity.source_uri, "failed", message=result.message,
                )
            )
            failed += 1
            if audit_ctx is not None:
                await record_wikibase_write(
                    db, audit_ctx,
                    operation=OPERATION_FAILED,
                    target_kind=TARGET_ITEM,
                    target_key=entity.source_uri,
                    outcome_message=result.message,
                )
            continue

        await _record_instance_mapping(
            db,
            source_uri=entity.source_uri,
            wikibase_id=result.entity_id,
            run_id=run_id,
            label=entity.labels.get("en") or entity.local_id,
        )
        created_this_call[entity.source_uri] = result.entity_id
        outcomes.append(
            HmoItemUploadOutcome(
                entity.local_id, entity.source_uri, "created", result.entity_id,
            )
        )
        created += 1
        if audit_ctx is not None:
            await record_wikibase_write(
                db, audit_ctx,
                operation=OPERATION_CREATE,
                target_kind=TARGET_ITEM,
                target_key=entity.source_uri,
                wikibase_id=result.entity_id,
            )

    return outcomes, created_this_call, created, skipped, failed, cancelled


async def _pass_two_link(
    db: AsyncSession,
    entities: list[ResolvedWikibaseEntity],
    local_id_to_source_uri: dict[str, str],
    known_qids: dict[str, str],
    *,
    writer: Any | None,
    dry_run: bool,
    audit_ctx: WikibaseAuditContext | None = None,
    on_progress: Callable[[int, int, str], Awaitable[None]] | None = None,
    should_cancel: Callable[[], Awaitable[bool]] | None = None,
    total: int = 0,
    processed_offset: int = 0,
) -> tuple[list[HmoDeferredLinkOutcome], int, int, int, bool]:
    outcomes: list[HmoDeferredLinkOutcome] = []
    linked = failed = unresolved = 0
    cancelled = False
    seen_links = 0
    total_links = total - processed_offset

    for entity in entities:
        if cancelled:
            break
        source_qid = known_qids.get(entity.source_uri)
        for link in entity.deferred_links:
            if not dry_run and should_cancel is not None and await should_cancel():
                cancelled = True
                break
            seen_links += 1
            if not dry_run and on_progress is not None:
                await on_progress(
                    processed_offset + seen_links, total,
                    f"{seen_links}/{total_links} item links added",
                )
            target_source_uri = local_id_to_source_uri.get(link.target_local_id)
            target_qid = known_qids.get(target_source_uri) if target_source_uri else None

            if source_qid is None or target_qid is None:
                outcomes.append(
                    HmoDeferredLinkOutcome(
                        link.source_local_id, link.property_id, link.target_local_id,
                        "unresolved",
                    )
                )
                unresolved += 1
                continue

            if dry_run:
                outcomes.append(
                    HmoDeferredLinkOutcome(
                        link.source_local_id, link.property_id, link.target_local_id,
                        "would_link",
                    )
                )
                linked += 1
                continue

            claim = _build_wbi_claim(ResolvedClaim(link.property_id, "wikibase-item", target_qid))
            result = await asyncio.to_thread(writer.add_claim, source_qid, claim)
            if result.status == "failed":
                outcomes.append(
                    HmoDeferredLinkOutcome(
                        link.source_local_id, link.property_id, link.target_local_id,
                        "failed", message=result.message,
                    )
                )
                failed += 1
                if audit_ctx is not None:
                    await record_wikibase_write(
                        db, audit_ctx,
                        operation=OPERATION_FAILED,
                        target_kind=TARGET_CLAIM,
                        target_key=f"{source_qid}|{link.property_id}|{target_qid}",
                        outcome_message=result.message,
                    )
                continue
            outcomes.append(
                HmoDeferredLinkOutcome(
                    link.source_local_id, link.property_id, link.target_local_id, "linked",
                )
            )
            linked += 1
            if audit_ctx is not None:
                await record_wikibase_write(
                    db, audit_ctx,
                    operation=OPERATION_CREATE,
                    target_kind=TARGET_CLAIM,
                    target_key=f"{source_qid}|{link.property_id}|{target_qid}",
                    wikibase_id=source_qid,
                )

    return outcomes, linked, failed, unresolved, cancelled


def _build_wbi_claim(claim: ResolvedClaim) -> Any:
    """Convert a JSON-safe :class:`ResolvedClaim` into a live
    ``wikibaseintegrator.datatypes`` claim object."""
    from wikibaseintegrator import datatypes  # noqa: PLC0415

    if claim.datatype == "wikibase-item":
        return datatypes.Item(prop_nr=claim.property_id, value=claim.value)
    if claim.datatype == "string":
        return datatypes.String(prop_nr=claim.property_id, value=claim.value)
    if claim.datatype == "url":
        return datatypes.URL(prop_nr=claim.property_id, value=claim.value)
    if claim.datatype == "external-id":
        return datatypes.ExternalID(prop_nr=claim.property_id, value=claim.value)
    if claim.datatype == "monolingualtext":
        v = claim.value
        return datatypes.MonolingualText(
            prop_nr=claim.property_id, text=v["text"], language=v.get("language", "en"),
        )
    if claim.datatype == "time":
        v = claim.value
        return datatypes.Time(
            prop_nr=claim.property_id, time=v["time"], precision=v.get("precision", 9),
        )
    if claim.datatype == "quantity":
        return datatypes.Quantity(prop_nr=claim.property_id, amount=claim.value["amount"])
    raise ValueError(f"unsupported claim datatype: {claim.datatype!r}")


async def _load_run_instance_mappings(
    db: AsyncSession, run_id: uuid.UUID,
) -> dict[str, str]:
    """source_uri -> live Wikibase id for this run's already-uploaded instances."""
    rows = (
        await db.execute(
            select(
                WikibaseEntityMapping.ontology_uri, WikibaseEntityMapping.wikibase_id,
            ).where(
                WikibaseEntityMapping.run_id == run_id,
                WikibaseEntityMapping.entity_kind == ENTITY_KIND_INSTANCE,
            )
        )
    ).all()
    return {uri: wikibase_id for uri, wikibase_id in rows}


async def _record_instance_mapping(
    db: AsyncSession,
    *,
    source_uri: str,
    wikibase_id: str,
    run_id: uuid.UUID,
    label: str,
) -> None:
    db.add(
        WikibaseEntityMapping(
            ontology_uri=source_uri,
            entity_kind=ENTITY_KIND_INSTANCE,
            wikibase_id=wikibase_id,
            run_id=run_id,
            label=label,
        )
    )
    await db.commit()
