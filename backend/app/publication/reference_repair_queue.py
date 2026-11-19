"""Process exact reference repairs serially through normal Publication executions."""

import fcntl
import json
import math
import re
import tempfile
from asyncio import sleep
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.pipeline.wikidata_foreground_repair import ForegroundReferenceGateway
from app.pipeline.wikidata_gila_execution import (
    RepairGateway,
    SourceCheck,
    execute_repair,
    expected_entity,
    semantic_entity,
)
from app.pipeline.wikidata_gila_repairs import (
    build_gila_repair_proposals,
    entity_digest,
    validate_gila_repair_proposal,
)
from app.pipeline.wikidata_retry_diagnostics import ApiRetryDiagnostics
from app.publication.reference_recovery import LagProbeResult, LagProbeSchedule
from app.publication.repair_retry_runner import RepairRetryWait, wait_for_repair_publication
from app.publication.repository import PublicationRepository
from converter.wikidata.uploader import CreatorIdentityTimeoutError
from converter.wikidata.work_identity import reference_control_numbers

DEFAULT_WAIT_SECONDS = 24 * 60 * 60
CREATOR_RETRY_DELAY = timedelta(minutes=5)


class ReferencePriorWriteUnresolvedError(ValueError):
    """An earlier write still lacks a conclusive result."""


def candidate_qids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("The candidate file must contain a nonempty list.")
    qids = []
    for row in value:
        qid = row.get("qid") if isinstance(row, dict) else row
        if not isinstance(qid, str) or not re.fullmatch(r"Q[1-9][0-9]*", qid):
            raise ValueError("Each candidate needs an exact item QID.")
        if qid in qids:
            raise ValueError("The candidate file contains a repeated QID.")
        qids.append(qid)
    return tuple(qids)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def queue_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "queue.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another process holds this queue directory.") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def reference_source_controls(proposal: Mapping[str, object]) -> set[str]:
    changes = proposal.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError("The exact reference proposal is absent.")
    controls = set()
    for change in changes:
        if change.get("kind") not in {
            "deduplicate_exact_references",
            "coalesce_retrieval_references",
        }:
            raise ValueError("The automatic queue accepts reference corrections only.")
        before, after = change["before"], change["after"]
        if {k: v for k, v in before.items() if k != "references"} != {
            k: v for k, v in after.items() if k != "references"
        }:
            raise ValueError("A non-reference field changed.")
        old = reference_control_numbers(before.get("references", []))
        if old != reference_control_numbers(after.get("references", [])):
            raise ValueError("The reference source identity changed.")
        controls.update(old)
    if not controls:
        raise ValueError("The reference correction lacks catalogue source evidence.")
    return controls


