"""Diagnostic: why does RDF show fewer manuscripts than run records?"""
import asyncio
import re
import sys
sys.path.insert(0, ".")

from sqlalchemy import select
from app.db import SessionLocal
from app.models.run import Run, RunRecord
from app.pipeline.rdf_build import _prepare_record_for_rdf, rdf_output_path_for_run


async def main() -> None:
    async with SessionLocal() as db:
        runs = (
            await db.execute(select(Run).order_by(Run.created_at.desc()).limit(3))
        ).scalars().all()
        for run in runs:
            records = (
                await db.execute(
                    select(RunRecord).where(RunRecord.run_id == run.id)
                )
            ).scalars().all()
            print(f"\nRun: {run.name!r}  ({run.id})")
            print(f"  DB records: {len(records)}")

            cn_uris: dict[str, list[str]] = {}
            missing_cn: int = 0
            for r in records:
                rec = _prepare_record_for_rdf(dict(r.marc))
                cn_raw = (
                    rec.get("_control_number")
                    or rec.get("control_number")
                    or rec.get("controlNumber")
                    or ""
                )
                cn = str(cn_raw)
                if not cn:
                    missing_cn += 1
                    cn = f"MISSING_{id(r)}"
                cn_uri = (
                    re.sub(r"[^\w.\-]", "_", cn.strip("\"'")).strip("_") or cn
                )
                cn_uris.setdefault(cn_uri, []).append(cn)

            dupes = {k: v for k, v in cn_uris.items() if len(v) > 1}
            print(f"  unique cn_uris: {len(cn_uris)}")
            print(f"  missing CN: {missing_cn}")
            print(f"  duplicate cn_uris: {len(dupes)}")
            for k, v in list(dupes.items())[:5]:
                print(f"    {k!r} → {v}")

            # Count from TTL if it exists
            ttl = rdf_output_path_for_run(str(run.id))
            if ttl.exists():
                import rdflib
                from rdflib.namespace import RDF
                g = rdflib.Graph()
                g.parse(str(ttl), format="turtle")
                ms_count = sum(
                    1 for _s, _p, o in g.triples((None, RDF.type, None))
                    if str(o).rsplit("/", 1)[-1].split("#")[-1]
                    in ("Manuscript", "F4_Manifestation_Singleton", "F3_Manifestation")
                )
                print(f"  TTL manuscripts: {ms_count}  (triples: {len(g)})")


asyncio.run(main())
