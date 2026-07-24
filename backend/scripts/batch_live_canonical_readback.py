"""Batch live Wikibase read-back into durable hmo_canonical_entities rows.

The all-at-once backfill holds a DB session open across thousands of
external Wikibase calls and Postgres kills the idle transaction. This
script commits per batch with a fresh session each time.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db import SessionLocal
from app.models.hmo_canonical_entity import HmoCanonicalEntity
from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.wikibase_entity_mapping import ENTITY_KIND_INSTANCE, WikibaseEntityMapping
from app.pipeline.hmo_canonical import assert_canonical_entities, canonical_snapshot_from_wikibase, normalize_live_entity
from app.services.wikibase_credentials import build_server_wikibase_writer
from converter.wikibase.resolved_models import ResolvedWikibaseEntity

logger = logging.getLogger(__name__)


async def _existing_local_ids(db, run_id: uuid.UUID) -> set[str]:
    rows = (
        await db.execute(
            select(HmoCanonicalEntity.local_id).where(HmoCanonicalEntity.run_id == run_id)
        )
    ).scalars().all()
    return set(rows)


async def _read_batch_snapshots(
    entities: list[ResolvedWikibaseEntity],
    known_qids: dict[str, str],
    property_uris: dict[str, str],
    writer: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    target_uris = {qid: source_uri for source_uri, qid in known_qids.items() if qid and qid != "Q_PENDING"}
    snapshots: list[dict[str, Any]] = []
    failed: list[str] = []
    for entity in entities:
        qid = known_qids.get(entity.source_uri)
        if not qid or qid == "Q_PENDING":
            failed.append(entity.local_id)
            continue
        try:
            live = await asyncio.wait_for(
                asyncio.to_thread(writer.get_entity, qid), timeout=90.0,
            )
        except asyncio.TimeoutError:
            live = None
        if not live:
            logger.warning("read-back missing for %s (%s)", entity.local_id, qid)
            failed.append(entity.local_id)
            continue
        live_id = str(live.get("id") or "").strip()
        if live_id and live_id != qid:
            raise RuntimeError(
                f"identity mismatch for {entity.local_id}: mapped {qid}, received {live_id}"
            )
        snapshot = canonical_snapshot_from_wikibase(
            live,
            local_id=entity.local_id,
            source_uri=entity.source_uri,
            authority_evidence=list(entity.authority_evidence),
            entity_type=entity.entity_type,
            control_numbers=list(entity.control_numbers),
            property_uris=property_uris,
            target_uris=target_uris,
        )
        snapshot["wikibase_id"] = qid
        snapshots.append(snapshot)
    return snapshots, failed


async def run_batch(
    run_id: uuid.UUID,
    *,
    batch_size: int,
    limit: int | None,
) -> dict[str, object]:
    writer = build_server_wikibase_writer()
    report = {
        "run_id": str(run_id),
        "batch_size": batch_size,
        "total_entities": 0,
        "already_done": 0,
        "processed": 0,
        "written": 0,
        "missing_mapping": 0,
        "readback_failed": 0,
        "batches": 0,
    }

    async with SessionLocal() as db:
        cache = (
            await db.execute(select(HmoStudioItemCache).where(HmoStudioItemCache.run_id == run_id))
        ).scalar_one_or_none()
        if cache is None:
            raise RuntimeError(f"no HmoStudioItemCache for run {run_id}")
        entities = [ResolvedWikibaseEntity.from_dict(raw) for raw in (cache.resolved_entities or [])]
        mappings = (
            await db.execute(
                select(WikibaseEntityMapping).where(
                    WikibaseEntityMapping.run_id == run_id,
                    WikibaseEntityMapping.entity_kind == ENTITY_KIND_INSTANCE,
                )
            )
        ).scalars().all()
        known_qids = {str(row.ontology_uri): str(row.wikibase_id) for row in mappings}
        property_rows = (
            await db.execute(
                select(WikibaseEntityMapping).where(
                    WikibaseEntityMapping.entity_kind == "property",
                    WikibaseEntityMapping.run_id.is_(None),
                )
            )
        ).scalars().all()
        property_uris = {row.wikibase_id: row.ontology_uri for row in property_rows}
        done = await _existing_local_ids(db, run_id)

    report["total_entities"] = len(entities)
    report["already_done"] = len(done)

    pending = [e for e in entities if e.local_id not in done]
    if limit is not None:
        pending = pending[:limit]

    for offset in range(0, len(pending), batch_size):
        chunk = pending[offset : offset + batch_size]
        if not chunk:
            break
        mapped = [e for e in chunk if known_qids.get(e.source_uri) not in {None, "", "Q_PENDING"}]
        report["missing_mapping"] += len(chunk) - len(mapped)
        if not mapped:
            report["processed"] += len(chunk)
            continue

        snapshots, failed = await _read_batch_snapshots(mapped, known_qids, property_uris, writer)
        report["readback_failed"] += len(failed)
        if not snapshots:
            report["processed"] += len(chunk)
            continue

        canonical_entities = [normalize_live_entity(snapshot) for snapshot in snapshots]
        assert_canonical_entities(canonical_entities)

        async with SessionLocal() as db:
            for snapshot in snapshots:
                db.add(HmoCanonicalEntity(
                    run_id=run_id,
                    local_id=str(snapshot["local_id"]),
                    source_uri=str(snapshot["source_uri"]),
                    entity_type=str(snapshot.get("entity_type") or ""),
                    wikibase_id=str(snapshot["wikibase_id"]),
                    source_fingerprint=str(snapshot["source_fingerprint"]),
                    labels=dict(snapshot.get("labels") or {}),
                    descriptions=dict(snapshot.get("descriptions") or {}),
                    aliases=dict(snapshot.get("aliases") or {}),
                    claims=list(snapshot.get("claims") or []),
                    authority_evidence=list(snapshot.get("authority_evidence") or []),
                    provenance={
                        "control_numbers": list(snapshot.get("control_numbers") or []),
                        "canonical_source": "wikibase",
                    },
                    status="live",
                    snapshot=snapshot,
                ))
            await db.commit()

        report["written"] += len(snapshots)
        report["processed"] += len(chunk)
        report["batches"] += 1
        print(
            f"batch {report['batches']}: wrote {len(snapshots)} "
            f"(total {report['written']}, processed {report['processed']}/{len(pending)})",
            flush=True,
        )

    async with SessionLocal() as db:
        report["durable_rows"] = len(await _existing_local_ids(db, run_id))
    return report


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=uuid.UUID, required=True)
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--limit", type=int, default=None, help="process at most N pending entities")
    args = parser.parse_args()
    result = asyncio.run(
        run_batch(args.run_id, batch_size=args.batch_size, limit=args.limit),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    expected = result["total_entities"]
    got = result.get("durable_rows", 0)
    raise SystemExit(0 if got >= expected else 1)


if __name__ == "__main__":
    main()
