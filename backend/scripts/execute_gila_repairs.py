"""Prepare or apply an exact, owned-manuscript Gila repair batch."""

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import shutil
import subprocess
import traceback
import uuid
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy import select


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def print_receipt(receipt: Mapping[str, object]) -> None:
    print(
        json.dumps(
            {
                key: receipt[key]
                for key in (
                    "qid",
                    "status",
                    "corrected",
                    "before_revision",
                    "after_revision",
                )
            }
        ),
        flush=True,
    )


async def run(args: argparse.Namespace, cookie: str) -> int:
    from app.auth.session import current_auth
    from app.db import session_scope
    from app.models.run import RunRecord
    from app.models.wikibase_cloud_write import WikibaseCloudWrite
    from app.pipeline.marc_verify_context import canonical_control_number
    from app.pipeline.wikidata_gila_execution import (
        LiveRepairGateway,
        execute_repair,
        source_colophon,
        validate_source_colophons,
    )
    from app.pipeline.wikidata_gila_repairs import build_gila_repair_proposals
    from app.routers.runs import _lookup_run_with_access
    from app.routers.wikidata_studio import _unwrap_user_secret
    from app.services.wikibase_audit import WikibaseAuditContext, record_wikibase_write
    from converter.wikidata.uploader import UnauthorisedModificationError, WikidataUploader

    run_id = uuid.UUID(args.run_id)
    async with session_scope() as db:
        auth = await current_auth(db=db, cookie_value=cookie)
        run_row = await _lookup_run_with_access(db, run_id, auth, write=True)
        token = await _unwrap_user_secret(db, auth, "wikidata")
        if not token:
            raise ValueError("The actor has no usable live Wikidata credential.")
        source_rows = (
            await db.execute(
                select(RunRecord.control_number, RunRecord.marc).where(
                    RunRecord.run_id == run_id,
                )
            )
        ).all()
        actor_id = auth.user.id
        project_id = run_row.project_id
        sources = {canonical_control_number(cn): dict(marc) for cn, marc in source_rows}
        await db.commit()
    del auth, cookie
    gateway = LiveRepairGateway(WikidataUploader(token=token, allow_live=True, mark_as_bot=False))
    del token
    receipts: list[dict[str, object]] = []
    ctx = WikibaseAuditContext(
        actor_user_id=actor_id, channel="wikidata_repair", project_id=project_id, run_id=run_id
    )
    expected_sources: dict[str, str] = {}

    async def source_check(
        entity: Mapping[str, object],
        proposal: Mapping[str, object],
    ) -> dict[str, object]:
        claims = entity.get("claims")
        rows = claims.get("P3959", []) if isinstance(claims, Mapping) else []
        controls = set()
        for row in rows:
            value = row.get("mainsnak", {}).get("datavalue", {}).get("value")
            if isinstance(value, str):
                controls.add(canonical_control_number(value))
        if len(controls) != 1 or not controls <= sources.keys():
            raise ValueError(
                "The repair target needs one exact source record in the authorized run."
            )
        cn = next(iter(controls))
        if expected_sources and digest(sources[cn]) != expected_sources.get(cn):
            raise ValueError("The source does not match the reviewed plan.")
        async with session_scope() as db:
            live_rows = (
                (
                    await db.execute(
                        select(RunRecord.marc).where(
                            RunRecord.run_id == run_id,
                            RunRecord.control_number.in_([cn, f'"{cn}"']),
                        )
                    )
                )
                .scalars()
                .all()
            )
            await db.rollback()
        if len(live_rows) != 1 or digest(live_rows[0]) != digest(sources[cn]):
            raise ValueError("The source changed before the repair.")
        if proposal:
            validate_source_colophons(proposal, entity, live_rows[0])
        return {
            "control_number": cn,
            "source_sha256": digest(live_rows[0]),
            "run_id": str(run_id),
            "project_id": str(project_id),
            "actor_user_id": str(actor_id),
        }

    async def journal(phase: str, receipt: dict[str, object]) -> None:
        receipts.append({"phase": phase, **receipt})
        write_json(Path(args.receipts), receipts)
        operation = "repair_intent" if phase == "intent" else str(receipt["status"])
        async with session_scope() as db:
            await record_wikibase_write(
                db,
                ctx,
                operation=operation,
                target_kind="item",
                target_key=str(receipt["qid"]),
                wikibase_id=str(receipt["qid"]),
                outcome_message=json.dumps(receipt, sort_keys=True),
                raise_on_error=True,
            )

    if args.apply:
        plan = json.loads(await asyncio.to_thread(Path(args.plan).read_text))
        if (
            plan.get("run_id") != str(run_id)
            or plan.get("actor_user_id") != str(actor_id)
            or plan.get("project_id") != str(project_id)
        ):
            raise ValueError("The plan does not match the authenticated actor and project.")
        expected_sources = plan["source_sha256"]
        authorized = frozenset(plan["authorized_qids"])
        for proposal in plan["proposals"]:
            qid = proposal["qid"]
            async with session_scope() as db:
                previous = (
                    await db.execute(
                        select(WikibaseCloudWrite)
                        .where(
                            WikibaseCloudWrite.channel == "wikidata_repair",
                            WikibaseCloudWrite.run_id == run_id,
                            WikibaseCloudWrite.target_key == qid,
                        )
                        .order_by(WikibaseCloudWrite.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                prior_outcome = "not_sent"
                if previous is not None:
                    state = json.loads(previous.outcome_message)
                    if state.get("status") in {"intent", "uncertain"}:
                        prior_outcome = "uncertain"
                    elif state.get("entity_sha256") == proposal["entity_sha256"] and state.get(
                        "corrected"
                    ):
                        prior_outcome = "applied"
                await db.rollback()
            receipt = await execute_repair(
                proposal,
                gateway,
                authorized_qids=authorized,
                source_check=source_check,
                journal=journal,
                apply=True,
                prior_outcome=prior_outcome,
            )
            print_receipt(receipt)
            if receipt["status"] != "verified":
                return 2
        return 0

    reviewed = json.loads(await asyncio.to_thread(Path(args.qids_from).read_text))
    if not isinstance(reviewed, list):
        raise ValueError("The reviewed proposal file must contain a list.")
    authorized = frozenset(
        str(row["qid"]) for row in reviewed if not args.only_qid or row["qid"] == args.only_qid
    )
    if not authorized:
        raise ValueError("The authorized batch is empty.")
    entities = {qid: await gateway.fetch(qid) for qid in sorted(authorized)}
    colophons = {}
    for qid, entity in entities.items():
        await source_check(entity, {})
        for claim in entity["claims"].get("P3959", []):
            cn = canonical_control_number(
                claim.get("mainsnak", {}).get("datavalue", {}).get("value")
            )
            text = source_colophon(sources[cn])
            if text:
                colophons[qid] = str(text)
    proposals = build_gila_repair_proposals(entities, colophons_by_qid=colophons)
    ready = []
    ready_proposals = []
    blocked = []
    for proposal in proposals:
        try:
            receipt = await execute_repair(
                proposal,
                gateway,
                authorized_qids=authorized,
                source_check=source_check,
                journal=journal,
            )
        except (ValueError, UnauthorisedModificationError) as error:
            failure = {
                "qid": proposal["qid"],
                "status": "blocked",
                "error_type": type(error).__name__,
            }
            blocked.append(failure)
            print(json.dumps(failure), flush=True)
            continue
        ready.append(receipt)
        ready_proposals.append(proposal)
        print_receipt(receipt)
    write_json(
        Path(args.plan),
        {
            "run_id": str(run_id),
            "actor_user_id": str(actor_id),
            "project_id": str(project_id),
            "source_sha256": {cn: digest(raw) for cn, raw in sources.items()},
            "authorized_qids": sorted(authorized),
            "proposals": ready_proposals,
            "preflight": ready,
            "blocked": blocked,
        },
    )
    print(
        json.dumps(
            {
                "ready": len(ready),
                "blocked": len(blocked),
                "already_clean": len(authorized) - len(proposals),
            }
        ),
        flush=True,
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--qids-from")
    parser.add_argument("--only-qid")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--receipts", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--database-from-heroku", action="store_true")
    args = parser.parse_args()
    if args.database_from_heroku:
        heroku_path = shutil.which("heroku")
        if not heroku_path:
            raise ValueError("The Heroku CLI is unavailable.")
        result = subprocess.run(  # noqa: S603 — resolved executable and fixed arguments
            [heroku_path, "config:get", "DATABASE_URL", "-a", "mhm-pipeline-web"],
            capture_output=True,
            text=True,
            check=True,
            timeout=45,
        )
        os.environ["DATABASE_URL"] = result.stdout.strip()
        os.environ["PG_BOUNCER_URL"] = ""
    cookie = getpass.getpass("MHM session cookie (memory only): ")
    try:
        raise SystemExit(asyncio.run(run(args, cookie)))
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_type": type(error).__name__,
                    "frames": [
                        {
                            "file": Path(frame.filename).name,
                            "line": frame.lineno,
                            "function": frame.name,
                        }
                        for frame in traceback.extract_tb(error.__traceback__)
                    ],
                }
            ),
            flush=True,
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
