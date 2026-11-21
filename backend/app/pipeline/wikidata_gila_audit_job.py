"""Read the current Gila corpus and retain a revision-bound case inventory."""

import asyncio
import json
import logging
import re
import uuid
import zlib
from base64 import b64decode, b64encode
from collections.abc import Mapping
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException
from sqlalchemy import select

from app.auth.admin import ROLE_ADMIN
from app.db import session_scope
from app.models.project import Membership, Project
from app.models.run_job import JOB_KIND_WIKIDATA_GILA_AUDIT, RunJob
from app.models.user import User
from app.pipeline.hmo_canonical_wikidata import filter_public_wikidata_items
from app.pipeline.marc_verify_context import canonical_control_number, load_run_marc_records
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress
from app.pipeline.wikidata_gila_audit import build_gila_audit_report
from app.pipeline.wikidata_gila_completion import build_gila_completion_packet
from app.pipeline.wikidata_qid_ledger import (
    ledger_key_for_item,
    load_global_ledger,
    lookup_ledger_qid,
)
from app.pipeline.wikidata_reference_reader import ReferenceItemReader
from app.pipeline.wikidata_reference_repair_job import load_seed
from app.pipeline.wikidata_source_choices import source_hash
from app.publication.reviewed_reference_scope import remaining_reference_inventory
from app.publication.runtime import PublicationSourceError, StudioCacheProjectionSource
from converter.wikidata import troki_author_repair as troki
from converter.wikidata.catalog_references import STALE_CATALOGUE_EDIT
from converter.wikidata.reference_retrieval import current_reference_indices

logger = logging.getLogger(__name__)
KIND = JOB_KIND_WIKIDATA_GILA_AUDIT
_QID = re.compile(r"Q[1-9][0-9]*\Z")


class AuditCancelledError(Exception):
    """The administrator cancelled the audit."""


