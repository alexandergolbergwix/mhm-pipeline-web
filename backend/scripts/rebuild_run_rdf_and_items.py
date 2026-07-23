"""Rebuild the RDF graph + HMO Wikibase item drafts for one run.

Mirrors ``POST /runs/{run_id}/rdf/build`` followed by
``POST /runs/{run_id}/hmo-studio/build-items?force_rebuild=true`` exactly
(same DB queries, same pipeline calls, same cache-busting + Postgres
write-through), invoked directly against the database instead of over
HTTP. Written for the Rule W-43 fix (SHACL ``inference="none"`` +
graph_builder cleanup) so an already-built run can be regenerated with
the fixed code without needing a curator session cookie.

Invoke (from inside ``backend/``)::

    python -m scripts.rebuild_run_rdf_and_items <run_id>

Or on Heroku::

    heroku run --app mhm-pipeline-web -- \\
        bash -lc "cd backend && python -m scripts.rebuild_run_rdf_and_items <run_id>"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any


def _ensure_app_on_syspath() -> None:
    backend_dir = Path(__file__).resolve().parent.parent
    backend_str = str(backend_dir)
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)


_ensure_app_on_syspath()

from sqlalchemy import select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.models.extraction_approval import ExtractionApproval  # noqa: E402
from app.models.rdf_artifact import RdfArtifact  # noqa: E402
from app.models.run import AuthorityMatch, RdfTripleOverride, RunRecord  # noqa: E402
from app.pipeline import hmo_item_build  # noqa: E402
from app.pipeline import hmo_item_upload  # noqa: E402
from app.models.run import Run  # noqa: E402
from app.models.wikibase_cloud_write import CHANNEL_ITEM_UPLOAD  # noqa: E402
from app.services.wikibase_audit import WikibaseAuditContext  # noqa: E402
from app.services.wikibase_credentials import build_server_wikibase_writer  # noqa: E402
from app.pipeline.rdf_build import (  # noqa: E402
    RdfBuildOptions,
    build_rdf_graph,
    normalise_matches,
    rdf_output_path_for_run,
    validate_with_shacl,
)


async def rebuild(run_id: uuid.UUID, *, upload: bool = False) -> None:
    async with session_scope() as db:
        records = (
            await db.execute(
                select(RunRecord)
                .where(RunRecord.run_id == run_id)
                .order_by(RunRecord.control_number.asc())
            )
        ).scalars().all()
        if not records:
            print(f"Run {run_id} has no MARC records — nothing to rebuild.")
            return

        matches = (
            await db.execute(
                select(AuthorityMatch)
                .where(AuthorityMatch.run_id == run_id)
                .where(AuthorityMatch.approved.is_(True))
            )
        ).scalars().all()

        ner_rows = (
            await db.execute(
                select(ExtractionApproval)
                .where(ExtractionApproval.run_id == run_id)
                .where(ExtractionApproval.approved.is_(True))
            )
        ).scalars().all()
        entities_by_cn: dict[str, list[dict[str, Any]]] = {}
        for r in ner_rows:
            entities_by_cn.setdefault(r.control_number, []).append({
                "text":             r.override_text or r.text,
                "type":             (r.override_type or r.type or "").upper(),
                "role":             (r.override_role or r.role or "").upper(),
                "source":           r.source,
                "start":            int(r.start or 0),
                "end":              int(r.end or 0),
                "confidence":       r.confidence,
                "model_confidence": r.model_confidence,
            })

        marc_records = [dict(r.marc) for r in records]
        authority_matches = normalise_matches(matches)
        kima_places_by_cn: dict[str, dict[str, str]] = {}
        for rec in marc_records:
            cn = str(rec.get("_control_number") or rec.get("control_number") or "")
            kp = rec.get("kima_places")
            if cn and isinstance(kp, dict) and kp:
                kima_places_by_cn[cn.strip("\"'")] = kp

        opts = RdfBuildOptions(
            add_epistemological_status=True,
            add_cataloging_view=True,
            add_philological_overlay=True,
        )

        overrides_rows = (
            await db.execute(
                select(RdfTripleOverride).where(RdfTripleOverride.run_id == run_id)
            )
        ).scalars().all()
        overrides = [
            {
                "subject_uri": r.subject_uri,
                "predicate_uri": r.predicate_uri,
                "new_value": r.new_value,
                "new_datatype": r.new_datatype,
                "new_lang": r.new_lang,
            }
            for r in overrides_rows
        ]

        out_path = rdf_output_path_for_run(str(run_id))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        result = await build_rdf_graph(
            marc_records=marc_records,
            authority_matches=authority_matches,
            entities_by_cn=entities_by_cn,
            output_path=out_path,
            overrides=overrides,
            kima_places_by_cn=kima_places_by_cn,
            build_options=opts,
        )
        print(
            f"RDF rebuilt: {result.manuscripts_count} manuscripts, "
            f"{result.triples_count} triples -> {out_path}"
        )

        ttl_text = out_path.read_text(encoding="utf-8")
        existing = await db.get(RdfArtifact, run_id)
        if existing:
            existing.ttl_content = ttl_text
            existing.triples_count = result.triples_count
            existing.manuscripts_count = result.manuscripts_count
        else:
            db.add(RdfArtifact(
                run_id=run_id,
                ttl_content=ttl_text,
                triples_count=result.triples_count,
                manuscripts_count=result.manuscripts_count,
            ))
        await db.commit()

        for cache_file in out_path.parent.glob("graph_*.json"):
            cache_file.unlink(missing_ok=True)
        for cache_file in out_path.parent.glob("graph_viewport_*.json"):
            cache_file.unlink(missing_ok=True)
        try:
            from app.pipeline.research_graph import invalidate_cache as _inval
            _inval(str(run_id))
        except Exception:  # noqa: BLE001
            pass

        report = await validate_with_shacl(out_path)
        print(
            f"SHACL validation: conforms={report.conforms}, "
            f"violations={len(report.violations)}"
        )
        if report.violations:
            for v in report.violations[:20]:
                print("  -", v)

        item_result = await hmo_item_build.build_items_for_run(
            db, run_id, out_path, force_rebuild=True,
        )
        await db.commit()
        print(
            f"HMO items rebuilt: {item_result.entity_count} entities, "
            f"{item_result.deferred_link_count} deferred links, "
            f"{item_result.skipped_statement_count} skipped statements, "
            f"from_cache={item_result.from_cache}"
        )

        if not upload:
            return

        run = await db.get(Run, run_id)
        if run is None:
            raise RuntimeError(f"Run {run_id} does not exist")
        writer = build_server_wikibase_writer()
        result = await hmo_item_upload.upload_items_for_run(
            db,
            run_id,
            writer=writer,
            dry_run=False,
            update_existing=True,
            allow_shacl_errors=False,
            audit_ctx=WikibaseAuditContext(
                actor_user_id=run.created_by,
                project_id=run.project_id,
                run_id=run_id,
                channel=CHANNEL_ITEM_UPLOAD,
            ),
        )
        await db.commit()
        print(
            "HMO live upload/read-back: "
            f"created={result.created} updated={result.updated} "
            f"skipped={result.skipped} failed={result.failed} "
            f"blocked={result.blocked} linked={result.linked} "
            f"unresolved_links={result.unresolved_links}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id", type=uuid.UUID)
    parser.add_argument(
        "--upload",
        action="store_true",
        help="update existing Wikibase items and read every live item back",
    )
    args = parser.parse_args()
    asyncio.run(rebuild(args.run_id, upload=args.upload))


if __name__ == "__main__":
    main()
