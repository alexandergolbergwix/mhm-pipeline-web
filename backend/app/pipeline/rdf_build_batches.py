"""Keyset-paginated batch loading for the RDF build (batch-build R23).

The build used to materialise the whole corpus before mapping: four
unbounded ``.all()`` queries put every ``run_records.marc`` JSONB row
(twice — once as ORM entities, once as plain dicts) plus all approved
authority/NER rows in memory at once. For ultra-big runs that was the
OOM. The loader below pages ``run_records`` by the ``(run_id,
control_number)`` keyset and loads each page's approved matches + NER
rows with one ``IN`` query per table, so the process holds at most one
page of inputs. Short sessions per page keep no transaction open across
the long mapping thread work.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models.extraction_approval import ExtractionApproval
from app.models.run import AuthorityMatch, RdfTripleOverride, RunRecord
from app.pipeline.rdf_build import RdfBuildBatch, normalise_matches

DEFAULT_BUILD_BATCH_SIZE = 200


def _cn_keys(control_numbers: Iterable[str]) -> list[str]:
    """Distinct CN lookup keys — raw + quote-stripped variants.

    ``run_records.control_number`` and the CN embedded in the MARC dict
    may carry surrounding quotes from the upstream catalogue; match and
    NER lookups must try both (same behaviour as the former in-memory
    dict lookups).
    """
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


def entities_by_cn_from_rows(
    ner_rows: Iterable[ExtractionApproval],
) -> dict[str, list[dict[str, Any]]]:
    """Group approved NER rows by control number (curator overrides win)."""
    entities: dict[str, list[dict[str, Any]]] = {}
    for r in ner_rows:
        entities.setdefault(r.control_number, []).append({
            "text":             r.override_text or r.text,
            "type":             (r.override_type or r.type or "").upper(),
            "role":             (r.override_role or r.role or "").upper(),
            "source":           r.source,
            "start":            int(r.start or 0),
            "end":              int(r.end or 0),
            "confidence":       r.confidence,
            "model_confidence": r.model_confidence,
        })
    return entities


async def count_run_records(db: AsyncSession, run_id: uuid.UUID) -> int:
    return int((await db.execute(
        select(func.count()).select_from(RunRecord).where(RunRecord.run_id == run_id),
    )).scalar_one())


async def run_record_bounds(
    db: AsyncSession, run_id: uuid.UUID,
) -> tuple[int, str | None, str | None]:
    """``(count, first_cn, last_cn)`` via one server-side aggregate.

    Used for the resume signature + progress total without
    materialising the corpus (batch-build R23).
    """
    row = (await db.execute(
        select(
            func.count(),
            func.min(RunRecord.control_number),
            func.max(RunRecord.control_number),
        ).where(RunRecord.run_id == run_id),
    )).one()
    return int(row[0] or 0), row[1], row[2]


async def load_rdf_triple_overrides(
    db: AsyncSession, run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Curator per-triple overrides (small, curator-scoped — one query)."""
    rows = (
        await db.execute(
            select(RdfTripleOverride).where(RdfTripleOverride.run_id == run_id)
        )
    ).scalars().all()
    return [
        {
            "subject_uri": r.subject_uri,
            "predicate_uri": r.predicate_uri,
            "new_value": r.new_value,
            "new_datatype": r.new_datatype,
            "new_lang": r.new_lang,
        }
        for r in rows
    ]


