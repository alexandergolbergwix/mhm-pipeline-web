"""Fail-closed readiness gate for canonical HMO migration."""
from __future__ import annotations
import argparse, asyncio, json, sys, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import SessionLocal
from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.hmo_canonical_entity import HmoCanonicalEntity
from app.models.run import AuthorityMatch
from app.pipeline.hmo_authority_gate import validate_authority_rows
from app.pipeline.hmo_canonical_readiness import evaluate
from sqlalchemy import select

async def inspect(run_id: uuid.UUID | None) -> dict[str, object]:
    async with SessionLocal() as db:
        q = select(HmoStudioItemCache)
        if run_id: q = q.where(HmoStudioItemCache.run_id == run_id)
        caches = (await db.execute(q)).scalars().all()
        readiness_results = []
        for cache in caches:
            rows = (await db.execute(select(HmoCanonicalEntity).where(HmoCanonicalEntity.run_id == cache.run_id))).scalars().all()
            matches = (await db.execute(select(AuthorityMatch).where(AuthorityMatch.run_id == cache.run_id))).scalars().all()
            authority = validate_authority_rows(matches)
            readiness_results.append({
                "run_id": str(cache.run_id),
                **evaluate(
                    list(cache.resolved_entities or []),
                    rows,
                    authority_conflicts=[*authority["conflicts"], *authority["invalid"]],
                ).to_dict(),
            })
        ready = bool(readiness_results) and all(result["ready"] for result in readiness_results)
        return {
            "runs": len(readiness_results),
            "items": sum(result["expected_item_count"] for result in readiness_results),
            "canonical_rows": sum(result["durable_row_count"] for result in readiness_results),
            "missing": sum(len(result["missing_rows"]) for result in readiness_results),
            "invalid": [item for result in readiness_results for item in result["malformed_rows"]],
            "duplicates": [
                result["run_id"] for result in readiness_results
                if result["duplicate_local_ids"]
                or result["duplicate_source_uris"]
                or result["duplicate_wikibase_qids"]
            ],
            "ready": ready,
            "results": readiness_results,
        }

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--run-id", type=uuid.UUID); args = parser.parse_args()
    result = asyncio.run(inspect(args.run_id)); print(json.dumps(result, indent=2, sort_keys=True)); raise SystemExit(0 if result["ready"] else 1)
if __name__ == "__main__": main()
