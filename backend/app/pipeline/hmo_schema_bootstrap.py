"""Live, ontology-driven Wikibase schema bootstrap (Phase 3 of HMO
Wikibase Studio — see dev-docs/hmo-wikibase-studio-plan.md).

Turns the offline drafts from :mod:`converter.wikibase.schema_bootstrap`
into real Wikibase Property/Item entities on ``mhm-hmo.wikibase.cloud``,
recording each result in ``wikibase_entity_mappings`` (schema rows have
``run_id IS NULL``) so re-running is idempotent: only ontology URIs
without a mapping row are created.

Properties are created before classes because a class's future
``instance of`` claims (Phase 5) reference properties by PID; v1 classes
are created with no claims of their own (Wikibase Cloud has no seeded
meta-class item to point ``instance of`` at yet).

Every successful live create commits its mapping row immediately —
resuming after a crash mid-batch never re-creates an already-live
entity.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wikibase_entity_mapping import (
    ENTITY_KIND_CLASS,
    ENTITY_KIND_PROPERTY,
    WikibaseEntityMapping,
)
from app.models.wikibase_cloud_write import (
    OPERATION_CREATE,
    OPERATION_FAILED,
    OPERATION_SKIP,
    TARGET_ITEM,
    TARGET_PROPERTY,
)
from app.services.wikibase_audit import record_wikibase_write

if TYPE_CHECKING:
    from app.services.wikibase_audit import WikibaseAuditContext

# Global (not per-run) state dir: the schema is one ontology, one
# Wikibase instance. This is also the eval-agent boundary for the
# "hmo_wikibase_schema" evaluator (see eval-agent/CLAUDE.md) — it reads
# this directory from disk, never importing backend Python.
_SCHEMA_STATE_ROOT = Path(__file__).resolve().parents[2] / "state" / "hmo_wikibase_schema"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SchemaBootstrapEntry:
    """Outcome for one ontology class/property in a bootstrap pass."""

    ontology_uri: str
    entity_kind: str
    label: str
    wikibase_id: str | None  # None for "would_create"/"failed"
    status: str  # "created" | "skipped" | "would_create" | "failed"
    message: str = ""


@dataclass(frozen=True)
class SchemaBootstrapResult:
    dry_run: bool
    created: int
    skipped: int
    failed: int
    # Populated only when dry_run=True — "created" stays 0 in a dry run
    # since nothing is actually written.
    would_create: int = 0
    entries: list[SchemaBootstrapEntry] = field(default_factory=list)


@dataclass(frozen=True)
class SchemaStatusResult:
    total_classes: int
    total_properties: int
    mapped_classes: int
    mapped_properties: int
    missing_sample: list[str] = field(default_factory=list)


async def bootstrap_schema(
    db: AsyncSession,
    *,
    writer: Any | None,
    dry_run: bool,
    on_progress: Callable[[int, int, str], Awaitable[None]] | None = None,
    should_cancel: Callable[[], Awaitable[bool]] | None = None,
    audit_ctx: WikibaseAuditContext | None = None,
) -> SchemaBootstrapResult:
    """Create every missing HMO ontology class/property on the Wikibase Cloud.

    ``writer`` must be a live :class:`WikibaseCloudWriter` when
    ``dry_run=False``; it is unused (and may be ``None``) when
    ``dry_run=True``.

    ``on_progress(processed, total, message)`` is awaited after every
    entry (live mode only — dry-run has no network calls to report
    progress on) so a caller running this inside a background job
    (``hmo_schema_bootstrap_job.py``) can update a job-progress row.
    ``should_cancel()`` is polled the same way to support cooperative
    cancellation; when it returns True the loop stops and the result
    reflects whatever was completed so far.
    """
    from converter.wikibase.ontology_schema_reader import read_hmo_schema  # noqa: PLC0415

    schema = await asyncio.to_thread(read_hmo_schema)
    existing = await _load_schema_mappings(db)

    # Properties first — item claims (Phase 4/5) reference them by PID.
    ordered = [
        (prop.uri, ENTITY_KIND_PROPERTY, prop.label, prop.description, prop.aliases, prop.datatype)
        for prop in schema.properties
    ] + [
        (cls.uri, ENTITY_KIND_CLASS, cls.label, cls.description, cls.aliases, None)
        for cls in schema.classes
    ]
    total = len(ordered)

    entries: list[SchemaBootstrapEntry] = []
    created = skipped = failed = would_create = 0

    for idx, (uri, kind, label, description, aliases, datatype) in enumerate(ordered):
        if not dry_run and should_cancel is not None and await should_cancel():
            break

        if uri in existing:
            entries.append(
                SchemaBootstrapEntry(uri, kind, label, existing[uri], "skipped")
            )
            skipped += 1
            if audit_ctx is not None and not dry_run:
                await record_wikibase_write(
                    db, audit_ctx,
                    operation=OPERATION_SKIP,
                    target_kind=_audit_target_kind(kind),
                    target_key=uri,
                    wikibase_id=existing[uri],
                )
        elif dry_run:
            entries.append(SchemaBootstrapEntry(uri, kind, label, None, "would_create"))
            would_create += 1
        else:
            outcome = await asyncio.to_thread(
                _create_live, writer, kind, label, description, aliases, datatype,
            )
            if outcome.entity_id is None:
                entries.append(
                    SchemaBootstrapEntry(uri, kind, label, None, "failed", outcome.message)
                )
                failed += 1
                if audit_ctx is not None:
                    await record_wikibase_write(
                        db, audit_ctx,
                        operation=OPERATION_FAILED,
                        target_kind=_audit_target_kind(kind),
                        target_key=uri,
                        outcome_message=outcome.message,
                    )
                logger.warning(
                    "Failed to create schema %s %s (%s): %s",
                    kind, label, uri, outcome.message,
                )
            else:
                await _record_mapping(
                    db, ontology_uri=uri, entity_kind=kind,
                    wikibase_id=outcome.entity_id, label=label, datatype=datatype,
                )
                entries.append(
                    SchemaBootstrapEntry(uri, kind, label, outcome.entity_id, "created")
                )
                created += 1
                if audit_ctx is not None:
                    await record_wikibase_write(
                        db, audit_ctx,
                        operation=OPERATION_CREATE,
                        target_kind=_audit_target_kind(kind),
                        target_key=uri,
                        wikibase_id=outcome.entity_id,
                    )

        if not dry_run and on_progress is not None:
            await on_progress(idx + 1, total, label)

    return SchemaBootstrapResult(
        dry_run=dry_run, created=created, skipped=skipped, failed=failed,
        would_create=would_create, entries=entries,
    )


async def schema_status(db: AsyncSession) -> SchemaStatusResult:
    """Ontology class/property counts vs. how many already have a live mapping."""
    from converter.wikibase.ontology_schema_reader import read_hmo_schema  # noqa: PLC0415

    schema = await asyncio.to_thread(read_hmo_schema)
    existing = await _load_schema_mappings(db)

    class_uris = {entry.uri for entry in schema.classes}
    property_uris = {entry.uri for entry in schema.properties}
    missing = sorted((class_uris | property_uris) - existing.keys())

    return SchemaStatusResult(
        total_classes=len(class_uris),
        total_properties=len(property_uris),
        mapped_classes=len(class_uris & existing.keys()),
        mapped_properties=len(property_uris & existing.keys()),
        missing_sample=missing[:10],
    )


def _audit_target_kind(entity_kind: str) -> str:
    return TARGET_PROPERTY if entity_kind == ENTITY_KIND_PROPERTY else TARGET_ITEM


def _create_live(
    writer: Any,
    entity_kind: str,
    label: str,
    description: str,
    aliases: list[str],
    datatype: str | None,
) -> Any:
    """Call the writer synchronously (invoked via ``asyncio.to_thread``).

    The HMO ontology only carries ``en``/``he`` labels, so non-English
    aliases are recorded under ``he`` — a reasonable default given the
    ontology's actual language pairing, not a general i18n mechanism.
    """
    labels = {"en": label}
    alias_map = {"he": aliases} if aliases else None
    if entity_kind == ENTITY_KIND_PROPERTY:
        return writer.create_property(
            labels=labels,
            descriptions={"en": description},
            datatype=datatype,
            aliases=alias_map,
            summary=f"mhm-pipeline-web: bootstrap property {label}",
        )
    return writer.create_item(
        labels=labels,
        descriptions={"en": description},
        aliases=alias_map,
        summary=f"mhm-pipeline-web: bootstrap class {label}",
    )


async def _load_schema_mappings(db: AsyncSession) -> dict[str, str]:
    """URI → live Wikibase id for every existing schema-level mapping row."""
    rows = (
        await db.execute(
            select(
                WikibaseEntityMapping.ontology_uri,
                WikibaseEntityMapping.wikibase_id,
            ).where(WikibaseEntityMapping.run_id.is_(None))
        )
    ).all()
    return {uri: wikibase_id for uri, wikibase_id in rows}


async def _record_mapping(
    db: AsyncSession,
    *,
    ontology_uri: str,
    entity_kind: str,
    wikibase_id: str,
    label: str,
    datatype: str | None,
) -> None:
    """Insert one mapping row and commit immediately (crash-safe resume)."""
    db.add(
        WikibaseEntityMapping(
            ontology_uri=ontology_uri,
            entity_kind=entity_kind,
            wikibase_id=wikibase_id,
            run_id=None,
            label=label,
            datatype=datatype,
        )
    )
    await db.commit()


def serialise_bootstrap_entry(entry: SchemaBootstrapEntry) -> dict[str, Any]:
    return asdict(entry)


def serialise_bootstrap_result(result: SchemaBootstrapResult) -> dict[str, Any]:
    return {
        "dry_run": result.dry_run,
        "created": result.created,
        "skipped": result.skipped,
        "failed": result.failed,
        "would_create": result.would_create,
        "entries": [serialise_bootstrap_entry(e) for e in result.entries],
    }


def bootstrap_result_from_mapping(raw: dict[str, Any]) -> SchemaBootstrapResult | None:
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list):
        return None
    entries = [
        SchemaBootstrapEntry(
            ontology_uri=str(e.get("ontology_uri") or ""),
            entity_kind=str(e.get("entity_kind") or ""),
            label=str(e.get("label") or ""),
            wikibase_id=e.get("wikibase_id") if isinstance(e.get("wikibase_id"), str) else None,
            status=str(e.get("status") or ""),
            message=str(e.get("message") or ""),
        )
        for e in entries_raw
        if isinstance(e, dict)
    ]
    return SchemaBootstrapResult(
        dry_run=bool(raw.get("dry_run")),
        created=int(raw.get("created") or 0),
        skipped=int(raw.get("skipped") or 0),
        failed=int(raw.get("failed") or 0),
        would_create=int(raw.get("would_create") or 0),
        entries=entries,
    )


async def load_last_bootstrap_report(db: AsyncSession) -> SchemaBootstrapResult | None:
    """Latest succeeded job result, else on-disk cache."""
    from sqlalchemy import desc, select  # noqa: PLC0415

    from app.models.run_job import (  # noqa: PLC0415
        JOB_KIND_HMO_SCHEMA_BOOTSTRAP,
        JOB_STATUS_SUCCEEDED,
        RunJob,
    )

    job = (
        await db.execute(
            select(RunJob)
            .where(RunJob.kind == JOB_KIND_HMO_SCHEMA_BOOTSTRAP)
            .where(RunJob.status == JOB_STATUS_SUCCEEDED)
            .order_by(desc(RunJob.finished_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if job is not None and isinstance(job.result, dict):
        parsed = bootstrap_result_from_mapping(job.result)
        if parsed is not None and parsed.entries:
            return parsed
    return load_cached_schema_bootstrap_report()


def load_cached_schema_bootstrap_report() -> SchemaBootstrapResult | None:
    """Read the last on-disk bootstrap report, if any."""
    report_path = _SCHEMA_STATE_ROOT / "hmo_wikibase_schema.json"
    if not report_path.is_file():
        return None
    try:
        raw = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list):
        return None
    entries = [
        SchemaBootstrapEntry(
            ontology_uri=str(e.get("ontology_uri") or ""),
            entity_kind=str(e.get("entity_kind") or ""),
            label=str(e.get("label") or ""),
            wikibase_id=e.get("wikibase_id") if isinstance(e.get("wikibase_id"), str) else None,
            status=str(e.get("status") or ""),
            message=str(e.get("message") or ""),
        )
        for e in entries_raw
        if isinstance(e, dict)
    ]
    return SchemaBootstrapResult(
        dry_run=bool(raw.get("dry_run")),
        created=int(raw.get("created") or 0),
        skipped=int(raw.get("skipped") or 0),
        failed=int(raw.get("failed") or 0),
        would_create=int(raw.get("would_create") or 0),
        entries=entries,
    )


def cache_schema_bootstrap_report(result: SchemaBootstrapResult) -> Path:
    """Persist the bootstrap report where the eval-agent's
    ``hmo_wikibase_schema`` evaluator can read it from disk.

    Writes ``hmo_wikibase_schema.json`` (the report itself) plus an
    empty ``marc_extracted.json`` alongside it — schema entries have no
    MARC correlation, so the placeholder is genuinely empty, not a stub
    for future population; it exists only because
    ``eval_agent.ingest.pipeline_run.discover()`` requires the file.
    """
    _SCHEMA_STATE_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = _SCHEMA_STATE_ROOT / "hmo_wikibase_schema.json"
    report_path.write_text(
        json.dumps(serialise_bootstrap_result(result), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    marc_path = _SCHEMA_STATE_ROOT / "marc_extracted.json"
    if not marc_path.exists():
        marc_path.write_text("[]\n", encoding="utf-8")
    return report_path
