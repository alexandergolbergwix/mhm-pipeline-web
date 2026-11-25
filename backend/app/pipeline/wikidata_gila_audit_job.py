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
from app.models.run_job import JOB_KIND_WIKIDATA_GILA_AUDIT, JOB_KIND_WIKIDATA_GILA_REPAIR, RunJob
from app.models.user import User
from app.pipeline.hmo_canonical_wikidata import filter_public_wikidata_items
from app.pipeline.marc_verify_context import canonical_control_number, load_run_marc_records
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress
from app.pipeline.wikidata_gila_audit import build_gila_audit_report
from app.pipeline.wikidata_gila_authority_cache import AuthorityEvidence, CACHE_KEY
from app.pipeline.wikidata_gila_completion import build_gila_completion_packet
from app.pipeline.wikidata_gila_exports import proven_claim_errors, verified_export
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


def gila_audit_export(job: RunJob, kind: str) -> dict | None:
    encoded = job.params.get("_gila_audit_exports")
    return json.loads(zlib.decompress(b64decode(encoded))).get(kind) if encoded else None


def gila_people_marc(job: RunJob) -> dict[str, dict]:
    encoded = job.params.get("_gila_people_marc")
    return json.loads(zlib.decompress(b64decode(encoded))) if encoded else {}


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
    from app.pipeline.wikidata_gila_linked_people import COPY_ROLE_PROPERTIES

    return {
        value["id"]
        for entity in entities.values()
        for pid in ("P1574", "P50", "P1071", *COPY_ROLE_PROPERTIES)
        for claim in entity["claims"].get(pid, [])
        if claim.get("rank") != "deprecated"
        and isinstance(value := claim["mainsnak"].get("datavalue", {}).get("value"), dict)
        and _live_qid(value.get("id"))
    }


def _geographic_context(entities, qids, properties):
    from app.pipeline.wikidata_source_choices import _claim_values

    return {
        value["id"] for qid in qids if qid in entities
        for pid in properties for value in _claim_values(entities[qid], pid)
        if isinstance(value, dict) and _live_qid(value.get("id"))
    }


def _cited_person_roles(entities, manuscript_qids, work_qids):
    from app.pipeline.wikidata_gila_facts import _live_claims

    from app.pipeline.wikidata_gila_linked_people import COPY_ROLE_PROPERTIES

    for qids, properties in ((manuscript_qids, COPY_ROLE_PROPERTIES), (work_qids, ("P50",))):
        for qid in qids:
            entity = entities.get(qid, {})
            for pid in properties:
                for claim in _live_claims(entity, pid):
                    if claim["references"]:
                        yield pid, claim


async def _read_cited_authority_sources(entities, manuscript_qids, work_qids=()):
    from app.pipeline.wikidata_gila_authority_reads import (
        authority_audit_client, library_of_congress_identifier, read_library_of_congress_headings, read_viaf_clusters,
    )
    from app.pipeline.wikidata_gila_identity_evidence import _viaf_citation_ids, _viaf_reference_ids

    manuscript_qids = set(manuscript_qids)
    work_qids = set(work_qids) | _geographic_context(entities, manuscript_qids, ("P1574",))
    ids = {
        identifier for qid in manuscript_qids if qid in entities
        for identifier in _viaf_citation_ids(entities[qid])
    }
    ids.update(
        identifier for qid in work_qids if qid in entities
        for identifier in _viaf_citation_ids(entities[qid], ("P50",))
    )
    async with authority_audit_client() as client:
        viaf_sources = await read_viaf_clusters(client, ids)
        lc_ids = {
            identifier
            for pid, claim in _cited_person_roles(entities, manuscript_qids, work_qids)
            if not (pid == "P11603" and _viaf_reference_ids(claim["references"]) & viaf_sources.keys())
            if (identifier := library_of_congress_identifier(entities.get(claim["value"], {})))
        }
        lc_death_years = {}
        lc_headings = await read_library_of_congress_headings(client, lc_ids, death_years=lc_death_years)
    return viaf_sources, lc_headings, lc_death_years


async def _read_mazal_role_authorities(entities, manuscript_qids, work_qids):
    from app.pipeline.wikidata_gila_authority_reads import mazal_identifier, read_mazal_headings

    manuscript_qids = set(manuscript_qids)
    work_qids = set(work_qids) | _geographic_context(entities, manuscript_qids, ("P1574",))
    identifiers = {
        identifier
        for _, claim in _cited_person_roles(entities, manuscript_qids, work_qids)
        if (identifier := mazal_identifier(entities.get(claim["value"], {})))
    }
    return await read_mazal_headings(identifiers)


