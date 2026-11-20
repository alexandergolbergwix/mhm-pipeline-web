"""Read the current Gila corpus and retain a revision-bound case inventory."""

import asyncio
import json
import logging
import re
import uuid
import zlib
from base64 import b64decode, b64encode
from hashlib import sha256

from sqlalchemy import select

from app.auth.admin import ROLE_ADMIN
from app.db import session_scope
from app.models.project import Membership, Project
from app.models.run_job import JOB_KIND_WIKIDATA_GILA_AUDIT, RunJob
from app.models.user import User
from app.pipeline.marc_verify_context import canonical_control_number, load_run_marc_records
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress
from app.pipeline.wikidata_gila_audit import build_gila_audit_report
from app.pipeline.wikidata_gila_completion import build_gila_completion_packet
from app.pipeline.wikidata_item_views import fetch_merged_wikidata_items
from app.pipeline.wikidata_qid_ledger import load_global_ledger
from app.pipeline.wikidata_reference_reader import ReferenceItemReader
from app.pipeline.wikidata_reference_repair_job import load_seed
from app.pipeline.wikidata_source_choices import source_hash
from app.publication.reviewed_reference_scope import remaining_reference_inventory
from app.publication.runtime import PublicationSourceError, StudioCacheProjectionSource
from converter.wikidata.reference_retrieval import current_reference_indices

logger = logging.getLogger(__name__)
KIND = JOB_KIND_WIKIDATA_GILA_AUDIT
_QID = re.compile(r"Q[1-9][0-9]*\Z")


class AuditCancelledError(Exception):
    """The administrator cancelled the audit."""


def _live_qid(value: object) -> bool:
    return isinstance(value, str) and _QID.fullmatch(value) is not None


def gila_record_fingerprint(records: list[dict]) -> str:
    return sha256("|".join(source_hash(row) for row in records).encode()).hexdigest()


def gila_cases(job: RunJob) -> list[dict]:
    return json.loads(zlib.decompress(b64decode(job.params["_gila_cases"])))


def _catalogue_mappings(records, ledger):
    mappings = {}
    for record in records:
        control = canonical_control_number(record.get("_control_number") or record.get("001"))
        qids = {
            ledger.get(key)
            for key in (
                f"wikidata:marc:{control}",
                f"wikidata:marc:QDraft_MS_{control}",
                f"wikidata:marc:marc:{control}",
            )
        } - {None, ""}
        if control and len(qids) == 1:
            mappings[control] = qids.pop()
    return mappings


def _linked_entities(entities: dict[str, dict]) -> set[str]:
    return {
        value["id"]
        for entity in entities.values()
        for pid in ("P1574", "P50", "P3342", "P11603")
        for claim in entity["claims"].get(pid, [])
        if claim.get("rank") != "deprecated"
        and isinstance(value := claim["mainsnak"].get("datavalue", {}).get("value"), dict)
        and _live_qid(value.get("id"))
    }


async def _authorize(db, job: RunJob) -> None:
    if job is None or job.status != "running" or job.cancel_requested_at:
        raise AuditCancelledError()
    user = await db.get(User, job.created_by)
    project = await db.get(Project, job.project_id)
    member = await db.scalar(
        select(Membership.user_id).where(
            Membership.project_id == job.project_id,
            Membership.user_id == job.created_by,
        )
    )
    if (
        not user
        or user.role != ROLE_ADMIN
        or not project
        or not (project.owner_id == user.id or member)
    ):
        raise ValueError("The audit requires current administrator and project access.")


async def _live_items(
    job_id: uuid.UUID, requested: set[str], entities: dict[str, dict], seen: set[str], phase: str
):
    reader = ReferenceItemReader()
    qids = sorted(requested - seen)
    async with reader._public_client() as client:
        for start in range(0, len(qids), 50):
            if await is_cancel_requested(job_id):
                raise AuditCancelledError()
            batch = qids[start : start + 50]
            await update_job_progress(
                job_id,
                {
                    "phase": phase,
                    "processed": len(seen),
                    "total": len(seen) + len(qids) - start,
                    "unit": "items",
                    "message": "Read live Wikidata revisions and references.",
                },
            )
            entities.update(await reader.fetch_batch(client, batch))
            seen.update(batch)


