"""Keyset-paginated loading for the sharded Wikidata Studio build.

The sequential build materialises the whole corpus before mapping: four
unbounded ``.all()`` queries hold every ``run_records.marc`` JSONB row
plus all authority/NER rows in memory at once. The sharded build never
does that:

* the orchestrator streams control numbers + fingerprint inputs page by
  page (server-side cursors, one page in memory) and chunks the corpus
  into ``control_number`` slices for the Modal shard fan-out;
* each shard container loads only its slice with one ``IN`` query per
  table, exactly like ``rdf_build_batches.load_record_slice``.

The streamed fingerprint is byte-identical to
``wikidata_studio.compute_build_fingerprint`` — same inputs, same
normalisation, same sort — so the Postgres cache contract (one approval
tick changes the fingerprint) holds across both paths.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extraction_approval import ExtractionApproval
from app.models.item_override import WikidataItemOverride
from app.models.run import AuthorityMatch, RunRecord
from app.pipeline.ai_verdict_cache_common import strip_volatile_metadata
from app.pipeline.wikidata_studio import WIKIDATA_STUDIO_BUILD_SCHEMA, hmo_instance_qids_for_run

DEFAULT_SHARD_CNS = 500


def _hash16(obj: Any) -> str:
    import hashlib  # noqa: PLC0415
    import json  # noqa: PLC0415

    return hashlib.sha256(
        json.dumps(strip_volatile_metadata(obj), sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


async def list_run_control_numbers(
    db: AsyncSession, run_id: uuid.UUID,
) -> list[str]:
    """All control numbers for a run, CN-ordered (small — strings only)."""
    rows = (
        await db.execute(
            select(RunRecord.control_number)
            .where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )
    ).scalars().all()
    return [str(r) for r in rows]


async def _streamed_column_hashes(
    db: AsyncSession, run_id: uuid.UUID,
) -> tuple[list[tuple[str, str]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Stream records/matches/entities and accumulate fingerprint tuples.

    Server-side cursor streaming (``yield_per``) keeps memory at one
    partition; only the small hashed tuples accumulate. Sort order does
    not matter here — the fingerprint sorts the tuples at the end,
    exactly like ``compute_build_fingerprint``.
    """
    record_parts: list[tuple[str, str]] = []
    result = await db.stream(
        select(RunRecord.control_number, RunRecord.marc)
        .where(RunRecord.run_id == run_id)
        .order_by(RunRecord.control_number.asc()),
        execution_options={"yield_per": 500},
    )
    async for partition in result.partitions():
        for control_number, marc in partition:
            record_parts.append((str(control_number), _hash16(marc or {})))

    match_parts: list[tuple[Any, ...]] = []
    result = await db.stream(
        select(AuthorityMatch)
        .where(AuthorityMatch.run_id == run_id)
        .order_by(AuthorityMatch.id.asc()),
        execution_options={"yield_per": 500},
    )
    async for partition in result.partitions():
        for (m,) in partition:
            match_parts.append((
                str(m.id), m.approved, m.wikidata_qid or "", m.viaf_id or "",
                m.mazal_id or "", _hash16(m.payload or {}),
            ))

    entity_parts: list[tuple[Any, ...]] = []
    result = await db.stream(
        select(ExtractionApproval)
        .where(ExtractionApproval.run_id == run_id)
        .order_by(ExtractionApproval.id.asc()),
        execution_options={"yield_per": 500},
    )
    async for partition in result.partitions():
        for (e,) in partition:
            entity_parts.append((
                str(e.id), bool(e.approved),
                e.override_text or "", e.override_type or "", e.override_role or "",
            ))
    return record_parts, match_parts, entity_parts