async def _read_catalogue_role_evidence(entities, mappings, work_qids):
    from app.pipeline.wikidata_gila_authority_reads import mazal_identifier, read_mazal_people
    from app.pipeline.wikidata_gila_catalogue_authority import (
        REVIEWED_SCRIBE_SOURCES, cited_catalogue_identities, read_catalogue_authority_records,
    )
    from app.pipeline.wikidata_gila_people import catalogue_mazal_names

    manuscript_qids = set(mappings.values())
    work_qids = set(work_qids) | _geographic_context(entities, manuscript_qids, ("P1574",))
    pairs = {
        (cn, identifier)
        for _, claim in _cited_person_roles(entities, manuscript_qids, work_qids)
        for cn, identifier in cited_catalogue_identities(claim)
        if cn in mappings and identifier == mazal_identifier(entities.get(claim["value"], {}))
    }
    corroboration = {
        (proof.corroborating_control, identifier) for cn, identifier in pairs
        if (proof := REVIEWED_SCRIBE_SOURCES.get((cn, identifier))) and proof.corroborating_control
    }
    records = await read_catalogue_authority_records(pairs | corroboration)
    profiles = await read_mazal_people(
        {identifier for _, identifier in records}, source_names=catalogue_mazal_names(records.values()),
    )
    return profiles, records


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
        "source_key",
        "occurrence",
        "person_role",
        "property",
        "source_value",
        "expected_author",
        "expected_value",
        "source_repair",
        "marc_730",
        "holding_label",
        "expected_qualifiers",
        "expected_precision",
        "expected_calendar",
        "expected_unit",
        "expected_forbids_circa",
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
            and row.get("proposal_provenance") == "source_bound"
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
        exact_source = (
            troki.author_statement(item) is not None
            and troki.manuscript_links_work(entities.get(troki.MANUSCRIPT_QID) or {})
            and troki.author_matches(entities.get(troki.AUTHOR_QID) or {})
        )
        is_troki = item.get("local_id") == troki.WORK_LOCAL_ID
        verified = is_troki and exact_source and troki.live_author_matches(live)
        if not missing and not is_troki:
            continue
        exact_author = exact_source and troki.work_matches(live, require_missing=True)
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
                "expected_value": ", ".join(sorted(expected if is_troki else missing)),
                "source_field": "100",
                "property": "P50",
                "expected_values": sorted(expected if is_troki else missing),
                "disposition": "verified" if verified else
                "repair_required" if repair or exact_author else "manual_review",
                "reason": (
                    "troki_author_link_confirmed" if verified else
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
    from app.pipeline.wikidata_gila_case_review import load_authority_evidence
    from app.pipeline.wikidata_gila_people import import_nli_people_marc

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
            proof_authority = await load_authority_evidence(db, run_id)
            fingerprint = gila_record_fingerprint(records)
            cache = None
            repair_receipts = []
            parent_id = job.params.get("_gila_cycle_parent")
            parent = await db.get(RunJob, uuid.UUID(parent_id)) if parent_id else None
            if parent and parent.run_id == run_id and parent.kind == JOB_KIND_WIKIDATA_GILA_REPAIR:
                if job.params.get("_gila_cycle_step") == "verify":
                    repair_receipts = [
                        {"qid": qid, "status": "verified", "corrected": True,
                         "after_revision": receipt.get("after_revision")}
                        for qid, receipt in (parent.params.get("_gila_people_receipts") or {}).items()
                        if receipt.get("state") == "verified"
                    ]
                if job.params.get("_gila_reuse_authority"):
                    stored = parent.params.get(CACHE_KEY)
                    if stored is None:
                        stored = await db.scalar(select(RunJob.params[CACHE_KEY]).where(
                            RunJob.run_id == run_id,
                            RunJob.kind == KIND,
                            RunJob.status == "succeeded",
                            RunJob.params[CACHE_KEY]["fingerprint"].as_string() == fingerprint,
                        ).order_by(RunJob.finished_at.desc()).limit(1))
                    cache = AuthorityEvidence.load(stored, fingerprint)
            source_job_id = job.params.get("_gila_people_source_audit")
            people_marc = None
            if source_job_id:
                source_job = await db.get(RunJob, uuid.UUID(source_job_id))
                if (source_job is None or source_job.run_id != run_id or source_job.kind != KIND
                    or source_job.status != "succeeded"
                    or source_job.params.get("_gila_cycle_parent") != job.params.get("_gila_cycle_parent")
                    or source_job.params.get("_gila_source_fingerprint") != fingerprint):
                    raise ValueError("The MARC source changed since the first audit.")
                people_marc = gila_people_marc(source_job)
        if people_marc is None:
            async def import_progress(processed, total):
                if await is_cancel_requested(job_id):
                    raise AuditCancelledError()
                await update_job_progress(job_id, {
                    "phase": "people_marc", "processed": processed, "total": total, "unit": "records",
                    "message": "Import the full MARC fields for people and roles from NLI.",
                })

            people_marc = await import_nli_people_marc(records, import_progress)
        mappings = _catalogue_mappings(records, ledger)
        seed, _ = load_seed()
        inventory = remaining_reference_inventory()["items"]
        known = (
            set(mappings.values())
            | set(seed["manifest"]["qids"])
            | {row["qid"] for row in inventory}
            | {row["qid"] for row in repair_receipts}
        )
        known.update(
            item.get("existing_qid") for item in canonical if _live_qid(item.get("existing_qid"))
        )
        if any(troki.author_statement(item) is not None for item in canonical):
            known.update({troki.WORK_QID, troki.MANUSCRIPT_QID, troki.AUTHOR_QID})
        if job.params.get("_gila_full_sources"):
            from app.pipeline.wikidata_gila_holdings_contents import reviewed_work_targets
            from app.pipeline.wikidata_gila_sources import source_topic_ids
            from app.pipeline.wikidata_gila_uniform_titles import uniform_title_targets

            known.update(reviewed_work_targets())
            known.update(uniform_title_targets())
            known.update(await asyncio.to_thread(source_topic_ids, people_marc.values()))
        seen: set[str] = set()
        await _live_items(job_id, known, entities, seen, "public_items")
        linked = await asyncio.to_thread(_linked_entities, entities)
        await _live_items(job_id, linked, entities, seen, "linked_items")
        linked_works = await asyncio.to_thread(_geographic_context, entities, mappings.values(), ("P1574",))
        work_people = await asyncio.to_thread(_geographic_context, entities, linked_works, ("P50",))
        await _live_items(job_id, work_people, entities, seen, "linked_items")
        places = await asyncio.to_thread(_geographic_context, entities, mappings.values(), ("P1071",))
        parents = await asyncio.to_thread(_geographic_context, entities, places, ("P131", "P17"))
        await _live_items(job_id, parents, entities, seen, "linked_items")
        regions = await asyncio.to_thread(_geographic_context, entities, parents, ("P131",))
        await _live_items(job_id, regions, entities, seen, "linked_items")
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
        work_qids = {item.get("existing_qid") for item in canonical if item.get("entity_type") == "work"}
        (viaf_sources, lc_headings, lc_death_years), mazal_headings, (mazal_people, catalogue_records) = await asyncio.gather(
            _read_cited_authority_sources(entities, mappings.values(), work_qids),
            _read_mazal_role_authorities(entities, mappings.values(), work_qids),
            _read_catalogue_role_evidence(entities, mappings, work_qids),
        )
        from app.pipeline.wikidata_gila_bibframe_people import cited_frame_controls, read_bibframes
        from app.pipeline.wikidata_gila_linked_people import cited_bib_controls, read_linked_bibs
        from app.pipeline.wikidata_gila_mazal_viaf import cited_mazal_controls, read_viaf_mazal

        linked_controls = set()
        frame_controls = set()
        viaf_mazal_ids = set()
        for _, claim in _cited_person_roles(entities, mappings.values(), work_qids | linked_works):
            linked_controls.update(cited_bib_controls(claim) & people_marc.keys())
            frame_controls.update(cited_frame_controls(claim) & people_marc.keys())
            viaf_mazal_ids.update(cited_mazal_controls(claim))

        async def linked_progress(processed, total):
            if await is_cancel_requested(job_id):
                raise AuditCancelledError()
            await update_job_progress(job_id, {
                "phase": "linked_people", "processed": processed, "total": total, "unit": "records",
                "message": "Verify the NLI authority links cited on live statements.",
            })

        async def read(group, requested, fetch):
            return await cache.read(group, requested, fetch, linked_progress) if cache else await fetch(
                requested, linked_progress,
            )

        linked_bibs = await read("bibs", linked_controls, read_linked_bibs)
        linked_frames = await read("frames", frame_controls, read_bibframes)
        linked_viaf = await read("viaf", viaf_mazal_ids, read_viaf_mazal)
        from app.pipeline.wikidata_gila_gnd_evidence import cited_gnd_ids, read_gnd_sources

        gnd_ids = {identifier for qid in mappings.values() if qid in entities
                   for identifier in cited_gnd_ids(entities[qid])}
        gnd_sources = await read_gnd_sources(gnd_ids) if gnd_ids else {}
        audit = await asyncio.to_thread(
            build_gila_audit_report,
            records,
            canonical,
            qids_by_cn=mappings,
            live_entities=entities,
            include_repair_payloads=False,
            repair_authority_by_cn=proof_authority,
            repair_viaf_sources=viaf_sources,
            repair_gnd_sources=gnd_sources,
            people_marc=people_marc,
            full_source_coverage=bool(job.params.get("_gila_full_sources")),
            repair_lc_headings=lc_headings,
            repair_lc_death_years=lc_death_years,
            repair_mazal_headings=mazal_headings,
            repair_mazal_people=mazal_people,
            repair_catalogue_records=catalogue_records,
            repair_linked_bibs=linked_bibs,
            repair_linked_frames=linked_frames,
            repair_linked_viaf=linked_viaf,
        )
        citation_reconciliations = [
            {**citation, "control_number": record["control_number"], "qid": record["qid"]}
            for record in audit["records"] for citation in record.get("citation_reconciliations", ())
        ]
        packet = await asyncio.to_thread(
            build_gila_completion_packet,
            audit,
            entities,
            repair_receipts=repair_receipts,
            read_only_qids=frozenset(row["qid"] for row in inventory if not row["creator_matches"]),
        )
        packet["requirement_coverage"]["reconciled_legacy_citations"] = len(citation_reconciliations)
        cases = _case_inventory(packet, audit)
        person_cases = [row for record in audit["records"] for row in record["cases"] if row.get("person_role")]
        people_summary = {
            "records_with_full_marc": len(people_marc),
            "source_roles": len(person_cases),
            "roles_verified": sum(bool(row.get("role_verified")) for row in person_cases),
            "authority_links_verified": sum(bool(row.get("authority_verified")) for row in person_cases),
            "uncertainty_preserved": sum(row["reason"] == "recorded_person_uncertainty" for row in person_cases),
        }
        additional_cases = [
            *_work_author_cases(canonical, entities),
            *_stale_catalogue_edit_cases(canonical),
        ]
        for case in additional_cases:
            disposition = case["disposition"]
            if disposition == "verified":
                packet["requirement_coverage"]["verified_claims"] += 1
            else:
                cases.append(case)
                if disposition == "repair_required":
                    packet["requirement_coverage"]["pending_repairs"] += 1
            point = packet["email_points"][case["points"][0] - 1]
            counts = point["counts"]
            counts[disposition] = counts.get(disposition, 0) + 1
            if len(counts) == 1 and counts[disposition] == 1 or (
                disposition != "verified" and point["status"] == "verified"
            ) or (
                point["status"] == "manual_review" and disposition == "repair_required"
            ):
                point["status"] = disposition
            packet["disposition_counts"][disposition] = (
                packet["disposition_counts"].get(disposition, 0) + 1
            )
        exports = {
            "verified": verified_export(packet, additional_cases),
            "proven-claim-errors": await proven_claim_errors(packet, entities, people_marc),
        }
        contents_coverage = None
        if audit["full_source_coverage"]:
            from app.pipeline.wikidata_gila_contents import contents_passage_coverage

            contents_coverage = await asyncio.to_thread(
                contents_passage_coverage, packet["source_passages"],
                exports["verified"]["verified_checks"], people_marc,
            )
        packet["all_requirements_verified"] = all(
            point["status"] == "verified" for point in packet["email_points"]
        )
        ready_counts = {}
        for case in cases:
            if case.get("can_prepare"):
                for point in case["points"]:
                    ready_counts[point] = ready_counts.get(point, 0) + 1
        for point in packet["email_points"]:
            point["ready_count"] = ready_counts.get(point["point"], 0)
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
                "_gila_citation_reconciliations": citation_reconciliations,
                "_gila_audit_exports": b64encode(zlib.compress(json.dumps(
                    exports, ensure_ascii=False, separators=(",", ":"),
                ).encode())).decode(),
                "_gila_people_marc": b64encode(zlib.compress(json.dumps(
                    people_marc, ensure_ascii=False, separators=(",", ":"),
                ).encode())).decode(),
                "_gila_checked_manuscripts": packet["checked_manuscripts"],
                "_gila_source_fingerprint": digest,
                "_gila_build_revision": snapshot.revision,
                "_gila_build_digest": snapshot.digest,
                **({CACHE_KEY: cache.dump()} if cache is not None else {}),
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
                "people_coverage": people_summary,
                **({"contents_passage_coverage": contents_coverage} if contents_coverage is not None else {}),
                "audit_exports": {
                    "verified_statements": exports["verified"]["matched_statement_count"],
                    "proven_claim_errors": len(exports["proven-claim-errors"]["claims"]),
                },
                **({"authority_reads_reused": cache.reused} if cache is not None else {}),
                "full_source_coverage": audit["full_source_coverage"],
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