def _case_inventory(packet, audit):
    local_ids = {row["control_number"]: row.get("local_id") for row in audit["records"]}
    items = []
    keys = (
        "case_id",
        "points",
        "disposition",
        "classification",
        "qid",
        "qids",
        "control_number",
        "source_field",
        "source_value",
        "expected_author",
        "expected_value",
        "expected_qualifiers",
        "live_status",
        "live_revision",
        "reason",
        "live_reason",
        "completion_reason",
    )
    for row in packet["cases"]:
        if row["disposition"] == "verified":
            continue
        case = {key: row[key] for key in keys if row.get(key) not in (None, "", {})}
        case["local_id"] = local_ids.get(row.get("control_number"))
        # Only an exact expected value on a known manuscript can start a
        # normal source-projection release. Identity and historic cases have
        # separate routes and review boundaries.
        case["can_prepare"] = (
            row["case_id"].startswith("fact:")
            and row["disposition"] == "repair_required"
            and row.get("proposal_status") == "present"
            and bool(row.get("expected_value"))
            and row.get("live_status") in {"missing", "present"}
            and bool(row.get("qid") and case["local_id"])
        )
        items.append(case)
    for row in packet["source_passages"]:
        items.append(
            {
                "case_id": f"passage:{row['passage_id']}",
                "points": row.get("points") or [9],
                "disposition": "not_checked",
                "qid": row.get("qid"),
                "control_number": row["control_number"],
                "source_field": row.get("source_field"),
                "source_value": row.get("source_text"),
                "local_id": local_ids.get(row["control_number"]),
                "reason": "passage_requires_exact_scope_and_public_reference_proof",
            }
        )
    return items


def _work_author_cases(canonical, entities):
    cases = []
    for item in canonical:
        if item.get("entity_type") != "work":
            continue
        qid = item.get("existing_qid")
        statements = item.get("statements") or []
        expected = {
            row["value"]
            for row in statements
            if row.get("property") == "P50" and _live_qid(row.get("value"))
        }
        if not expected:
            continue
        # This published work has exact authority evidence. If the canonical
        # ledger has no current association, the case remains a review.
        target = qid or (
            "Q141175530"
            if item.get("local_id") == "work:חזוק_אמונה" and expected == {"Q168171"}
            else None
        )
        live = entities.get(target)
        if not live:
            continue
        actual = {
            value.get("id")
            for row in live["claims"].get("P50", [])
            if row.get("rank") != "deprecated"
            and isinstance(value := row["mainsnak"].get("datavalue", {}).get("value"), dict)
        }
        missing = expected - actual
        if not missing:
            continue
        repair = bool(
            qid
            and not (actual - expected)
            and item.get("local_id")
            and not item.get("rebuild_required")
            and not any(
                issue.get("severity") == "error" for issue in item.get("validation_issues", [])
            )
        )
        cases.append(
            {
                "case_id": f"work-author:{target}",
                "points": [5],
                "qid": target,
                "local_id": item.get("local_id"),
                "live_revision": live["lastrevid"],
                "source_value": item.get("labels", {}).get("he")
                or item.get("labels", {}).get("en"),
                "expected_value": ", ".join(sorted(missing)),
                "source_field": "100",
                "disposition": "repair_required" if repair else "manual_review",
                "reason": "author_authority_link_absent",
                "can_prepare": repair,
            }
        )
    return cases


