"""Upload resolved HMO items to the Wikibase Cloud (Phase 5 of HMO
Wikibase Studio — see dev-docs/hmo-wikibase-studio-plan.md).

Two-pass, create-or-update:

* **Pass 1** creates every not-yet-uploaded instance (identified by its
  RDF source URI, unique per ``(ontology_uri, run_id)`` in
  ``wikibase_entity_mappings``) with its non-deferred claims, recording
  a mapping row immediately per success. An already-mapped instance is
  skipped by default; passing ``update_existing=True`` instead refreshes
  its labels/descriptions and merges in any new claims (via
  :meth:`converter.wikibase.cloud_client.WikibaseCloudWriter.update_item`
  — a curator-added statement not present in the current build is left
  untouched, never wiped).
* **Pass 2** resolves each entity's ``deferred_links`` (item -> item
  claims that needed both ends to have live QIDs) now that pass 1 (or a
  prior run) may have created the target — entries whose target never
  got created are reported as ``unresolved``, never silently dropped.
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
from app.models.wikibase_cloud_write import (
    OPERATION_ADOPT,
    OPERATION_CREATE,
    OPERATION_FAILED,
    OPERATION_SKIP,
    OPERATION_UPDATE,
    TARGET_CLAIM,
    TARGET_ITEM,
)
from app.models.wikibase_entity_mapping import ENTITY_KIND_INSTANCE, WikibaseEntityMapping
from app.pipeline.hmo_item_reconcile import (
    ReconciliationUnavailableError,
    reconcile_item,
    resolve_source_uri_pid,
)
from app.pipeline.hmo_item_shacl_gate import (
    blocking_shacl_issues,
    format_shacl_block_message,
    sanitize_wikibase_descriptions,
    sanitize_wikibase_labels,
)
from app.services.wikibase_audit import record_wikibase_write
from app.pipeline.hmo_canonical import canonical_entity_fingerprint
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
    # "created" | "updated" | "skipped" | "adopted" | "would_create" | "would_update"
    # | "would_block" | "blocked" | "failed"
    status: str
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
    blocked: int
    linked: int
    unresolved_links: int
    outcomes: list[HmoItemUploadOutcome] = field(default_factory=list)
    link_outcomes: list[HmoDeferredLinkOutcome] = field(default_factory=list)
    cancelled: bool = False
    updated: int = 0


async def upload_items_for_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    writer: Any | None,
    dry_run: bool = True,
    update_existing: bool = False,
    allow_shacl_errors: bool = False,
    audit_ctx: WikibaseAuditContext | None = None,
    on_progress: Callable[[int, int, str], Awaitable[None]] | None = None,
    should_cancel: Callable[[], Awaitable[bool]] | None = None,
) -> HmoItemUploadResult:
    """Upload the run's most recent item build. Raises
    :class:`ItemBuildMissingError` if ``build-items`` hasn't run yet.

    An already-mapped instance is skipped by default; pass
    ``update_existing=True`` to instead refresh its labels/descriptions
    and merge in any new claims (see :func:`_pass_one_create`).

    ``on_progress(processed, total, message)`` is awaited after every
    item create/update (pass 1) and deferred-link write (pass 2), with
    ``total`` covering both passes. ``should_cancel()`` is polled before
    each write for cooperative cancellation — a partial result with
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
    shacl_by_local_id = cache_row.shacl_report or {}
    existing = await _load_run_instance_mappings(db, run_id)
    local_id_to_source_uri = {e.local_id: e.source_uri for e in entities}
    total = len(entities) + sum(len(e.deferred_links) for e in entities)

    # Resolved once, not once per entity: the property id is schema-level
    # and constant for the whole run, so re-querying it per item (up to
    # ~7800 identical SELECTs on a large corpus) would only add redundant
    # DB round trips ahead of each item's slow external Wikibase Cloud
    # call — see reconcile_item's docstring for the idle-transaction risk
    # that pattern would reintroduce.
    reconcile_pid = None if dry_run else await resolve_source_uri_pid(db)

    (
        outcomes, created_this_call, created, skipped, failed, blocked, updated, cancelled,
    ) = await _pass_one_create(
        db, run_id, entities, existing, writer=writer, dry_run=dry_run,
        update_existing=update_existing,
        allow_shacl_errors=allow_shacl_errors,
        shacl_by_local_id=shacl_by_local_id,
        audit_ctx=audit_ctx,
        on_progress=on_progress, should_cancel=should_cancel, total=total,
        reconcile_pid=reconcile_pid,
    )
    known_qids = {**existing, **created_this_call}

    if not dry_run and writer is not None and known_qids:
        await _persist_live_canonical_state(db, cache_row, entities, known_qids, writer)

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
        blocked=blocked,
        linked=linked,
        unresolved_links=unresolved,
        outcomes=outcomes,
        link_outcomes=link_outcomes,
        cancelled=cancelled,
        updated=updated,
    )