class PublicAuditSnapshot(Mapping[str, dict]):
    """Keep only a small set of complete entity responses in memory."""

    def __init__(self, directory: str) -> None:
        self._directory = Path(directory)
        self._keys: set[str] = set()
        self._load = lru_cache(maxsize=24)(self._load_entity)

    def __getitem__(self, qid: str) -> dict:
        if qid not in self._keys:
            raise KeyError(qid)
        return self._load(qid)

    def __iter__(self):
        return iter(sorted(self._keys))

    def __len__(self) -> int:
        return len(self._keys)

    def __contains__(self, qid: object) -> bool:
        return qid in self._keys

    def _load_entity(self, qid: str) -> dict:
        return json.loads((self._directory / qid).read_bytes())

    def add_batch(self, batch: Mapping[str, dict]) -> None:
        for qid, entity in batch.items():
            if not _live_qid(qid) or qid in self._keys:
                raise ValueError("A public audit snapshot needs one response per exact QID.")
            (self._directory / qid).write_text(
                json.dumps(entity, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            self._keys.add(qid)


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


async def _canonical_items(projection, run_id, snapshot, ledger):
    items = []
    fields = (
        "local_id",
        "entity_type",
        "existing_qid",
        "records",
        "labels",
        "descriptions",
        "statements",
        "work_candidate_evidence",
        "authority_evidence",
        "validation_issues",
        "rebuild_required",
    )
    async for raw in projection._stream_items(run_id, "canonical", True, snapshot.digest):
        for item in filter_public_wikidata_items([raw], source="canonical"):
            compact = {name: item[name] for name in fields if name in item}
            if not compact.get("existing_qid"):
                compact["existing_qid"] = lookup_ledger_qid(
                    ledger,
                    ledger_key_for_item(item, "wikidata"),
                )
            items.append(compact)
    return items


def _linked_entities(entities: Mapping[str, dict]) -> set[str]:
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
    job_id: uuid.UUID,
    requested: set[str],
    entities: PublicAuditSnapshot,
    seen: set[str],
    phase: str,
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
            await asyncio.to_thread(entities.add_batch, await reader.fetch_batch(client, batch))
            seen.update(batch)


def _case_inventory(packet, audit):
    local_ids = {row["control_number"]: row.get("local_id") for row in audit["records"]}
    items = [{
        "case_id": "model:manuscript-and-work",
        "points": [2],
        "disposition": "manual_review",
        "reason": "manuscript_work_model_requires_decision",
        "can_prepare": False,
    }]
    keys = (
        "case_id",
        "points",
        "disposition",
        "classification",
        "qid",
        "qids",
        "control_number",
        "source_field",
        "property",
        "source_value",
        "expected_author",
        "expected_value",
        "expected_qualifiers",
        "expected_precision",
        "expected_calendar",
        "expected_unit",
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
        target = qid or (
            troki.WORK_QID
            if item.get("local_id") == troki.WORK_LOCAL_ID and expected == {troki.AUTHOR_QID}
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
        exact_author = (
            troki.author_statement(item) is not None
            and troki.work_matches(live, require_missing=True)
            and troki.manuscript_links_work(entities.get(troki.MANUSCRIPT_QID) or {})
            and troki.author_matches(entities.get(troki.AUTHOR_QID) or {})
        )
        repair = bool(
            item.get("local_id") != troki.WORK_LOCAL_ID
            and qid
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
                "property": "P50",
                "expected_values": sorted(missing),
                "disposition": "repair_required" if repair or exact_author else "manual_review",
                "reason": (
                    "troki_author_link_verified" if exact_author else "author_authority_link_absent"
                ),
                "can_prepare": repair or exact_author,
                **({"repair_kind": "troki_author", "control_number": troki.WORK_SOURCE}
                   if exact_author else {}),
            }
        )
    return cases


def _stale_catalogue_edit_cases(canonical):
    return [
        {
            "case_id": f"stale-catalogue-edit:{item['local_id']}",
            "points": [6],
            "disposition": "manual_review",
            "local_id": item["local_id"],
            "control_number": (item.get("records") or [None])[0],
            "source_field": "P528",
            "reason": "stale_catalogue_edit",
            "can_prepare": False,
        }
        for item in canonical
        if item.get("local_id") and any(
            issue.get("code") == STALE_CATALOGUE_EDIT for issue in item.get("validation_issues", [])
        )
    ]


def _repeated_reference_counts(entities: PublicAuditSnapshot) -> dict[str, int]:
    return {
        qid: blocks
        for qid, entity in entities.items()
        if (
            blocks := sum(
                len(refs) - len(current_reference_indices(refs))
                for claims in entity["claims"].values()
                for claim in claims
                if (refs := claim.get("references", [])) and len(refs) > 1
            )
        )
    }


async def run_gila_audit_job(job_id: uuid.UUID) -> None:
    with TemporaryDirectory(prefix="mhm-gila-audit-") as directory:
        await _run_audit(job_id, PublicAuditSnapshot(directory))


async def _run_audit(job_id: uuid.UUID, entities: PublicAuditSnapshot) -> None:
    from app.routers.wikidata_studio import STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS

    try:
        await update_job_progress(
            job_id,
            {
                "phase": "check_source",
                "message": "Verify the Studio build. This check can take up to five minutes.",
            },
        )
        async with session_scope() as db:
            job = await db.get(RunJob, job_id)
            await _authorize(db, job)
            run_id = job.run_id
            projection = StudioCacheProjectionSource(
                db, freshness_timeout_seconds=STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS,
            )
            snapshot = await projection.current_snapshot(
                run_id=run_id,
                source="canonical",
                approved_only=True,
            )
            await update_job_progress(
                job_id,
                {"phase": "source", "message": "Read the MARC records and the verified build."},
            )
            records = await load_run_marc_records(db, run_id)
            ledger = await load_global_ledger(db, is_test=False)
            canonical = await _canonical_items(projection, run_id, snapshot, ledger)
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
        if any(troki.author_statement(item) is not None for item in canonical):
            known.update({troki.WORK_QID, troki.MANUSCRIPT_QID, troki.AUTHOR_QID})
        seen: set[str] = set()
        await _live_items(job_id, known, entities, seen, "public_items")
        linked = await asyncio.to_thread(_linked_entities, entities)
        await _live_items(job_id, linked, entities, seen, "linked_items")
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
            include_repair_payloads=False,
        )
        packet = await asyncio.to_thread(
            build_gila_completion_packet,
            audit,
            entities,
            read_only_qids=frozenset(row["qid"] for row in inventory if not row["creator_matches"]),
        )
        cases = _case_inventory(packet, audit)
        additional_cases = [
            *_work_author_cases(canonical, entities),
            *_stale_catalogue_edit_cases(canonical),
        ]
        for case in additional_cases:
            cases.append(case)
            disposition = case["disposition"]
            point = packet["email_points"][case["points"][0] - 1]
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
        duplicates = await asyncio.to_thread(_repeated_reference_counts, entities)
        reference_checks = {
            row["qid"]: duplicates.get(row["qid"], 0) if row["qid"] in entities else None
            for row in inventory
        }
        compressed = b64encode(zlib.compress(json.dumps(cases, ensure_ascii=False).encode()))
        await update_job_progress(
            job_id,
            {
                "phase": "check_report_source",
                "message": "Check that the source still matches the report.",
            },
        )
        async with session_scope() as db:
            projection = StudioCacheProjectionSource(
                db, freshness_timeout_seconds=STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS,
            )
            current = await projection.is_current(str(run_id), snapshot)
            current_records = await load_run_marc_records(db, run_id)
            digest = gila_record_fingerprint(records)
            if not current or digest != gila_record_fingerprint(current_records):
                raise ValueError("The source or canonical build changed. Start a fresh audit.")
            job = await db.get(RunJob, job_id, with_for_update=True)
            await _authorize(db, job)
            job.params = {
                **job.params,
                "_gila_cases": compressed.decode(),
                "_gila_checked_manuscripts": packet["checked_manuscripts"],
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
            error="The audit needs a current canonical Studio build.",
            progress={"phase": "source_rebuild_required"},
        )
    except HTTPException:
        logger.warning("The manuscript audit could not verify the Studio source.", exc_info=True)
        await finish_job(
            job_id,
            status="failed",
            error="The audit could not verify the Studio build before the time limit.",
        )
    except Exception:
        logger.exception("The Gila audit stopped.")
        await finish_job(
            job_id,
            status="failed",
            error="The audit could not confirm its live or source evidence. Start a new audit.",
        )
