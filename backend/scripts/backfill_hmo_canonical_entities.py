"""Audit/backfill durable canonical HMO rows from live read-back snapshots."""
from __future__ import annotations
import argparse, asyncio, json, uuid, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import delete, select
from app.db import SessionLocal
from app.models.hmo_canonical_entity import HmoCanonicalEntity
from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.wikibase_entity_mapping import ENTITY_KIND_INSTANCE, WikibaseEntityMapping
from app.pipeline.hmo_canonical import normalize_live_entity
from app.pipeline.hmo_item_upload import _persist_live_canonical_state
from app.services.wikibase_credentials import build_server_wikibase_writer
from converter.wikibase.resolved_models import ResolvedWikibaseEntity

async def run(run_id: uuid.UUID | None, apply: bool, live_readback: bool = False) -> dict[str, object]:
    async with SessionLocal() as db:
        q = select(HmoStudioItemCache)
        if run_id is not None:
            q = q.where(HmoStudioItemCache.run_id == run_id)
        caches = (await db.execute(q)).scalars().all()
        report = {"runs": 0, "candidates": 0, "written": 0, "live_readbacks": 0, "missing_live": 0, "invalid": [], "duplicates": []}
        writer = build_server_wikibase_writer() if live_readback else None
        for cache in caches:
            report["runs"] += 1
            seen: set[str] = set(); snapshots = []
            for raw in cache.resolved_entities or []:
                live = raw.get("canonical_live")
                if not live:
                    report["missing_live"] += 1
                    continue
                try:
                    entity = normalize_live_entity(live)
                except (TypeError, ValueError, KeyError) as exc:
                    report["invalid"].append({"run_id": str(cache.run_id), "local_id": raw.get("local_id"), "error": str(exc)})
                    continue
                if entity.local_id in seen:
                    report["duplicates"].append({"run_id": str(cache.run_id), "local_id": entity.local_id})
                    continue
                seen.add(entity.local_id); snapshots.append(entity)
            report["candidates"] += len(snapshots)
            if live_readback and writer is not None:
                entities = [ResolvedWikibaseEntity.from_dict(raw) for raw in cache.resolved_entities or []]
                mappings = (await db.execute(select(WikibaseEntityMapping).where(WikibaseEntityMapping.run_id == cache.run_id, WikibaseEntityMapping.entity_kind == ENTITY_KIND_INSTANCE))).scalars().all()
                known_qids = {str(row.ontology_uri): str(row.wikibase_id) for row in mappings}
                await _persist_live_canonical_state(db, cache, entities, known_qids, writer)
                await db.commit()
                count = len((await db.execute(select(HmoCanonicalEntity).where(HmoCanonicalEntity.run_id == cache.run_id))).scalars().all())
                report["live_readbacks"] += count
                continue
            if apply and snapshots:
                await db.execute(delete(HmoCanonicalEntity).where(HmoCanonicalEntity.run_id == cache.run_id))
                for entity in snapshots:
                    snapshot = entity.to_dict()
                    db.add(HmoCanonicalEntity(
                        run_id=cache.run_id,
                        local_id=entity.local_id,
                        source_uri=entity.source_uri,
                        entity_type=str(snapshot.get("entity_type") or ""),
                        wikibase_id=entity.wikibase_id or "",
                        source_fingerprint=entity.source_fingerprint,
                        labels=entity.labels,
                        descriptions=entity.descriptions,
                        aliases=entity.aliases,
                        claims=entity.claims,
                        authority_evidence=entity.authority_evidence,
                        provenance={"control_numbers": list(snapshot.get("control_numbers") or []), "canonical_source": "wikibase"},
                        status="live",
                        snapshot=snapshot,
                    ))
                report["written"] += len(snapshots)
        if apply:
            await db.commit()
        return report

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=uuid.UUID)
    parser.add_argument("--apply", action="store_true", help="write rows; default is read-only")
    parser.add_argument("--live-readback", action="store_true", help="read every mapped Wikibase item and persist canonical snapshots")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.run_id, args.apply or args.live_readback, args.live_readback)), indent=2, sort_keys=True))
if __name__ == "__main__":
    main()