async def _persist_live_canonical_state(db: AsyncSession, cache_row: HmoStudioItemCache, entities: list[ResolvedWikibaseEntity], known_qids: dict[str, str], writer: Any) -> None:
    snapshots: dict[str, dict[str, Any]] = {}
    for entity in entities:
        qid = known_qids.get(entity.local_id)
        if not qid:
            continue
        live = await asyncio.to_thread(writer.get_entity, qid)
        if not live:
            continue
        snapshot = {
            'local_id': entity.local_id, 'source_uri': entity.source_uri,
            'wikibase_id': qid, 'labels': dict(live.get('labels') or {}),
            'descriptions': dict(live.get('descriptions') or {}),
            'aliases': dict(live.get('aliases') or {}),
            'claims': list(live.get('claims') or []),
            'authority_evidence': list(entity.authority_evidence),
            'canonical_source': 'wikibase',
        }
        snapshot['source_fingerprint'] = canonical_entity_fingerprint(snapshot)
        snapshots[entity.local_id] = snapshot
    if not snapshots:
        return
    cache_row.resolved_entities = [
        {**raw, 'canonical_live': snapshots[str(raw.get('local_id') or '')]}
        if str(raw.get('local_id') or '') in snapshots else raw
        for raw in cache_row.resolved_entities
    ]
    await db.flush()


async def _pass_one_create(
    db: AsyncSession,
    run_id: uuid.UUID,
    entities: list[ResolvedWikibaseEntity],
    existing: dict[str, str],
    *,
    writer: Any | None,
    dry_run: bool,
    update_existing: bool = False,
    allow_shacl_errors: bool = False,
    shacl_by_local_id: dict[str, list[dict[str, Any]]] | None = None,
    audit_ctx: WikibaseAuditContext | None = None,
    on_progress: Callable[[int, int, str], Awaitable[None]] | None = None,
    should_cancel: Callable[[], Awaitable[bool]] | None = None,
    total: int = 0,
    reconcile_pid: str | None = None,
) -> tuple[list[HmoItemUploadOutcome], dict[str, str], int, int, int, int, int, bool]:
    outcomes: list[HmoItemUploadOutcome] = []
    created_this_call: dict[str, str] = {}
    created = skipped = failed = blocked = updated = 0
    cancelled = False
    shacl_index = shacl_by_local_id or {}

    for idx, entity in enumerate(entities):
        if not dry_run and should_cancel is not None and await should_cancel():
            cancelled = True
            break
        if not dry_run and on_progress is not None:
            await on_progress(idx + 1, total, f"{idx + 1}/{len(entities)} items uploaded")

        block_issues = blocking_shacl_issues(shacl_index.get(entity.local_id))
        if block_issues and not allow_shacl_errors:
            block_message = format_shacl_block_message(block_issues)
            block_status = "would_block" if dry_run else "blocked"
            outcomes.append(
                HmoItemUploadOutcome(
                    entity.local_id, entity.source_uri, block_status,
                    message=block_message,
                )
            )
            blocked += 1
            if not dry_run and audit_ctx is not None:
                await record_wikibase_write(
                    db, audit_ctx,
                    operation=OPERATION_FAILED,
                    target_kind=TARGET_ITEM,
                    target_key=entity.source_uri,
                    outcome_message=block_message,
                )
            continue

        if entity.source_uri in existing:
            existing_qid = existing[entity.source_uri]
            if not update_existing:
                outcomes.append(
                    HmoItemUploadOutcome(
                        entity.local_id, entity.source_uri, "skipped", existing_qid,
                    )
                )
                skipped += 1
                if audit_ctx is not None and not dry_run:
                    await record_wikibase_write(
                        db, audit_ctx,
                        operation=OPERATION_SKIP,
                        target_kind=TARGET_ITEM,
                        target_key=entity.source_uri,
                        wikibase_id=existing_qid,
                    )
                continue

            if dry_run:
                outcomes.append(
                    HmoItemUploadOutcome(
                        entity.local_id, entity.source_uri, "would_update", existing_qid,
                    )
                )
                updated += 1
                continue

            outcome = await push_single_item(
                db, run_id, entity,
                writer=writer, audit_ctx=audit_ctx,
                update_existing=update_existing,
                reconcile_pid=reconcile_pid,
                existing_qid=existing_qid,
                allow_shacl_errors=allow_shacl_errors,
                shacl_issues=shacl_index.get(entity.local_id),
            )
            outcomes.append(outcome)
            if outcome.status == "updated":
                updated += 1
            elif outcome.status == "failed":
                failed += 1
            elif outcome.status == "blocked":
                blocked += 1
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

        outcome = await push_single_item(
            db, run_id, entity,
            writer=writer, audit_ctx=audit_ctx,
            update_existing=update_existing,
            reconcile_pid=reconcile_pid,
            existing_qid=None,
            allow_shacl_errors=allow_shacl_errors,
            shacl_issues=shacl_index.get(entity.local_id),
        )
        outcomes.append(outcome)
        if outcome.status in ("created", "adopted"):
            created += 1
            if outcome.wikibase_id:
                created_this_call[entity.source_uri] = outcome.wikibase_id
        elif outcome.status == "failed":
            failed += 1
        elif outcome.status == "blocked":
            blocked += 1

    return outcomes, created_this_call, created, skipped, failed, blocked, updated, cancelled