async def run_reference_repair_queue(
    qids: Sequence[str],
    gateway: RepairGateway,
    source_check: SourceCheck,
    repository: PublicationRepository,
    *,
    state_dir: Path,
    actor_id: str,
    run_id: str,
    project_id: str,
    apply: bool = False,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    on_event: Callable[[dict], None] | None = None,
    before_item: Callable[[str], Awaitable[None]] | None = None,
    on_receipt: Callable[[dict], Awaitable[None]] | None = None,
    on_checkpoint: Callable[[str, list[dict], Path], Awaitable[None]] | None = None,
    on_creator_retry: Callable[[str, list[dict], Path, datetime], Awaitable[None]] | None = None,
    creator_retry_at: datetime | None = None,
    resume_results: Sequence[dict] = (),
    lag_probe: Callable[[], Awaitable[LagProbeResult]] | None = None,
) -> dict:
    """Retain each exact plan and stop before the next item after any unsafe result."""
    qids = candidate_qids(list(qids))
    if not math.isfinite(wait_seconds) or wait_seconds < 0:
        raise ValueError("The wait limit must be finite and non-negative.")
    if isinstance(gateway, ForegroundReferenceGateway):
        raise ValueError("The automatic queue cannot use a foreground gateway.")
    binding = {
        "version": 1,
        "run_id": run_id,
        "actor_user_id": actor_id,
        "project_id": project_id,
        "qids": list(qids),
    }
    emit = on_event or (lambda event: None)
    results = [dict(row) for row in resume_results]
    if [row.get("qid") for row in results] != list(qids[: len(results)]) or any(
        not (row.get("corrected") is True and row.get("status") == "verified")
        and row.get("status") != "already_clean"
        for row in results
    ):
        raise ValueError("The saved completed prefix does not match the reviewed queue.")
    lag_probe_schedule = LagProbeSchedule()

    async def preview_journal(phase, receipt):
        return None

    with queue_lock(state_dir):
        manifest = state_dir / "queue.json"
        if manifest.exists():
            if json.loads(manifest.read_text()) != binding:
                raise ValueError("The queue scope or authenticated actor changed.")
        else:
            write_json(manifest, binding)
        while len(results) < len(qids):
            ordinal = len(results) + 1
            qid = qids[ordinal - 1]
            if creator_retry_at is not None:
                emit({
                    "qid": qid, "ordinal": ordinal, "total": len(qids),
                    "status": "creator_retry_wait", "next_attempt_at": creator_retry_at.isoformat(),
                })
                remaining = (creator_retry_at - datetime.now(UTC)).total_seconds()
                if remaining > 0:
                    await sleep(remaining)
                creator_retry_at = None
            await repository.checkpoint()
            plan_file = state_dir / f"plan-{qid}.json"
            try:
                if before_item is not None:
                    await before_item(qid)
                if plan_file.exists():
                    plan = json.loads(plan_file.read_text())
                    if (
                        plan.get("execution_mode") is not None
                        or plan.get("authorized_qids") != [qid]
                        or plan.get("run_id") != run_id
                        or plan.get("actor_user_id") != actor_id
                        or plan.get("project_id") != project_id
                        or len(plan.get("proposals", [])) != 1
                        or plan["proposals"][0].get("qid") != qid
                    ):
                        raise ValueError("The saved exact plan does not match this queue item.")
                    proposal = plan["proposals"][0]
                    reference_source_controls(proposal)
                    validate_gila_repair_proposal(proposal, plan["before_entities"][qid])
                else:
                    entity = await gateway.fetch(qid)
                    await gateway.assert_allowed(qid)
                    proposals = build_gila_repair_proposals({qid: entity})
                    if not proposals:
                        row = {"qid": qid, "status": "already_clean", "corrected": False}
                        results.append(row)
                        write_json(state_dir / "progress.json", {"results": results})
                        if on_checkpoint is not None:
                            await on_checkpoint(qid, results, state_dir)
                        emit({**row, "ordinal": ordinal, "total": len(qids)})
                        continue
                    if len(proposals) != 1:
                        raise ValueError("The reference proposal is ambiguous.")
                    proposal = proposals[0]
                    reference_source_controls(proposal)
                    preflight = await execute_repair(
                        proposal,
                        gateway,
                        authorized_qids=frozenset({qid}),
                        source_check=source_check,
                        journal=preview_journal,
                    )
                    plan = {
                        "run_id": run_id,
                        "actor_user_id": actor_id,
                        "project_id": project_id,
                        "source_sha256": preflight["source_evidence"]["source_sha256"],
                        "authorized_qids": [qid],
                        "proposals": [proposal],
                        "preflight": [preflight],
                        "before_entities": {qid: entity},
                    }
                    write_json(plan_file, plan)
                if on_checkpoint is not None:
                    await on_checkpoint(qid, results, state_dir)
                emit({"qid": qid, "status": "ready", "ordinal": ordinal, "total": len(qids)})
                if not apply:
                    await execute_repair(
                        proposal,
                        gateway,
                        authorized_qids=frozenset({qid}),
                        source_check=source_check,
                        journal=preview_journal,
                    )
                    row = {"qid": qid, "status": "ready", "corrected": False}
                else:

                    def report_wait(
                        event: RepairRetryWait,
                        current_qid: str = qid,
                        current_ordinal: int = ordinal,
                    ) -> None:
                        current = {
                            "qid": current_qid,
                            "status": "service_delay"
                            if event.diagnostics and event.diagnostics.code == "maxlag"
                            else "retry_wait",
                            "execution_id": event.execution_id,
                            "next_attempt_at": event.next_attempt_at.isoformat(),
                            "seconds": event.seconds,
                            "retry": event.diagnostics.to_dict() if event.diagnostics else None,
                            "lag_probe_status": event.lag_probe.status if event.lag_probe else None,
                            "lag_probe_seconds": event.lag_probe.lag_seconds
                            if event.lag_probe else None,
                            "next_lag_probe_at": event.next_probe_at.isoformat()
                            if event.next_probe_at else None,
                        }
                        write_json(
                            state_dir / "progress.json",
                            {
                                "status": current["status"],
                                "results": results,
                                "current": current,
                            },
                        )
                        emit({**current, "ordinal": current_ordinal, "total": len(qids)})

                    summary = await wait_for_repair_publication(
                        plan,
                        gateway,
                        source_check,
                        repository,
                        actor_id=actor_id,
                        run_id=run_id,
                        max_wait_seconds=wait_seconds,
                        on_wait=report_wait,
                        lag_probe=lag_probe,
                        lag_probe_schedule=lag_probe_schedule,
                    )
                    intent = (
                        await repository.latest_write_intent(summary.execution_id, f"repair:{qid}")
                        if summary.execution_id
                        else None
                    )
                    await repository.checkpoint()
                    after = (
                        await gateway.fetch(qid) if intent and intent.state == "succeeded" else None
                    )
                    verified = bool(
                        after
                        and entity_digest(after) == intent.result_fingerprint
                        and after["lastrevid"] > plan["before_entities"][qid]["lastrevid"]
                        and semantic_entity(after)
                        == semantic_entity(expected_entity(proposal, plan["before_entities"][qid]))
                    )
                    diagnostics = ApiRetryDiagnostics.from_detail(intent.detail) if intent else None
                    row = {
                        "qid": qid,
                        "status": "verified"
                        if verified
                        else (
                            "verification_failed"
                            if intent and intent.state == "succeeded"
                            else intent.state
                            if intent
                            else "blocked"
                        ),
                        "corrected": verified,
                        "before_revision": plan["before_entities"][qid]["lastrevid"],
                        "after_revision": after["lastrevid"] if verified else None,
                        "retry": diagnostics.to_dict() if diagnostics else None,
                        "publication_id": summary.publication_id,
                        "execution_id": summary.execution_id,
                        "next_attempt_at": intent.next_attempt_at.isoformat()
                        if intent and intent.next_attempt_at
                        else None,
                    }
                write_json(state_dir / f"receipt-{qid}.json", row)
                if apply and on_receipt is not None:
                    await on_receipt(row)
            except Exception as error:
                if (
                    apply and on_creator_retry is not None and not plan_file.exists()
                    and isinstance(error, CreatorIdentityTimeoutError)
                ):
                    creator_retry_at = datetime.now(UTC) + CREATOR_RETRY_DELAY
                    await on_creator_retry(qid, results, state_dir, creator_retry_at)
                    continue
                row = {
                    "qid": qid,
                    "status": "blocked",
                    "corrected": False,
                    "error_type": type(error).__name__,
                }
                if isinstance(error, ReferencePriorWriteUnresolvedError):
                    row["halt_reason"] = "prior_write_unresolved"
            results.append(row)
            resumable = row["status"] == "pre_send_retryable" and bool(row.get("next_attempt_at"))
            status = (
                "active"
                if row["status"] in {"verified", "ready"}
                else "paused"
                if resumable
                else "stopped"
            )
            progress = {"status": status, "results": results}
            if status == "stopped":
                progress["current"] = row
            if resumable:
                retry = row.get("retry")
                progress["current"] = {
                    **row,
                    "status": "service_delay"
                    if retry and retry.get("code") == "maxlag"
                    else "retry_wait",
                }
            write_json(state_dir / "progress.json", progress)
            if on_checkpoint is not None:
                await on_checkpoint(qid, results, state_dir)
            emit({**row, "ordinal": ordinal, "total": len(qids)})
            if status != "active":
                return progress
        result = {"status": "complete", "results": results}
        write_json(state_dir / "progress.json", result)
        return result
