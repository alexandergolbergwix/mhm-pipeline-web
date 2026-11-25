"""Coordinate the existing audit, exact reference repair, and Publication jobs."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from contextlib import nullcontext
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import defer

from app.auth.admin import ROLE_ADMIN
from app.db import session_scope
from app.models.project import PROJECT_ROLE_EDITOR, PROJECT_ROLE_OWNER, Membership, Project
from app.models.publication import PublicationExecution, PublicationExecutionAction
from app.models.run import Run
from app.models.run_job import (
    ACTIVE_JOB_STATUSES,
    JOB_KIND_WIKIDATA_GILA_AUDIT,
    JOB_KIND_WIKIDATA_GILA_CASE_REVIEW,
    JOB_KIND_WIKIDATA_GILA_REPAIR,
    JOB_KIND_WIKIDATA_PUBLICATION_DRY_RUN,
    JOB_KIND_WIKIDATA_PUBLICATION_EXECUTION,
    JOB_KIND_WIKIDATA_PUBLICATION_PREPARE,
    JOB_KIND_WIKIDATA_STUDIO_BUILD,
    RunJob,
)
from app.models.user import User
from app.pipeline.run_job_service import (
    ActiveJobError,
    create_job,
    finish_job,
    request_cancel,
    update_job_progress,
)
from app.pipeline.wikidata_publication_prepare_job import audited_batch_params
from app.pipeline.wikidata_evidence_reads import paced_wikidata_evidence_reads
from app.pipeline.wikidata_reference_reader import ReferenceItemReader
from app.pipeline.wikidata_reference_repair_job import REFERENCE_JOB_MAXLAG, load_remaining_seed, load_seed
from app.publication.credentials import (
    ExecutionCredentialResolver,
    configured_publication_gateway_factory,
    seal_execution_credential,
)
from app.publication.runtime import PublicationRuntime
from app.routers.admin_reference_repairs import queue_reviewed_reference_job
from app.schemas.publication import (
    AdvancePublicationRequest,
    DryRunPublicationCommand,
    EligibleReleaseSelection,
    PublicationSummaryQuery,
    PublishPublicationCommand,
    ReviewPublicationCommand,
)
from converter.wikidata import troki_author_repair as troki
from converter.wikidata.retry_policy import STUDIO_MAXLAG

logger = logging.getLogger(__name__)


class RepairCancelled(Exception):
    """The administrator stopped the repair cycle."""


def compare_requirements(before, after):
    initial = {row["point"]: row for row in before["email_points"]}
    return [
        {
            "point": row["point"],
            "title": row["title"],
            "before": initial[row["point"]].get("counts", {}),
            "after": row.get("counts", {}),
        }
        for row in after["email_points"]
        if row["point"] in initial
    ]


async def exact_author_retry_base(actions, reader: ReferenceItemReader) -> int | None:
    bases = {action.target_revision for action, _ in actions}
    if len(bases) != 1 or any(
        action.action != "update" or action.state != "outcome_unknown"
        or action.entity_key != troki.WORK_LOCAL_ID or action.allow_foreign_update
        or status != "paused" or action.lease_owner is not None
        or action.lease_expires_at is not None and action.lease_expires_at > datetime.now(UTC)
        for action, status in actions
    ):
        return None
    base = next(iter(bases))
    if not base:
        return None
    try:
        revision = await reader.current_revision(troki.WORK_QID)
        entity = await reader.fetch(troki.WORK_QID)
    except ValueError:
        return None
    return base if revision == base == entity.get("lastrevid") and troki.work_matches(
        entity, require_missing=True,
    ) else None


async def authorize_repair_cycle(db, job_id: uuid.UUID) -> RunJob:
    job = await db.get(RunJob, job_id, populate_existing=True)
    if not job or job.status != "running" or job.cancel_requested_at:
        raise RepairCancelled()
    user = await db.get(User, job.created_by, populate_existing=True)
    project = await db.get(Project, job.project_id, populate_existing=True)
    role = await db.scalar(select(Membership.role).where(
        Membership.project_id == job.project_id, Membership.user_id == job.created_by,
    ))
    if not project or not user or user.role != ROLE_ADMIN or (
        project.owner_id != user.id and role not in {PROJECT_ROLE_OWNER, PROJECT_ROLE_EDITOR}
    ):
        raise ValueError("The repair requires current administrator and project edit access.")
    return job


class RepairCycle:
    def __init__(self, job: RunJob) -> None:
        self.id = job.id
        self.actor = job.created_by
        self.run_id = job.run_id
        self.project_id = job.project_id
        self.corpus = job.params.get("_gila_scope") == "people_and_sources"
        self.evidence_batch = job.params.get("_gila_scope") == "people_evidence_batch"
        self.authority_batch = job.params.get("_gila_scope") == "people_authority"
        self.source_evidence_batch = job.params.get("_gila_scope") == "sources_append_only"
        self.subjects_only = self.source_evidence_batch and job.params.get("_gila_subjects_only") is True
        self.write_maxlag = REFERENCE_JOB_MAXLAG if self.subjects_only else STUDIO_MAXLAG
        self.people_only = self.evidence_batch or self.authority_batch or job.params.get("_gila_requirement") == 5
        self.has_preflight = self.corpus or self.evidence_batch or self.authority_batch or self.source_evidence_batch
        self.preflight_points = {
            "people_authority": (1, 5), "people_evidence_batch": (5,), "sources_append_only": (8,),
        }.get(job.params.get("_gila_scope"), (5, 8))
        self.people_links = dict(job.params.get("_gila_people_links") or {})
        self.resolver = ExecutionCredentialResolver(
            job.params["_publication_credential"], publication_id=str(self.run_id),
            execution_id=job.params["_cycle"], actor_id=str(self.actor),
        )
        self.source_baseline = None
        self.result = {
            "publication_ids": [], "items_published": 0, "reference_items_corrected": 0,
            "exclusions": [], "stages_needing_attention": [],
        }

    async def _wait(self, child_id, phase, message, *, full=False):
        last = None
        while True:
            async with session_scope() as db:
                job = await db.get(RunJob, child_id, options=[defer(RunJob.params)])
                if job is None:
                    raise ValueError("The repair stage job is missing.")
                if job.status not in ACTIVE_JOB_STATUSES:
                    if full:
                        await db.refresh(job, ["params"])
                    return job
                progress = job.progress or {}
                next_progress = {
                    "phase": phase,
                    "message": progress.get("message") or message,
                    **{key: progress[key] for key in (
                        "processed", "total", "unit", "next_attempt_at",
                    ) if key in progress},
                }
            if next_progress != last:
                await update_job_progress(self.id, next_progress)
                last = next_progress
            async with session_scope() as db:
                await authorize_repair_cycle(db, self.id)
            await asyncio.sleep(2)

    async def stage(self, kind, phase, message, params=None, *, full=False):
        while True:
            async with session_scope() as db:
                await authorize_repair_cycle(db, self.id)
                child_id = await db.scalar(select(RunJob.id).where(
                    RunJob.run_id == self.run_id,
                    RunJob.kind == kind,
                    RunJob.params["_gila_cycle_parent"].as_string() == str(self.id),
                    RunJob.params["_gila_cycle_step"].as_string() == phase,
                ).limit(1))
                existing_id = None
                if child_id is None:
                    try:
                        child = await create_job(
                            db, run_id=self.run_id, project_id=self.project_id,
                            kind=kind, created_by=self.actor, params={
                                **(params or {}), "_gila_cycle_parent": str(self.id),
                                **({"_gila_full_sources": True} if (self.has_preflight or self.people_only) and kind == JOB_KIND_WIKIDATA_GILA_AUDIT else {}),
                                **({"_gila_reuse_authority": True}
                                   if (self.authority_batch or self.source_evidence_batch) and kind == JOB_KIND_WIKIDATA_GILA_AUDIT else {}),
                                "_gila_cycle_step": phase, "repair_workspace": True,
                            },
                        )
                        child_id = child.id
                    except ActiveJobError as exc:
                        existing_id = exc.job_id
            if child_id is not None:
                return await self._wait(child_id, phase, message, full=full)
            await self._wait(existing_id, phase, "Wait for the active job to finish.")

    async def cancel_children(self):
        async with session_scope() as db:
            active = (await db.scalars(select(RunJob.id).where(
                RunJob.run_id == self.run_id,
                RunJob.params["_gila_cycle_parent"].as_string() == str(self.id),
                RunJob.status.in_(ACTIVE_JOB_STATUSES),
            ))).all()
            for child_id in active:
                await request_cancel(db, child_id)

    async def references(self, scope, audit):
        load = load_seed if scope == "original" else load_remaining_seed
        seed, digest = load()
        if scope == "remaining":
            checks = audit.result.get("reviewed_reference_checks", {})
            if all(checks.get(qid) == 0 for qid in seed["manifest"]["qids"]):
                return
        if len(seed["checkpoint"].get("results", [])) == len(seed["manifest"]["qids"]):
            return
        try:
            async with session_scope() as db:
                await authorize_repair_cycle(db, self.id)
                run = await db.get(Run, self.run_id)
                job = await queue_reviewed_reference_job(
                    db, run, self.actor, scope, seed, digest, parent_id=self.id,
                )
                child_id = job.id
                own = job.params.get("_gila_cycle_parent") == str(self.id)
                baseline = job.params.get("_gila_baseline_corrections", 0)
            job = await self._wait(child_id, f"references_{scope}", "Check reviewed references.")
            if own:
                verified = (job.result or {}).get("verified", (job.progress or {}).get("verified", 0))
                self.result["reference_items_corrected"] += max(0, verified - baseline)
            if job.status != "succeeded":
                self.attention(f"references_{scope}", job.error)
        except HTTPException as exc:
            self.attention(f"references_{scope}", str(exc.detail))

    def attention(self, phase, reason):
        self.result["stages_needing_attention"].append({
            "stage": phase, "reason": reason or "The job needs review.",
        })

    async def publication(self, audit, evidence):
        params = (audited_batch_params(audit, self.actor, evidence=evidence, people_links=self.people_links)
                  if self.people_links else audited_batch_params(audit, self.actor, evidence=evidence))
        if params is None:
            return
        async with session_scope() as db:
            qids = {entry[1] for entry in params["_gila_batch"]}
            cycle_executions = await db.scalars(select(RunJob.params["execution_id"].as_string()).where(
                RunJob.kind == JOB_KIND_WIKIDATA_PUBLICATION_EXECUTION,
                RunJob.params["_gila_cycle_parent"].as_string() == str(self.id),
            ))
            previous = (await db.execute(select(
                PublicationExecutionAction, PublicationExecution.status,
            ).join(PublicationExecution, PublicationExecution.id == PublicationExecutionAction.execution_id).where(
                PublicationExecutionAction.target_qid.in_(qids),
                PublicationExecutionAction.state.in_(("in_flight", "outcome_unknown")),
                PublicationExecutionAction.execution_id.not_in([
                    uuid.UUID(execution_id) for execution_id in cycle_executions if execution_id
                ]),
            ))).all()
        uncertain = {action.target_qid for action, _ in previous}
        if troki.WORK_QID in uncertain and any(
            qid == troki.WORK_QID and exact for _, qid, exact in params["_gila_batch"]
        ):
            base = await exact_author_retry_base(
                [(action, status) for action, status in previous if action.target_qid == troki.WORK_QID],
                ReferenceItemReader(),
            )
            if base:
                uncertain.remove(troki.WORK_QID)
                params["_gila_retry_base"] = base
        self.result["exclusions"].extend(
            {"qid": qid, "reason": "previous_write_needs_verification"} for qid in sorted(uncertain)
        )
        params["_gila_batch"] = [entry for entry in params["_gila_batch"] if entry[1] not in uncertain]
        while params["_gila_batch"]:
            number = len(self.result["publication_ids"])
            prepared = await self.stage(
                JOB_KIND_WIKIDATA_PUBLICATION_PREPARE, f"prepare_{number}",
                "Check each target and its first revision.", params,
            )
            result = prepared.result or {}
            self.result["exclusions"].extend(result.get("exclusions", []))
            publication_id = result.get("publication_id")
            if prepared.status != "succeeded":
                self.attention("prepare", prepared.error)
                return
            if not publication_id:
                return
            self.result["publication_ids"].append(publication_id)
            blocked = await self.publish(publication_id, number)
            if not blocked:
                return
            prepared_qids = set(result.get("prepared_qids", []))
            remaining = prepared_qids - blocked
            if not remaining or remaining == prepared_qids:
                return
            params = {
                **params, "_gila_batch": [entry for entry in params["_gila_batch"]
                                         if entry[1] in remaining],
            }

    async def publish(self, publication_id, number):
        async with session_scope() as db:
            await authorize_repair_cycle(db, self.id)
            runtime = PublicationRuntime(session=db, gateway_factory=configured_publication_gateway_factory)
            publication = (await runtime.read(
                run_id=self.run_id, publication_id=publication_id, actor_id=str(self.actor),
                query=PublicationSummaryQuery(type="summary"),
            )).publication
            approval = publication.approval_set
            if approval is None:
                release = publication.current_release
                publication = (await runtime.advance(
                    run_id=self.run_id, publication_id=publication_id, actor_id=str(self.actor),
                    request=AdvancePublicationRequest(command=ReviewPublicationCommand(
                        type="review", release_id=release.release_id,
                        expected_release_digest=release.release_digest,
                        selection=EligibleReleaseSelection(mode="eligible_release"),
                        decision="approve", reason="Administrator authorized the audited repair cycle.",
                    )),
                )).publication
                approval = publication.approval_set
        if approval is None:
            raise ValueError("The repair release could not record its approval.")
        credential = await self.resolver.resolve(f"wikidata:production:{self.actor}")
        check_scope = f"dry-run:{self.id}:{number}"
        checked = await self.stage(
            JOB_KIND_WIKIDATA_PUBLICATION_DRY_RUN, f"dry_run_{number}",
            "Check the scoped Publication plan.",
            {
                "publication_id": publication_id, "actor_id": str(self.actor),
                "credential_scope_id": check_scope,
                "request": AdvancePublicationRequest(command=DryRunPublicationCommand(
                    type="dry_run", approval_set_id=approval.approval_set_id,
                    expected_approval_digest=approval.approval_digest,
                )).model_dump(mode="json"),
                "_publication_credential": seal_execution_credential(
                    credential, publication_id=publication_id, execution_id=check_scope,
                ),
            },
        )
        async with session_scope() as db:
            await authorize_repair_cycle(db, self.id)
            runtime = PublicationRuntime(session=db, gateway_factory=configured_publication_gateway_factory)
            publication = (await runtime.read(
                run_id=self.run_id, publication_id=publication_id, actor_id=str(self.actor),
                query=PublicationSummaryQuery(type="summary"),
            )).publication
            plan, receipt = publication.plan, publication.dry_run_receipt
            if checked.status != "succeeded" or not plan or not receipt:
                self.attention("dry_run", checked.error)
                blocked = plan.blocked_actions if plan else []
                self.result["exclusions"].extend(
                    {"qid": action.target_qid, "reason": action.reason} for action in blocked
                )
                return {action.target_qid for action in blocked if action.target_qid}
            if plan.action_counts.get("create", 0) or plan.accepted_foreign_update_count:
                raise ValueError("The automatic repair plan must contain creator-verified updates only.")
            if not plan.action_counts.get("update", 0):
                return set()
            execution = publication.execution
            if execution is None:
                execution = (await runtime.advance(
                    run_id=self.run_id, publication_id=publication_id, actor_id=str(self.actor),
                    request=AdvancePublicationRequest(command=PublishPublicationCommand(
                        type="publish", plan_id=plan.plan_id,
                        dry_run_receipt_id=receipt.dry_run_receipt_id,
                        expected_receipt_digest=receipt.receipt_digest,
                    )),
                )).publication.execution
        if execution is None:
            raise ValueError("The checked repair did not create an Execution.")
        applied = await self.stage(
            JOB_KIND_WIKIDATA_PUBLICATION_EXECUTION, f"publish_{number}",
            "Apply and verify the approved edits.", {
                "publication_id": publication_id, "execution_id": execution.execution_id,
                "actor_id": str(self.actor),
                "_publication_credential": seal_execution_credential(
                    credential, publication_id=publication_id, execution_id=execution.execution_id,
                ),
            },
        )
        async with session_scope() as db:
            persisted = await db.get(PublicationExecution, uuid.UUID(execution.execution_id))
        if persisted:
            self.result["items_published"] += persisted.succeeded_count
        if applied.status != "succeeded":
            self.attention("publish", applied.error)
        return set()

    async def people_batch(self, audit):
        from app.pipeline.marc_verify_context import load_run_marc_records
        from app.pipeline.wikidata_gila_audit_job import gila_audit_export, gila_cases, gila_people_marc, gila_record_fingerprint
        from app.pipeline.wikidata_gila_people_batch import apply_planned_edits, plan_people_batch

        cases = await asyncio.to_thread(gila_cases, audit)
        records = await asyncio.to_thread(gila_people_marc, audit)
        inventory = None
        planner_options = {}
        source_audit_id = str(audit.id)
        if self.authority_batch:
            from app.pipeline.wikidata_gila_authority_cache import AuthorityEvidence, CACHE_KEY
            from app.pipeline.wikidata_gila_corpus_repair import corpus_inventory

            cases = [
                row for row in cases
                if row["points"] in ([1], [5]) and not row["case_id"].startswith("passage:")
            ]
            inventory = corpus_inventory(cases, frozenset(self.preflight_points))
            for row in cases:
                inventory[row["case_id"]]["person_role"] = row.get("person_role")
            evidence = AuthorityEvidence.load(
                audit.params.get(CACHE_KEY), audit.params["_gila_source_fingerprint"],
            )
            planner_options["evidence"] = evidence
        if self.evidence_batch:
            from app.pipeline.wikidata_gila_people_evidence_batch import evidence_batch, keep_reviewed_targets

            cases, inventory, source_audit_id = evidence_batch(cases)
        if self.source_evidence_batch:
            from app.pipeline.wikidata_gila_corpus_repair import source_corpus_inventory

            cases = [row for row in cases if 8 in row["points"] and not row["case_id"].startswith("passage:")]
            previous_cases = []
            already_verified = set()
            if self.source_baseline:
                previous_cases = await asyncio.to_thread(gila_cases, self.source_baseline)
                export = await asyncio.to_thread(gila_audit_export, audit, "verified")
                already_verified = {row["case_id"] for row in (export or {}).get("verified_checks", [])}
            inventory = source_corpus_inventory(
                cases, previous_cases, already_verified,
                reconciliations=audit.params.get("_gila_citation_reconciliations", ()),
            )
        open_ids = {
            row["case_id"] for row in cases
            if row.get("person_role") or row.get("holding_label")
            or ((self.corpus or self.source_evidence_batch) and 8 in row["points"])
        }
        counts: Counter[str] = Counter()
        self.result["people_batch"] = counts

        async def check():
            async with session_scope() as db:
                await authorize_repair_cycle(db, self.id)

        async def progress(phase, processed, total, unit, message):
            await update_job_progress(self.id, {
                "phase": phase, "message": message, "processed": processed, "total": total, "unit": unit,
            })

        async def check_source():
            from app.publication.runtime import StudioCacheProjectionSource
            from app.routers.wikidata_studio import STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS

            async with session_scope() as db:
                await authorize_repair_cycle(db, self.id)
                current = await load_run_marc_records(db, self.run_id)
                projection = StudioCacheProjectionSource(
                    db, freshness_timeout_seconds=STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS,
                )
                snapshot = await projection.current_snapshot(
                    run_id=self.run_id, source="canonical", approved_only=True,
                )
            if (
                snapshot.revision != audit.params.get("_gila_build_revision")
                or snapshot.digest != audit.params.get("_gila_build_digest")
                or gila_record_fingerprint(current) != audit.params.get("_gila_source_fingerprint")
            ):
                raise ValueError("The manuscript source or Studio build changed after the first audit.")

        async def journal(state, receipt):
            async with session_scope() as db:
                if state == "intent":
                    await authorize_repair_cycle(db, self.id)
                job = await db.get(RunJob, self.id, with_for_update=True)
                previous = job.params.get("_gila_people_receipts") or {}
                job.params = {**job.params, "_gila_people_receipts": {
                    **previous, receipt["qid"]: {**receipt, "state": state},
                }}
                await db.commit()

        async def credential():
            return await self.resolver.resolve(f"wikidata:production:{self.actor}")

        async with session_scope() as db:
            previous_receipts = await db.scalars(select(RunJob.params["_gila_people_receipts"]).where(
                RunJob.run_id == self.run_id, RunJob.kind == JOB_KIND_WIKIDATA_GILA_REPAIR,
            ))
            uncertain = {
                qid for receipts in previous_receipts if isinstance(receipts, dict)
                for qid, receipt in receipts.items()
                if receipt["state"] in {"intent", "uncertain"} and open_ids.intersection(receipt["case_ids"])
            }
        plans = [] if self.source_evidence_batch else await plan_people_batch(
            cases, records, check=check, progress=progress, counts=counts,
            **({"inventory": inventory} if inventory is not None else {}), **planner_options,
        )
        if self.has_preflight:
            from app.pipeline.wikidata_gila_corpus_repair import corpus_inventory, confirm_corpus_plans, plan_sources

            if inventory is None or self.source_evidence_batch:
                if inventory is None:
                    inventory = corpus_inventory(cases)
                source_cases = cases
                if self.subjects_only:
                    source_cases = [row for row in cases if row.get("source_repair", {}).get("property") == "P921"]
                plans.extend(await plan_sources(
                    source_cases, records, check=check, progress=progress, inventory=inventory,
                    additions_only=self.source_evidence_batch,
                ))
            elif self.evidence_batch:
                plans = keep_reviewed_targets(plans, inventory)
            elif self.authority_batch:
                from app.pipeline.wikidata_gila_corpus_repair import plan_holding_labels

                with paced_wikidata_evidence_reads():
                    plans.extend(await plan_holding_labels(cases, records, check=check, inventory=inventory))
            with paced_wikidata_evidence_reads():
                plans = await confirm_corpus_plans(
                    plans, inventory, check=check, progress=progress, uncertain=uncertain,
                )
            if self.source_evidence_batch:
                from app.pipeline.wikidata_gila_corpus_repair import source_additions_only

                if not all(source_additions_only(plan) for plan in plans):
                    raise ValueError("Source proof repairs must retain all existing claims and references.")
            await check_source()
            self.result["corpus_preflight"] = {
                "records": len(records), "eligible_items": len(plans),
                **({"source_archive": True, "baseline_audit_id": str(self.source_baseline.id) if self.source_baseline else source_audit_id}
                   if self.source_evidence_batch else {}),
                **({"source_audit_id": source_audit_id, "case_count": len(inventory)} if self.people_only or self.source_evidence_batch else {}),
                "requirements": [
                    {"point": point, "counts": dict(Counter(
                        row["status"] for row in inventory.values() if point in row["points"]
                    ))}
                    for point in self.preflight_points
                ],
            }
            async with session_scope() as db:
                job = await authorize_repair_cycle(db, self.id)
                job.params = {
                    **job.params, "_gila_corpus_preflight": list(inventory.values()),
                    **({CACHE_KEY: evidence.dump()} if self.authority_batch else {}),
                }
                job.result = dict(self.result)
                await db.commit()
            logger.info("Corpus preflight %s completed: %s.", self.id, self.result["corpus_preflight"])
        with paced_wikidata_evidence_reads() if self.has_preflight or self.people_only else nullcontext():
            verified = await apply_planned_edits(
                plans, credential=credential, check=check, check_source=check_source,
                progress=progress, journal=journal, counts=counts, uncertain_qids=uncertain,
                maxlag=self.write_maxlag,
            )
        logger.info("Manuscript repair batch %s completed with %s.", self.id, dict(counts))
        if counts["uncertain"]:
            self.attention("people_apply", "A Wikidata edit needs recovery from its saved receipt.")
        if counts["service_pause"]:
            self.attention("people_apply", "Wikidata requested a pause before the next edit.")
        return set(verified)

    async def run(self):
        if self.source_evidence_batch:
            async with session_scope() as db:
                job = await authorize_repair_cycle(db, self.id)
                self.source_baseline = await db.scalar(select(RunJob).where(
                    RunJob.run_id == self.run_id,
                    RunJob.kind == JOB_KIND_WIKIDATA_GILA_AUDIT,
                    RunJob.status == "succeeded",
                    RunJob.finished_at <= job.created_at,
                    RunJob.result["full_source_coverage"].as_boolean().is_(True),
                ).order_by(RunJob.created_at.desc(), RunJob.id.desc()).limit(1))
        audit = await self.stage(
            JOB_KIND_WIKIDATA_GILA_AUDIT, "initial_audit", "Read Wikidata and check the source.", full=True,
        )
        if audit.status == "failed" and (audit.progress or {}).get("phase") == "source_rebuild_required":
            build = await self.stage(
                JOB_KIND_WIKIDATA_STUDIO_BUILD, "rebuild", "Rebuild the canonical source.", {
                    "source": "canonical", "approved_only": True, "force_rebuild": True,
                    "extract_provenance_prose": False,
                },
            )
            if build.status != "succeeded":
                raise ValueError(build.error or "The canonical source rebuild did not finish.")
            audit = await self.stage(
                JOB_KIND_WIKIDATA_GILA_AUDIT, "audit_after_rebuild", "Check the rebuilt source.", full=True,
            )
        if audit.status != "succeeded":
            raise ValueError(audit.error or "The initial audit did not finish.")
        if self.source_baseline and any(
            audit.params.get(key) != self.source_baseline.params.get(key)
            for key in ("_gila_source_fingerprint", "_gila_build_revision", "_gila_build_digest")
        ):
            self.source_baseline = None
        verified_people_cases = set()
        if not self.people_links:
            try:
                verified_people_cases = await self.people_batch(audit)
            except (ValueError, RuntimeError) as exc:
                if self.evidence_batch or self.authority_batch or self.source_evidence_batch:
                    raise
                logger.exception("The bulk person stage stopped.")
                self.attention("people_evidence", str(exc))
        batch = self.result.get("people_batch", {})
        if not self.has_preflight and not self.people_only and not (batch.get("uncertain") or batch.get("service_pause")):
            for scope in (() if self.people_links else ("original", "remaining")):
                await self.references(scope, audit)
            repair_audit = audit
            if self.result["reference_items_corrected"]:
                refreshed = await self.stage(
                    JOB_KIND_WIKIDATA_GILA_AUDIT, "audit_after_references",
                    "Check current revisions after reference repairs.",
                    {"_gila_people_source_audit": str(audit.id)}, full=True,
                )
                if refreshed.status == "succeeded":
                    repair_audit = refreshed
                else:
                    self.attention("audit_after_references", refreshed.error)
            evidence = await self.stage(
                JOB_KIND_WIKIDATA_GILA_CASE_REVIEW, "find_evidence",
                "Find evidence in MARC and Wikidata with Kimi.", {
                    "_audit_id": str(repair_audit.id),
                    **({"_gila_people_links": self.people_links} if self.people_links else {}),
                },
            )
            if evidence.status == "succeeded":
                self.result["evidence_review"] = {
                    key: (evidence.result or {}).get(key, 0)
                    for key in ("checked", "supported", "needs_evidence", "excluded_creator", "unavailable")
                }
                if unavailable := self.result["evidence_review"]["unavailable"]:
                    self.attention("find_evidence", f"Evidence checks were unavailable for {unavailable} cases.")
            else:
                self.attention("find_evidence", evidence.error)
            try:
                await self.publication(repair_audit, evidence)
            except ValueError as exc:
                self.attention("publication", str(exc))
        final = await self.stage(
            JOB_KIND_WIKIDATA_GILA_AUDIT, "verify", "Recheck all nine requests against live Wikidata.",
            {"_gila_people_source_audit": str(audit.id)}, full=bool(verified_people_cases) or self.source_evidence_batch,
        )
        if final.status != "succeeded":
            raise ValueError(final.error or "The verification audit did not finish.")
        if "people_batch" in self.result:
            from app.pipeline.wikidata_gila_audit_job import gila_cases

            open_ids = (
                {row["case_id"] for row in await asyncio.to_thread(gila_cases, final)}
                if verified_people_cases and not self.source_evidence_batch else set()
            )
            confirmed_ids = verified_people_cases - open_ids
            verified_source_ids = set()
            if self.source_evidence_batch:
                from app.pipeline.wikidata_gila_audit_job import gila_audit_export

                export = await asyncio.to_thread(gila_audit_export, final, "verified")
                verified_source_ids = {row["case_id"] for row in (export or {}).get("verified_checks", [])}
                confirmed_ids = verified_people_cases & verified_source_ids
            if self.has_preflight and self.result.get("corpus_preflight"):
                async with session_scope() as db:
                    job = await authorize_repair_cycle(db, self.id)
                    rows = job.params["_gila_corpus_preflight"]
                    if self.source_evidence_batch:
                        job.params = {**job.params, "_gila_corpus_preflight": [
                            {**row, "outcome": "verified"
                             if row.get("original_case_id", row["case_id"]) in verified_source_ids else "unresolved"}
                            for row in rows
                        ]}
                        await db.commit()
                self.result["corpus_verified"] = [
                    {"point": point, "cases": sum(
                        point in row["points"] and row["case_id"] in confirmed_ids for row in rows
                    )}
                    for point in self.preflight_points
                ]
            self.result["people_batch"]["cases_verified"] = (
                next(row["cases"] for row in self.result["corpus_verified"] if row["point"] == 5)
                if self.authority_batch else len(confirmed_ids)
            )
        self.result["audit_id"] = str(final.id)
        comparison = compare_requirements(audit.result, final.result)
        if self.source_baseline:
            source_comparison = next(
                row for row in compare_requirements(self.source_baseline.result, final.result) if row["point"] == 8
            )
            comparison = [source_comparison if row["point"] == 8 else row for row in comparison]
        self.result["requirement_comparison"] = comparison
        self.result["requirements_verified"] = sum(
            point["status"] == "verified" for point in final.result["email_points"]
        )
        self.result["repeated_references"] = final.result["reference_blocks"]


async def run_gila_repair_job(job_id: uuid.UUID) -> None:
    cycle = None
    try:
        async with session_scope() as db:
            cycle = RepairCycle(await authorize_repair_cycle(db, job_id))
        await cycle.run()
        async with session_scope() as db:
            await authorize_repair_cycle(db, job_id)
        await finish_job(
            job_id, status="succeeded", result=cycle.result,
            progress={"phase": "complete", "message": "The verification audit is ready."},
        )
    except RepairCancelled:
        if cycle:
            await cycle.cancel_children()
        await finish_job(job_id, status="cancelled", result=cycle.result if cycle else None)
    except Exception as exc:
        if cycle:
            await cycle.cancel_children()
        logger.exception("The automatic manuscript repair job stopped.")
        await finish_job(
            job_id, status="failed", result=cycle.result if cycle else None,
            error=str(exc) if isinstance(exc, ValueError) else "The repair job stopped. See the stage results.",
        )
