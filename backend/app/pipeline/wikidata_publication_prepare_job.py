"""Build a durable Publication Release outside the HTTP request."""

from __future__ import annotations

import asyncio
import logging
import uuid

import httpx

from converter.wikidata import troki_author_repair as troki
from sqlalchemy import select

from app.db import session_scope
from app.models.run_job import (
    JOB_KIND_WIKIDATA_GILA_AUDIT,
    JOB_KIND_WIKIDATA_GILA_CASE_REVIEW,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress
from app.pipeline.wikidata_item_views import StudioBuildMissingError, fetch_merged_wikidata_item
from app.pipeline.wikidata_reference_reader import ReferenceItemReader
from app.publication.credentials import configured_publication_gateway_factory
from app.publication.reviewed_reference_scope import GILA_CREATOR_UID
from app.publication.runtime import PublicationRuntime, _has_blocking_item_issue
from app.schemas.publication import PreparePublicationRequest, RunPublicationSource


logger = logging.getLogger(__name__)


def audited_batch_params(report, actor_id, *, evidence=None, people_links=None):
    from app.pipeline.wikidata_gila_audit_job import gila_cases

    rows = gila_cases(report)
    eligible = {
        (case["local_id"], case["qid"], case.get("repair_kind") == "troki_author")
        for case in rows
        if not people_links and case.get("can_prepare") and case.get("local_id") and case.get("qid")
    }
    proofs = {
        proof["case_id"]: proof for proof in (evidence.result or {}).get("_repairs", [])
    } if evidence and evidence.status == JOB_STATUS_SUCCEEDED else {}
    eligible.update(
        (case["local_id"], case["qid"], False) for case in rows
        if case["case_id"] in proofs and case.get("local_id") and case.get("qid")
        and (not people_links or (
            case["points"] == [5] and case["qid"] in people_links
            and proofs[case["case_id"]].get("candidate_qid") == people_links[case["qid"]]
        ))
    )
    if not eligible:
        return None
    revision, digest, fingerprint = (
        report.params.get(key) for key in (
            "_gila_build_revision", "_gila_build_digest", "_gila_source_fingerprint",
        )
    )
    if not (revision and digest and fingerprint):
        raise ValueError("Run a fresh audit before preparation.")
    request = PreparePublicationRequest(
        profile_id="mhm-wikidata", profile_version="1-nodes", target="live",
        source=RunPublicationSource(projection_source="canonical"),
    )
    return {
        "actor_id": str(actor_id),
        "request": request.model_dump(mode="json"),
        "_gila_batch": sorted(eligible),
        "_gila_audit_id": str(report.id),
        **({"_gila_evidence_job": str(evidence.id)} if proofs else {}),
        **({"_gila_people_links": dict(people_links)} if people_links else {}),
        "_gila_build_snapshot": [revision, digest],
        "_gila_source_fingerprint": fingerprint,
        "_gila_open_case_counts": {
            "manual_review": sum(row.get("disposition") == "manual_review" for row in rows),
            "not_checked": sum(row.get("disposition") == "not_checked" for row in rows),
        },
    }


def audited_statements(item: dict, cases: list[dict]) -> list[dict]:
    from app.pipeline.wikidata_gila_facts import SourceFact, _matches, normalize_source_proposal

    selected = []
    for case in cases:
        prop = case.get("property")
        if not isinstance(prop, str) or not prop.startswith("P"):
            continue
        case_statements = []
        for value in case.get("expected_values") or [case.get("expected_value")]:
            if not isinstance(value, str) or not value:
                continue
            fact = SourceFact(
                str(case.get("source_field") or ""), prop, value,
                qualifiers=case.get("expected_qualifiers") or {},
                precision=case.get("expected_precision"),
                calendar=case.get("expected_calendar") or "",
                unit=case.get("expected_unit") or "",
                forbids_circa=case.get("expected_forbids_circa") is True,
            )
            matches = [
                statement for statement in item.get("statements", [])
                if (statement.get("property") or statement.get("property_id")) == prop
                and _matches(fact, normalize_source_proposal(statement))
            ]
            if len(matches) != 1:
                break
            case_statements.append(matches[0])
        else:
            for statement in case_statements:
                if statement not in selected:
                    selected.append(statement)
    return selected


async def _author_evidence_is_current(reader: ReferenceItemReader) -> bool:
    work, manuscript, author = await asyncio.gather(
        reader.fetch(troki.WORK_QID), reader.fetch(troki.MANUSCRIPT_QID),
        reader.fetch(troki.AUTHOR_QID),
    )
    return (
        troki.work_matches(work, require_missing=True)
        and troki.manuscript_links_work(manuscript)
        and troki.author_matches(author)
    )


async def _mapped_target(db, run_id, local_id, qid, exact_author):
    try:
        item = await fetch_merged_wikidata_item(db, run_id, local_id, source="canonical")
    except StudioBuildMissingError:
        return False
    if not item or item.get("rebuild_required") or _has_blocking_item_issue(item):
        return False
    if exact_author:
        return qid == troki.WORK_QID and troki.author_statement(item) is not None
    return item.get("existing_qid") == qid


async def _creator_result(reader: ReferenceItemReader, qid: str) -> str | None:
    try:
        creator = await reader.creator_uid(qid)
    except ValueError:
        return "creator_unavailable"
    if creator is None:
        return "creator_unavailable"
    return None if creator == GILA_CREATOR_UID else "other_creator"


async def _select_batch(job_id, db, run_id, entries):
    by_local = {}
    by_qid = {}
    for local_id, qid, exact_author in entries:
        by_local.setdefault(local_id, set()).add((qid, exact_author))
        by_qid.setdefault(qid, set()).add((local_id, exact_author))
    exclusions = []
    candidates = []
    for qid, mappings in sorted(by_qid.items()):
        if len(mappings) != 1:
            exclusions.append({"qid": qid, "reason": "target_conflict"})
            continue
        local_id, exact_author = next(iter(mappings))
        if len(by_local[local_id]) != 1:
            exclusions.append({"qid": qid, "reason": "target_conflict"})
        elif not await _mapped_target(db, run_id, local_id, qid, exact_author):
            exclusions.append({"qid": qid, "reason": "source_changed"})
        else:
            candidates.append((local_id, qid, exact_author))
    await db.commit()

    reader = ReferenceItemReader()
    selection = {}
    includes_exact_author = False
    for offset in range(0, len(candidates), 5):
        if await is_cancel_requested(job_id):
            return None
        group = candidates[offset : offset + 5]
        outcomes = await asyncio.gather(*(_creator_result(reader, qid) for _, qid, _ in group))
        for (local_id, qid, exact_author), reason in zip(group, outcomes, strict=True):
            if not reason and exact_author:
                try:
                    if not await _author_evidence_is_current(reader):
                        reason = "source_changed"
                except ValueError:
                    reason = "evidence_unavailable"
            if reason:
                exclusions.append({"qid": qid, "reason": reason})
            else:
                selection[local_id] = qid
                includes_exact_author |= exact_author
        await update_job_progress(job_id, {
            "phase": "check_creators",
            "processed": len(selection) + len(exclusions),
            "total": len(by_qid),
            "unit": "items",
            "message": "Check audited targets and first-revision creators.",
        })
    return selection, includes_exact_author, {
        "prepared_qids": sorted(selection.values()),
        "exclusions": exclusions,
        "batch_counts": {"eligible": len(by_qid), "prepared": len(selection)},
    }


async def _read_evidence(db, job, latest):
    params = job.params
    if not params.get("_gila_evidence_job"):
        return {}
    evidence = await db.get(RunJob, uuid.UUID(params["_gila_evidence_job"]))
    if (
        evidence is None or evidence.kind != JOB_KIND_WIKIDATA_GILA_CASE_REVIEW
        or evidence.status != JOB_STATUS_SUCCEEDED or evidence.run_id != job.run_id
        or evidence.created_by != job.created_by or evidence.project_id != job.project_id
        or evidence.params.get("_audit_id") != str(latest.id)
        or evidence.params.get("_gila_cycle_parent") != params.get("_gila_cycle_parent")
        or evidence.params.get("_gila_cycle_step") != "find_evidence"
    ):
        raise ValueError("The Kimi evidence does not belong to this repair and audit.")
    return {proof["case_id"]: proof for proof in (evidence.result or {}).get("_repairs", [])}


async def _evidence_statements(db, run_id, item, cases, *, people=None):
    from app.pipeline.wikidata_gila_case_review import (
        SCRIBE_REASONS, _case_record, load_authority_evidence, prepare_evidence_statement,
    )
    from app.pipeline.marc_verify_context import canonical_control_number

    statements = []
    revisions = set()
    if not item or item.get("entity_type") != "manuscript":
        return statements, revisions
    records = {canonical_control_number(cn) for cn in item.get("records", [])}
    for case, proof in cases:
        if records != {case["control_number"]}:
            continue
        try:
            cn = case["control_number"]
            source = (people or {}).get(cn) if case["reason"] in SCRIBE_REASONS else None
            raw = source if source is not None else await _case_record(db, run_id, cn)
            authority_rows = (
                (await load_authority_evidence(db, run_id, cn)).get(cn, [])
                if case["reason"] in SCRIBE_REASONS else []
            )
            await db.commit()
            async with asyncio.timeout(30):
                statement = await prepare_evidence_statement(raw, case, proof, authority_rows=authority_rows)
        except (ValueError, httpx.HTTPError, TimeoutError) as exc:
            logger.warning("Repair evidence for %s could not be checked (%s)", case["qid"], type(exc).__name__)
            continue
        if statement and statement not in statements:
            statements.append(statement)
            revisions.add(proof["base_revision"])
    return statements, revisions


async def run_wikidata_publication_prepare_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        params = job.params or {}
        actor_id = str(params.get("actor_id") or "").strip()
        payload = params.get("request")
        if not actor_id or not isinstance(payload, dict):
            await finish_job(
                job_id,
                status=JOB_STATUS_FAILED,
                error="Invalid Publication prepare job parameters",
            )
            return
        scoped = "_selected_existing" in params or "_gila_batch" in params
        await update_job_progress(job_id, {
            "phase": "preparing",
            "message": "Verify the audited source and prepare the Publication release."
            if scoped else "Prepare the immutable Publication Release.",
        })
        freshness_timeout = None
        troki_author_only = params.get("_gila_troki_author") is True
        selected = params.get("_selected_existing")
        batch = params.get("_gila_batch")
        batch_result = {}
        exact_statements = {}
        evidence_statements = {}
        audited_revisions = {}
        if selected is not None or batch:
            from app.pipeline.marc_verify_context import load_run_marc_records
            from app.pipeline.wikidata_gila_audit_job import gila_record_fingerprint
            from app.routers.wikidata_studio import STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS

            expected_fingerprint = params.get("_gila_source_fingerprint")
            if expected_fingerprint is not None:
                records = await load_run_marc_records(db, job.run_id)
                if gila_record_fingerprint(records) != expected_fingerprint:
                    await finish_job(
                        job_id, status=JOB_STATUS_FAILED,
                        error="The source changed. Run a new audit before preparation.",
                    )
                    return
            if batch:
                selection = await _select_batch(job_id, db, job.run_id, batch)
                if selection is None or await is_cancel_requested(job_id):
                    await finish_job(job_id, status=JOB_STATUS_CANCELLED)
                    return
                selected, troki_author_only, batch_result = selection
                latest = await db.scalar(select(RunJob).where(
                    RunJob.run_id == job.run_id,
                    RunJob.kind == JOB_KIND_WIKIDATA_GILA_AUDIT,
                    RunJob.status == JOB_STATUS_SUCCEEDED,
                ).order_by(RunJob.created_at.desc(), RunJob.id.desc()).limit(1))
                await db.commit()
                if not latest or str(latest.id) != params["_gila_audit_id"]:
                    await finish_job(
                        job_id, status=JOB_STATUS_FAILED,
                        error="The audit changed. Start the batch from the latest audit.",
                    )
                    return
                if selected and params.get("_gila_cycle_parent"):
                    from app.pipeline.wikidata_gila_audit_job import gila_cases, gila_people_marc

                    people = gila_people_marc(latest)
                    proofs = await _read_evidence(db, job, latest)
                    cases = {}
                    source_cases = {}
                    for row in gila_cases(latest):
                        local_id = row.get("local_id")
                        if local_id not in selected or row.get("qid") != selected[local_id]:
                            continue
                        if row.get("can_prepare") and not params.get("_gila_people_links"):
                            cases.setdefault(local_id, []).append(row)
                        if proof := proofs.get(row["case_id"]):
                            links = params.get("_gila_people_links")
                            if not links or (row["points"] == [5] and proof.get("candidate_qid") == links.get(row["qid"])):
                                source_cases.setdefault(local_id, []).append((row, proof))
                    for local_id, qid in list(selected.items()):
                        if troki_author_only and local_id == troki.WORK_LOCAL_ID:
                            continue
                        item = await fetch_merged_wikidata_item(
                            db, job.run_id, local_id, source="canonical",
                        )
                        audited_cases = cases.get(local_id, [])
                        statements = audited_statements(item or {}, audited_cases)
                        additions, revisions = await _evidence_statements(
                            db, job.run_id, item, source_cases.get(local_id, []), people=people,
                        )
                        if statements:
                            revisions.update(case.get("live_revision") for case in audited_cases)
                        revision = next(iter(revisions)) if len(revisions) == 1 else None
                        if (statements or additions) and type(revision) is int and revision > 0:
                            exact_statements[local_id] = statements
                            evidence_statements[local_id] = additions
                            audited_revisions[local_id] = revision
                        else:
                            del selected[local_id]
                            batch_result["exclusions"].append({"qid": qid, "reason": "exact_claim_needs_evidence"})
                    batch_result["prepared_qids"] = sorted(selected.values())
                    batch_result["batch_counts"]["prepared"] = len(selected)
                    await db.commit()
                if not selected:
                    await finish_job(
                        job_id, status=JOB_STATUS_SUCCEEDED, result=batch_result,
                        progress={
                            "phase": "succeeded", "message": "No items passed the repair checks.",
                        },
                    )
                    return
            else:
                for local_id, qid in selected.items():
                    if not await _mapped_target(db, job.run_id, local_id, qid, troki_author_only):
                        await finish_job(
                            job_id, status=JOB_STATUS_FAILED,
                            error="The selected item changed. Rebuild Studio and run a new audit.",
                        )
                        return
                if troki_author_only and not await _author_evidence_is_current(
                    ReferenceItemReader(),
                ):
                    await finish_job(
                        job_id, status=JOB_STATUS_FAILED,
                        error="The repair evidence changed. Run a new audit before preparation.",
                    )
                    return
            freshness_timeout = STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS
        runtime = PublicationRuntime(
            session=db, gateway_factory=configured_publication_gateway_factory,
            freshness_timeout_seconds=freshness_timeout,
        )
        response = await runtime.prepare(
            run_id=job.run_id,
            request=PreparePublicationRequest.model_validate(payload),
            actor_id=actor_id,
            idempotency_key=str(job.id) if params.get("_gila_cycle_parent") else None,
            **({
                "selected_existing": selected,
                "expected_snapshot": tuple(params["_gila_build_snapshot"]),
                "troki_author_only": troki_author_only,
                **({"repair_statements": exact_statements, "repair_revisions": audited_revisions,
                    "repair_evidence_statements": evidence_statements}
                   if exact_statements else {}),
                **({"exact_author_retry_base": params["_gila_retry_base"]}
                   if troki_author_only and params.get("_gila_retry_base") else {}),
            } if selected is not None else {}),
        )
    publication = response.publication
    if publication is None:
        await finish_job(
            job_id, status=JOB_STATUS_FAILED, error="Publication preparation returned no Release",
        )
        return
    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result={**batch_result, "publication_id": publication.publication_id},
        progress={
            "phase": "succeeded",
            "processed": publication.current_release.entity_count,
            "total": publication.current_release.entity_count,
            "unit": "entities",
            "message": "The immutable Publication Release is ready for review.",
        },
    )