async def compute_build_fingerprint_streamed(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    approved_only: bool,
) -> str:
    """SHA-256 fingerprint identical to the sequential build's.

    Override rows are curator-scoped (small) and load in one query;
    records/matches/entities stream. ``hmo_instance_qids`` joins the
    hash the same way — the CN list streams first (strings only).
    """
    import hashlib  # noqa: PLC0415
    import json  # noqa: PLC0415

    control_numbers = await list_run_control_numbers(db, run_id)
    hmo_instance_qids = await hmo_instance_qids_for_run(db, run_id, control_numbers)

    record_parts, match_parts, entity_parts = await _streamed_column_hashes(db, run_id)

    override_rows = (
        await db.execute(
            select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
        )
    ).scalars().all()
    override_parts = [
        (
            str(o.id), o.local_id,
            o.approved,
            _hash16({
                "labels": o.labels, "descriptions": o.descriptions,
                "aliases": o.aliases, "add_statements": o.add_statements,
                "remove_statements": o.remove_statements,
                "statement_edits": o.statement_edits,
            }),
        )
        for o in override_rows
    ]

    parts = {
        "build_schema": WIKIDATA_STUDIO_BUILD_SCHEMA,
        "approved_only": approved_only,
        "records": sorted(record_parts),
        "hmo_instance_qids": sorted((hmo_instance_qids or {}).items()),
        "matches": sorted(match_parts),
        "entities": sorted(entity_parts),
        "overrides": sorted(override_parts),
    }
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, default=str).encode()
    ).hexdigest()


def shard_slices(
    control_numbers: list[str], size: int = DEFAULT_SHARD_CNS,
) -> list[list[str]]:
    """CN slices per Modal shard (input order preserved for starmap)."""
    if size < 1:
        raise ValueError("shard_size must be >= 1")
    return [
        control_numbers[i: i + size]
        for i in range(0, len(control_numbers), size)
    ]


def _cn_keys(control_numbers: Iterable[str]) -> list[str]:
    """Distinct CN lookup keys — raw + quote-stripped variants."""
    keys: set[str] = set()
    for cn in control_numbers:
        cn = str(cn)
        if not cn:
            continue
        keys.add(cn)
        stripped = cn.strip("\"'")
        if stripped:
            keys.add(stripped)
    return sorted(keys)


async def load_wikidata_build_slice(
    db: AsyncSession,
    run_id: uuid.UUID,
    control_numbers: list[str],
    *,
    approved_only: bool,
) -> dict[str, Any]:
    """One shard's build inputs, loaded with one ``IN`` query per table.

    Same shapes as ``execute_studio_build`` feeds the sequential builder:
    MARC dicts, build-payload matches (approved-only aware), grouped
    entity dicts, and every curator override for the run.
    """
    from app.routers.wikidata_studio import (  # noqa: PLC0415
        _group_entity_rows,
        match_to_build_payload,
    )

    cn_keys = _cn_keys(control_numbers)

    marc_rows = (
        await db.execute(
            select(RunRecord.control_number, RunRecord.marc)
            .where(RunRecord.run_id == run_id)
            .where(RunRecord.control_number.in_(cn_keys))
            .order_by(RunRecord.control_number.asc())
        )
    ).all()
    marc_by_cn = {r.control_number: dict(r.marc) for r in marc_rows}
    marc_records: list[dict[str, Any]] = []
    for cn in control_numbers:
        raw = marc_by_cn.get(cn)
        if raw is None:
            raw = marc_by_cn.get(cn.strip("\"'"))
        if raw is not None:
            marc_records.append(raw)

    match_rows = (
        await db.execute(
            select(AuthorityMatch)
            .where(AuthorityMatch.run_id == run_id)
            .where(AuthorityMatch.control_number.in_(cn_keys))
            .order_by(AuthorityMatch.control_number.asc())
        )
    ).scalars().all()
    if approved_only:
        match_rows = [m for m in match_rows if m.approved]
    approved_matches = [match_to_build_payload(m) for m in match_rows]

    entity_rows = (
        await db.execute(
            select(ExtractionApproval)
            .where(ExtractionApproval.run_id == run_id)
            .where(ExtractionApproval.control_number.in_(cn_keys))
            .order_by(ExtractionApproval.control_number.asc())
        )
    ).scalars().all()

    override_rows = (
        await db.execute(
            select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
        )
    ).scalars().all()
    overrides = {
        r.local_id: {
            "labels":            r.labels,
            "descriptions":      r.descriptions,
            "aliases":           r.aliases,
            "add_statements":    r.add_statements,
            "remove_statements": r.remove_statements,
            "statement_edits":   r.statement_edits,
        }
        for r in override_rows
    }

    return {
        "marc_records": marc_records,
        "approved_matches": approved_matches,
        "entities_by_cn": _group_entity_rows(list(entity_rows), approved_only),
        "overrides": overrides,
        "hmo_instance_qids": await hmo_instance_qids_for_run(db, run_id, control_numbers),
    }