async def run_gila_audit_job(job_id: uuid.UUID) -> None:
    try:
        await update_job_progress(
            job_id,
            {"phase": "source", "message": "Load the MARC sources and the canonical Studio build."},
        )
        async with session_scope() as db:
            job = await db.get(RunJob, job_id)
            await _authorize(db, job)
            run_id = job.run_id
            snapshot = await StudioCacheProjectionSource(db).current_snapshot(
                run_id=run_id,
                source="canonical",
                approved_only=True,
            )
            records = await load_run_marc_records(db, run_id)
            ledger = await load_global_ledger(db, is_test=False)
            canonical = await fetch_merged_wikidata_items(db, run_id, source="canonical")
        mappings = _catalogue_mappings(records, ledger)
        seed, _ = load_seed()
        inventory = remaining_reference_inventory()["items"]
        known = (
            set(mappings.values())
            | set(seed["manifest"]["qids"])
            | {row["qid"] for row in inventory}
        )
        known.update(
            item.get("existing_qid") for item in canonical if _live_qid(item.get("existing_qid"))
        )
        entities: dict[str, dict] = {}
        seen: set[str] = set()
        await _live_items(job_id, known, entities, seen, "public_items")
        await _live_items(job_id, _linked_entities(entities), entities, seen, "linked_items")
        if await is_cancel_requested(job_id):
            raise AuditCancelledError()
        await update_job_progress(
            job_id,
            {
                "phase": "compare",
                "processed": len(seen),
                "total": len(seen),
                "unit": "items",
                "message": "Compare the nine requirements against the source and live statements.",
            },
        )
        audit = await asyncio.to_thread(
            build_gila_audit_report,
            records,
            canonical,
            qids_by_cn=mappings,
            live_entities=entities,
        )
        packet = await asyncio.to_thread(build_gila_completion_packet, audit, entities)
        cases = _case_inventory(packet, audit)
        for case in _work_author_cases(canonical, entities):
            cases.append(case)
            disposition = case["disposition"]
            point = packet["email_points"][4]
            counts = point["counts"]
            counts[disposition] = counts.get(disposition, 0) + 1
            if point["status"] == "verified" or (
                point["status"] == "manual_review" and disposition == "repair_required"
            ):
                point["status"] = disposition
            packet["all_requirements_verified"] = False
            packet["disposition_counts"][disposition] = (
                packet["disposition_counts"].get(disposition, 0) + 1
            )
        duplicates = {
            qid: blocks
            for qid, entity in entities.items()
            if (
                blocks := sum(
                    len(references) - len(current_reference_indices(references))
                    for statements in entity["claims"].values()
                    for statement in statements
                    if (references := statement.get("references", [])) and len(references) > 1
                )
            )
        }
        reference_checks = {
            row["qid"]: duplicates.get(row["qid"], 0) if row["qid"] in entities else None
            for row in inventory
        }
        compressed = b64encode(zlib.compress(json.dumps(cases, ensure_ascii=False).encode()))
        async with session_scope() as db:
            job = await db.get(RunJob, job_id, with_for_update=True)
            await _authorize(db, job)
            current = await StudioCacheProjectionSource(db).is_current(str(run_id), snapshot)
            current_records = await load_run_marc_records(db, run_id)
            digest = gila_record_fingerprint(records)
            if not current or digest != gila_record_fingerprint(current_records):
                raise ValueError("The source or canonical build changed. Start a fresh audit.")
            job.params = {
                **job.params,
                "_gila_cases": compressed.decode(),
                "_gila_source_fingerprint": digest,
                "_gila_build_revision": snapshot.revision,
                "_gila_build_digest": snapshot.digest,
            }
            await db.commit()
        await finish_job(
            job_id,
            status="succeeded",
            result={
                key: packet[key]
                for key in (
                    "record_count",
                    "all_records_read",
                    "all_requirements_verified",
                    "email_points",
                    "requirement_coverage",
                    "disposition_counts",
                )
            }
            | {
                "public_requested": len(seen),
                "public_read": len(entities),
                "reference_items": len(duplicates),
                "reference_blocks": sum(duplicates.values()),
                "reviewed_reference_checks": reference_checks,
                "case_count": len(cases),
                "build_revision": snapshot.revision,
            },
            progress={
                "phase": "complete",
                "message": "The report is ready. Open a requirement to review its cases.",
            },
        )
    except AuditCancelledError:
        await finish_job(job_id, status="cancelled", error="The administrator cancelled the audit.")
    except PublicationSourceError:
        await finish_job(
            job_id,
            status="failed",
            error="Build the canonical Studio source and start the audit again.",
        )
    except Exception:
        logger.exception("The Gila audit stopped.")
        await finish_job(
            job_id,
            status="failed",
            error="The audit could not confirm its live or source evidence. Start a new audit.",
        )