def _assemble_batch(
    control_numbers: list[str],
    marc_by_cn: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Order MARC dicts by the requested CN slice + collect kima maps."""
    marc_records: list[dict[str, Any]] = []
    kima_places_by_cn: dict[str, dict[str, str]] = {}
    for cn in control_numbers:
        raw = marc_by_cn.get(cn)
        if raw is None:
            raw = marc_by_cn.get(cn.strip("\"'"))
        if raw is None:
            continue
        rec = dict(raw)
        marc_records.append(rec)
        rec_cn = str(rec.get("_control_number") or rec.get("control_number") or "")
        kp = rec.get("kima_places")
        if rec_cn and isinstance(kp, dict) and kp:
            kima_places_by_cn[rec_cn.strip("\"'")] = kp
    return marc_records, kima_places_by_cn


async def _load_slice_rows(
    db: AsyncSession, run_id: uuid.UUID, cn_keys: list[str],
) -> tuple[list[AuthorityMatch], list[ExtractionApproval], dict[str, dict[str, Any]]]:
    """One query per table for an explicit CN key set."""
    matches = (
        await db.execute(
            select(AuthorityMatch)
            .where(AuthorityMatch.run_id == run_id)
            .where(AuthorityMatch.approved.is_(True))
            .where(AuthorityMatch.control_number.in_(cn_keys))
            .order_by(AuthorityMatch.control_number.asc())
        )
    ).scalars().all()
    ner_rows = (
        await db.execute(
            select(ExtractionApproval)
            .where(ExtractionApproval.run_id == run_id)
            .where(ExtractionApproval.approved.is_(True))
            .where(ExtractionApproval.control_number.in_(cn_keys))
            .order_by(ExtractionApproval.control_number.asc())
        )
    ).scalars().all()
    marc_rows = (
        await db.execute(
            select(RunRecord.control_number, RunRecord.marc)
            .where(RunRecord.run_id == run_id)
            .where(RunRecord.control_number.in_(cn_keys))
        )
    ).all()
    marc_by_cn = {r.control_number: dict(r.marc) for r in marc_rows}
    return matches, ner_rows, marc_by_cn


async def load_record_slice(
    db: AsyncSession, run_id: uuid.UUID, control_numbers: list[str],
) -> RdfBuildBatch:
    """One batch of inputs for an explicit, CN-ordered slice.

    Used by the keyset iterator (whole-run builds) and by Modal shard
    containers (distributed builds) — identical input shape either way.
    """
    cn_keys = _cn_keys(control_numbers)
    matches, ner_rows, marc_by_cn = await _load_slice_rows(db, run_id, cn_keys)
    marc_records, kima_places_by_cn = _assemble_batch(control_numbers, marc_by_cn)
    return RdfBuildBatch(
        marc_records=marc_records,
        authority_matches=normalise_matches(matches),
        entities_by_cn=entities_by_cn_from_rows(ner_rows),
        kima_places_by_cn=kima_places_by_cn,
    )


async def _load_page(
    db: AsyncSession,
    run_id: uuid.UUID,
    cursor: str | None,
    batch_size: int,
) -> tuple[RdfBuildBatch, str | None]:
    rows = (
        await db.execute(
            select(RunRecord.control_number, RunRecord.marc)
            .where(RunRecord.run_id == run_id)
            .where(RunRecord.control_number > (cursor or ""))
            .order_by(RunRecord.control_number.asc())
            .limit(batch_size)
        )
    ).all()
    if not rows:
        return RdfBuildBatch(marc_records=[]), None
    control_numbers = [r.control_number for r in rows]
    matches, ner_rows, marc_by_cn = await _load_slice_rows(
        db, run_id, _cn_keys(control_numbers),
    )
    marc_records, kima_places_by_cn = _assemble_batch(control_numbers, marc_by_cn)
    batch = RdfBuildBatch(
        marc_records=marc_records,
        authority_matches=normalise_matches(matches),
        entities_by_cn=entities_by_cn_from_rows(ner_rows),
        kima_places_by_cn=kima_places_by_cn,
    )
    return batch, control_numbers[-1]


async def iter_rdf_build_batches(
    run_id: uuid.UUID,
    *,
    batch_size: int = DEFAULT_BUILD_BATCH_SIZE,
    db: AsyncSession | None = None,
) -> AsyncIterator[RdfBuildBatch]:
    """Yield keyset pages of build inputs in ``control_number`` order.

    Keyset pagination (``control_number > cursor``), not OFFSET, so page
    N never re-scans the previous pages and the composite-PK ordering is
    stable under concurrent ingest. Pass ``db`` to reuse the caller's
    session (router path); otherwise each page opens its own short
    session (job path — no idle-in-transaction during mapping).
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    cursor: str | None = None
    while True:
        if db is not None:
            batch, next_cursor = await _load_page(db, run_id, cursor, batch_size)
        else:
            async with session_scope() as session:
                batch, next_cursor = await _load_page(session, run_id, cursor, batch_size)
        if next_cursor is None:
            return
        yield batch
        cursor = next_cursor
