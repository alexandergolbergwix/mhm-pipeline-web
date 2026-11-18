"""Run authorized reference repairs one at a time with normal maxlag protection."""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.publication.reference_repair_queue import DEFAULT_WAIT_SECONDS
from scripts.execute_gila_repairs import wait_seconds


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=uuid.UUID, required=True)
    parser.add_argument("--build-job-id", type=uuid.UUID, required=True)
    parser.add_argument("--qids-from", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--heroku-app")
    parser.add_argument(
        "--wait-seconds",
        type=wait_seconds,
        help="Maximum wait per item. Default: 86400 (24 hours) with --apply; zero for a preview.",
    )
    args = parser.parse_args(argv)
    if args.wait_seconds and not args.apply:
        parser.error("--wait-seconds requires --apply")
    if args.wait_seconds is None:
        args.wait_seconds = DEFAULT_WAIT_SECONDS if args.apply else 0
    return args


def load_environment(app: str | None) -> None:
    if app is None:
        return
    executable = shutil.which("heroku")
    if not executable:
        raise ValueError("The Heroku CLI is unavailable.")
    for name in ("DATABASE_URL", "WIKIDATA_PUBLICATION_LIVE_TOKEN"):
        result = subprocess.run(  # noqa: S603 — resolved CLI; no shell; credentials stay in memory
            [executable, "config:get", name, "--app", app],
            capture_output=True,
            text=True,
            check=True,
            timeout=45,
        )
        if not result.stdout.strip():
            raise ValueError("A required server credential is absent.")
        os.environ[name] = result.stdout.strip()
    os.environ["PG_BOUNCER_URL"] = ""


async def _run(
    args,
    qids,
    *,
    before_item_hook=None,
    on_checkpoint=None,
    resume_results=(),
    on_event=None,
    dispose_engine=True,
    authorize_session=None,
    lag_probe=None,
):
    from sqlalchemy import select

    import converter.wikidata.item_builder  # noqa: F401 — initialize shared source parsers
    from app.db import engine, session_scope
    from app.models.project import Membership, Project
    from app.models.publication import PublicationWriteIntent
    from app.models.run import Run, RunRecord
    from app.models.run_job import RunJob
    from app.models.wikibase_cloud_write import WikibaseCloudWrite
    from app.pipeline.marc_verify_context import canonical_control_number
    from app.pipeline.wikidata_gila_execution import LiveRepairGateway
    from app.pipeline.wikidata_gila_repairs import entity_digest
    from app.pipeline.wikidata_reference_reader import ReferenceItemReader
    from app.publication.credentials import ServerPublicationCredentialResolver
    from app.publication.reference_repair_queue import (
        reference_source_controls,
        run_reference_repair_queue,
    )
    from app.publication.sql_repository import SqlAlchemyPublicationRepository
    from app.services.wikibase_audit import WikibaseAuditContext, record_wikibase_write
    from converter.wikidata.uploader import WikidataUploader

    try:
        async with session_scope() as db:
            build = await db.get(RunJob, args.build_job_id)
            run_row = await db.get(Run, args.run_id)
            if (
                not build
                or not run_row
                or build.run_id != args.run_id
                or build.status != "succeeded"
                or not build.created_by
            ):
                raise ValueError("The authenticated build receipt is absent.")
            actor, project_id = build.created_by, run_row.project_id

            async def access(session):
                if authorize_session is not None:
                    await authorize_session(session, actor)
                project = await session.get(Project, project_id)
                if not project:
                    raise ValueError("The project is absent.")
                if project.owner_id != actor:
                    member = (
                        await session.execute(
                            select(Membership).where(
                                Membership.project_id == project_id,
                                Membership.user_id == actor,
                            )
                        )
                    ).scalar_one_or_none()
                    if not member or member.role not in {"owner", "editor"}:
                        raise ValueError("The actor lacks current project access.")

            await access(db)
            material = await ServerPublicationCredentialResolver().resolve(
                f"wikidata:production:{actor}"
            )
            if (
                material.target.site != "www.wikidata.org"
                or material.target.environment != "production"
            ):
                raise ValueError("The configured credential has another target.")
            gateway = LiveRepairGateway(
                WikidataUploader(
                    token=material.secret,
                    allow_live=True,
                    mark_as_bot=False,
                ),
                read_item=ReferenceItemReader().fetch,
            )
            del material
            rows = (
                await db.execute(
                    select(RunRecord.control_number, RunRecord.marc).where(
                        RunRecord.run_id == args.run_id,
                    )
                )
            ).all()
            sources = {canonical_control_number(cn): raw for cn, raw in rows}
            if len(sources) != len(rows):
                raise ValueError("The source control numbers are ambiguous.")
            await db.rollback()

        async def before_item(qid):
            if before_item_hook is not None:
                await before_item_hook(qid)
            async with session_scope() as db:
                await access(db)
                unknown = (
                    await db.execute(
                        select(PublicationWriteIntent.id)
                        .where(
                            PublicationWriteIntent.target_qid == qid,
                            PublicationWriteIntent.state.in_(["pending", "outcome_unknown"]),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                prior = (
                    await db.execute(
                        select(WikibaseCloudWrite.outcome_message)
                        .where(
                            WikibaseCloudWrite.channel == "wikidata_repair",
                            WikibaseCloudWrite.target_key == qid,
                        )
                        .order_by(WikibaseCloudWrite.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                await db.rollback()
            if unknown or (
                prior
                and json.loads(prior).get("status")
                in {
                    "intent",
                    "uncertain",
                    "outcome_unknown",
                }
            ):
                raise ValueError("A prior uncertain write requires recovery.")

        async def source_check(entity, proposal):
            controls = reference_source_controls(proposal)
            if not controls <= sources.keys():
                raise ValueError("The reference source is outside the authorized run.")
            keys = [key for cn in sorted(controls) for key in (cn, f'"{cn}"')]
            async with session_scope() as db:
                await access(db)
                current_rows = (
                    await db.execute(
                        select(RunRecord.control_number, RunRecord.marc).where(
                            RunRecord.run_id == args.run_id,
                            RunRecord.control_number.in_(keys),
                        )
                    )
                ).all()
                await db.rollback()
            current = {canonical_control_number(cn): raw for cn, raw in current_rows}
            if (
                len(current_rows) != len(controls)
                or set(current) != controls
                or any(entity_digest(current[cn]) != entity_digest(sources[cn]) for cn in controls)
            ):
                raise ValueError("The exact source changed before the repair.")
            return {
                "source_control_numbers": sorted(controls),
                "source_sha256": {cn: entity_digest(current[cn]) for cn in sorted(controls)},
                "run_id": str(args.run_id),
                "project_id": str(project_id),
                "actor_user_id": str(actor),
                "scope": "exact repeated references only",
            }

        context = WikibaseAuditContext(
            actor_user_id=actor,
            channel="wikidata_repair",
            project_id=project_id,
            run_id=args.run_id,
        )

        async def journal(receipt):
            operation = (
                "verified"
                if receipt["corrected"]
                else "repair_retry"
                if receipt["status"] == "pre_send_retryable"
                else "uncertain"
                if receipt["status"] == "outcome_unknown"
                else "blocked"
            )
            async with session_scope() as db:
                await record_wikibase_write(
                    db,
                    context,
                    operation=operation,
                    target_kind="item",
                    target_key=receipt["qid"],
                    wikibase_id=receipt["qid"],
                    outcome_message=json.dumps(receipt, sort_keys=True),
                    raise_on_error=True,
                )

        async with session_scope() as db:
            return await run_reference_repair_queue(
                qids,
                gateway,
                source_check,
                SqlAlchemyPublicationRepository(db),
                state_dir=args.state_dir,
                actor_id=str(actor),
                run_id=str(args.run_id),
                project_id=str(project_id),
                apply=args.apply,
                wait_seconds=args.wait_seconds,
                before_item=before_item,
                on_receipt=journal,
                on_event=on_event or (lambda event: print(json.dumps(event), flush=True)),
                on_checkpoint=on_checkpoint,
                resume_results=resume_results,
                lag_probe=lag_probe,
            )
    finally:
        if dispose_engine:
            await engine.dispose()


async def run(args, qids, **kwargs):
    if args.apply and (args.state_dir / "backend-handoff.json").exists():
        raise ValueError("Use the backend administrator controls after the handoff.")
    from app.publication.reference_queue_lease import direct_reference_queue_lease

    if args.apply:
        async with direct_reference_queue_lease(args.run_id):
            return await _run(args, qids, **kwargs)
    return await _run(args, qids, **kwargs)


def main(argv=None):
    args = arguments(argv)
    from app.publication.reference_repair_queue import candidate_qids

    try:
        qids = candidate_qids(json.loads(args.qids_from.read_text()))
        load_environment(args.heroku_app)
        result = asyncio.run(run(args, qids))
    except KeyboardInterrupt:
        print(
            json.dumps(
                {"status": "interrupted", "resume": "Use the same input and state directory."}
            )
        )
        raise SystemExit(130) from None
    except Exception as error:
        print(json.dumps({"status": "blocked", "error_type": type(error).__name__}))
        raise SystemExit(2) from None
    exit_code = 0 if result["status"] == "complete" else 75 if result["status"] == "paused" else 2
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