def _prepare_entity_payload(
    entity: ResolvedWikibaseEntity,
) -> tuple[dict[str, str], dict[str, str]]:
    return (
        sanitize_wikibase_labels(dict(entity.labels)),
        sanitize_wikibase_descriptions(dict(entity.descriptions)),
    )


async def push_single_item(
    db: AsyncSession,
    run_id: uuid.UUID,
    entity: ResolvedWikibaseEntity,
    *,
    writer: Any,
    audit_ctx: WikibaseAuditContext | None,
    update_existing: bool,
    reconcile_pid: str | None,
    existing_qid: str | None,
    allow_shacl_errors: bool = False,
    shacl_issues: list[dict[str, Any]] | None = None,
) -> HmoItemUploadOutcome:
    """Create-or-update exactly one item on the live Wikibase Cloud, now.

    The per-entity body shared by the bulk two-pass upload's live path
    (:func:`_pass_one_create`, unchanged behaviour) and the single-item
    ``POST .../items/{local_id}/push`` endpoint, which lets a curator push
    one fixed item immediately instead of re-running the whole corpus
    upload with ``update_existing=True``. Always live — dry-run outcomes
    are computed inline by the caller and never reach this function.

    ``existing_qid`` is the source_uri's current ``wikibase_entity_mappings``
    row if one exists, or ``None`` for a not-yet-uploaded item.
    """
    block_issues = blocking_shacl_issues(shacl_issues)
    if block_issues and not allow_shacl_errors:
        block_message = format_shacl_block_message(block_issues)
        if audit_ctx is not None:
            await record_wikibase_write(
                db, audit_ctx,
                operation=OPERATION_FAILED,
                target_kind=TARGET_ITEM,
                target_key=entity.source_uri,
                wikibase_id=existing_qid,
                outcome_message=block_message,
            )
        return HmoItemUploadOutcome(
            entity.local_id, entity.source_uri, "blocked",
            existing_qid, message=block_message,
        )

    labels, descriptions = _prepare_entity_payload(entity)

    if existing_qid is not None:
        if not update_existing:
            if audit_ctx is not None:
                await record_wikibase_write(
                    db, audit_ctx,
                    operation=OPERATION_SKIP,
                    target_kind=TARGET_ITEM,
                    target_key=entity.source_uri,
                    wikibase_id=existing_qid,
                )
            return HmoItemUploadOutcome(
                entity.local_id, entity.source_uri, "skipped", existing_qid,
            )

        wbi_claims = [_build_wbi_claim(c) for c in entity.claims]
        result = await asyncio.to_thread(
            writer.update_item,
            existing_qid,
            labels=labels,
            descriptions=descriptions,
            claims=wbi_claims,
        )
        if result.entity_id is None or result.status == "failed":
            if audit_ctx is not None:
                await record_wikibase_write(
                    db, audit_ctx,
                    operation=OPERATION_FAILED,
                    target_kind=TARGET_ITEM,
                    target_key=entity.source_uri,
                    wikibase_id=existing_qid,
                    outcome_message=result.message,
                )
            return HmoItemUploadOutcome(
                entity.local_id, entity.source_uri, "failed",
                existing_qid, message=result.message,
            )

        if audit_ctx is not None:
            await record_wikibase_write(
                db, audit_ctx,
                operation=OPERATION_UPDATE,
                target_kind=TARGET_ITEM,
                target_key=entity.source_uri,
                wikibase_id=existing_qid,
            )
        return HmoItemUploadOutcome(
            entity.local_id, entity.source_uri, "updated", existing_qid,
        )

    try:
        reconcile = await reconcile_item(db, entity.source_uri, pid=reconcile_pid)
    except ReconciliationUnavailableError as exc:
        return HmoItemUploadOutcome(
            entity.local_id, entity.source_uri, "failed",
            message=f"reconcile unavailable: {exc}",
        )

    if reconcile.found and reconcile.wikibase_id:
        await _record_instance_mapping(
            db,
            source_uri=entity.source_uri,
            wikibase_id=reconcile.wikibase_id,
            run_id=run_id,
            label=labels.get("en") or entity.local_id,
        )
        if audit_ctx is not None:
            await record_wikibase_write(
                db, audit_ctx,
                operation=OPERATION_ADOPT,
                target_kind=TARGET_ITEM,
                target_key=entity.source_uri,
                wikibase_id=reconcile.wikibase_id,
                outcome_message=(
                    f"adopted via reconcile: {reconcile.message}"
                    if reconcile.message else "adopted via reconcile"
                ),
            )
        return HmoItemUploadOutcome(
            entity.local_id, entity.source_uri, "adopted", reconcile.wikibase_id,
            message=reconcile.message,
        )

    wbi_claims = [_build_wbi_claim(c) for c in entity.claims]
    result = await asyncio.to_thread(
        writer.create_item,
        labels=labels,
        descriptions=descriptions,
        claims=wbi_claims,
    )
    if result.entity_id is None:
        if audit_ctx is not None:
            await record_wikibase_write(
                db, audit_ctx,
                operation=OPERATION_FAILED,
                target_kind=TARGET_ITEM,
                target_key=entity.source_uri,
                outcome_message=result.message,
            )
        return HmoItemUploadOutcome(
            entity.local_id, entity.source_uri, "failed", message=result.message,
        )

    await _record_instance_mapping(
        db,
        source_uri=entity.source_uri,
        wikibase_id=result.entity_id,
        run_id=run_id,
        label=labels.get("en") or entity.local_id,
    )
    if audit_ctx is not None:
        await record_wikibase_write(
            db, audit_ctx,
            operation=OPERATION_CREATE,
            target_kind=TARGET_ITEM,
            target_key=entity.source_uri,
            wikibase_id=result.entity_id,
        )
    return HmoItemUploadOutcome(
        entity.local_id, entity.source_uri, "created", result.entity_id,
    )


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
        from converter.wikibase.label_sanitize import normalize_wikibase_language  # noqa: PLC0415

        v = claim.value
        return datatypes.MonolingualText(
            prop_nr=claim.property_id,
            text=v["text"],
            language=normalize_wikibase_language(v.get("language")),
        )
    if claim.datatype == "time":
        v = claim.value
        return datatypes.Time(
            prop_nr=claim.property_id, time=v["time"], precision=v.get("precision", 9),
        )
    if claim.datatype == "quantity":
        return datatypes.Quantity(prop_nr=claim.property_id, amount=claim.value["amount"])
    if claim.datatype == "boolean":
        from converter.wikibase.wbi_datatypes import Boolean  # noqa: PLC0415

        return Boolean(prop_nr=claim.property_id, value=bool(claim.value))
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
    # A corrupted/replayed upload can leave one live QID attached to multiple
    # source URIs. Never trust an ambiguous mapping: doing so updates the
    # wrong live item (e.g. Jerusalem resolving to an unrelated Q1389). Drop
    # every colliding QID so source-URI reconciliation can recover correctly.
    by_qid: dict[str, str] = {}
    collisions: set[str] = set()
    for uri, qid in rows:
        previous_uri = by_qid.get(qid)
        if previous_uri is not None and previous_uri != uri:
            collisions.add(qid)
        else:
            by_qid[qid] = uri
    return {uri: qid for uri, qid in rows if qid not in collisions}


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
