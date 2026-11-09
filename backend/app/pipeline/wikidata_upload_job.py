"""Background Wikidata upload / dry-run job (two-pass deferred links, W-192)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace
from typing import Any

from sqlalchemy import select, text
from starlette.concurrency import run_in_threadpool

from app.db import session_scope
from app.models.item_override import WikidataItemOverride
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.models.wikibase_cloud_write import CHANNEL_WIKIDATA_UPLOAD
from app.pipeline import wikidata_studio, wikidata_upload
from app.pipeline.run_job_service import (
    finish_job,
    is_cancel_requested,
    update_job_progress,
)
from app.pipeline.wikidata_qid_ledger import ledger_key_for_item
from app.services.wikibase_audit import WikibaseAuditContext

logger = logging.getLogger(__name__)

STEP_WRITE_ITEMS = 1
STEP_ADD_LINKS = 2
UPLOAD_STEP_TOTAL = 2


def _deferred_author_fallback(
    original: Any, rewritten: Any, skipped_person_names: dict[str, str],
) -> Any | None:
    from converter.wikidata.uploader import statement_has_unresolved_local  # noqa: PLC0415

    value = getattr(original, "value", None)
    if (getattr(original, "property_id", "") != "P50" or not isinstance(value, str)
            or not value.startswith("__LOCAL:") or getattr(rewritten, "value", None) != value
            or getattr(rewritten, "property_id", "") != "P50"
            or any(getattr(statement, "value_type", "") not in {
                "item", "wikibase-item", "wikibase-entityid",
            } for statement in (original, rewritten))
            or any(getattr(statement, "rank", None) != "normal"
                   or statement.qualifiers or statement.references or statement.reference_blocks
                   for statement in (original, rewritten))):
        return None
    target = value.removeprefix("__LOCAL:")
    if not target or any(character.isspace() for character in target):
        return None
    name = skipped_person_names.get(target, "").strip()
    if not name:
        return None
    fallback = wikidata_upload.rewrite_author_link_to_name_string(rewritten, name)
    return None if statement_has_unresolved_local(fallback, {}) else fallback


def slim_upload_progress_outcome(outcome: Any) -> dict[str, Any]:
    """Slim per-item row for live upload progress (modal + table patches)."""
    return {
        "local_id": outcome.local_id,
        "label": outcome.label,
        "entity_type": outcome.entity_type,
        "status": outcome.status,
        "qid": outcome.qid,
        "wikibase_id": outcome.qid,
        "message": outcome.message,
        "audit_status": getattr(outcome, "audit_status", ""),
    }


def upload_outcome_counts(outcomes: list[Any]) -> dict[str, int]:
    """Aggregate upload statuses for the live modal count strip."""
    counts = {
        "created": 0,
        "updated": 0,
        "adopted": 0,
        "blocked": 0,
        "skipped": 0,
        "failed": 0,
        "pending": 0,
    }
    for o in outcomes:
        status = str(getattr(o, "status", "") or "").lower()
        if status in {"success", "created", "would_create"}:
            counts["created"] += 1
        elif status in {"updated", "exists", "would_update"}:
            counts["updated"] += 1
        elif status in {"adopted", "would_adopt"}:
            counts["adopted"] += 1
        elif status in {"blocked", "would_block"}:
            counts["blocked"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
        elif status == "failed":
            counts["failed"] += 1
        elif status == "pending":
            counts["pending"] += 1
    return counts


def estimate_remaining_seconds(
    done: int,
    total: int,
    elapsed: float,
    *,
    min_samples: int = 3,
    cap_seconds: int = 24 * 3600,
) -> int | None:
    """ETA for the current step. Hidden until ``min_samples`` completions."""
    if done < min_samples or total <= 0 or elapsed <= 0 or done <= 0:
        return None
    remaining_items = max(0, total - done)
    if remaining_items == 0:
        return 0
    est = remaining_items * (elapsed / done)
    return int(min(max(est, 0), cap_seconds))


def _item_display_label(item: Any) -> str:
    labels = getattr(item, "labels", None) or {}
    if isinstance(labels, dict) and labels:
        return str(
            labels.get("en") or labels.get("he") or next(iter(labels.values()), "") or "",
        )
    return ""


def _step_blob(
    *,
    step_id: str,
    label: str,
    status: str,
    processed: int,
    total: int,
    unit: str,
    eta_seconds: int | None,
    current_label: str,
) -> dict[str, Any]:
    return {
        "id": step_id,
        "label": label,
        "status": status,
        "processed": processed,
        "total": total,
        "unit": unit,
        "eta_seconds": eta_seconds,
        "current_label": current_label,
    }


def build_upload_progress(
    *,
    phase: str,
    message: str,
    upload_target: str,
    step: int,
    item_done: int,
    item_total: int,
    link_done: int,
    link_total: int,
    current_label: str,
    eta_seconds: int | None,
    elapsed_seconds: float,
    item_outcome: dict[str, Any] | None = None,
    recent_item_outcomes: list[dict[str, Any]] | None = None,
    outcome_counts: dict[str, int] | None = None,
    processing_local_id: str | None = None,
) -> dict[str, Any]:
    """W-112/W-113 two-step progress plus modal ``steps`` + ETA (W-192)."""
    step1_status = "running" if step == STEP_WRITE_ITEMS else "done"
    if step < STEP_WRITE_ITEMS:
        step1_status = "pending"
    step2_status = "pending"
    if step == STEP_ADD_LINKS:
        step2_status = "running"
    elif step > STEP_ADD_LINKS:
        step2_status = "done"

    item_eta = eta_seconds if step == STEP_WRITE_ITEMS else (0 if step > STEP_WRITE_ITEMS else None)
    link_eta = eta_seconds if step == STEP_ADD_LINKS else None
    if step > STEP_ADD_LINKS:
        link_eta = 0

    sub_processed = item_done if step == STEP_WRITE_ITEMS else link_done
    sub_total = item_total if step == STEP_WRITE_ITEMS else link_total
    sub_unit = "items" if step == STEP_WRITE_ITEMS else "links"
    if phase in {"cancelled", "failed"}:
        step1_status = "skipped" if step == STEP_WRITE_ITEMS and item_done < item_total else step1_status
        if step < STEP_ADD_LINKS:
            step2_status = "skipped"

    outer = STEP_WRITE_ITEMS if step <= STEP_WRITE_ITEMS else STEP_ADD_LINKS
    if phase == "done" or step > STEP_ADD_LINKS:
        outer = UPLOAD_STEP_TOTAL
    progress: dict[str, Any] = {
        "phase": phase,
        "processed": outer,
        "total": UPLOAD_STEP_TOTAL,
        "unit": "steps",
        "message": message,
        "upload_target": upload_target,
        "sub_processed": sub_processed,
        "sub_total": sub_total,
        "sub_unit": sub_unit,
        "sub_message": current_label or message,
        "eta_seconds": eta_seconds,
        "elapsed_seconds": int(elapsed_seconds),
        "current_label": current_label,
        "processing_local_id": processing_local_id,
        "steps": [
            _step_blob(
                step_id="write_items",
                label="Step 1 — Write items",
                status=step1_status,
                processed=item_done,
                total=item_total,
                unit="items",
                eta_seconds=item_eta,
                current_label=current_label if step == STEP_WRITE_ITEMS else (
                    "Complete" if step1_status == "done" else ""
                ),
            ),
            _step_blob(
                step_id="add_connections",
                label="Step 2 — Add connections",
                status=step2_status,
                processed=link_done,
                total=link_total,
                unit="links",
                eta_seconds=link_eta,
                current_label=current_label if step == STEP_ADD_LINKS else (
                    "Waiting for step 1" if step2_status == "pending" else (
                        "Complete" if step2_status == "done" else ""
                    )
                ),
            ),
        ],
    }
    if item_outcome is not None:
        progress["item_outcome"] = item_outcome
    if recent_item_outcomes is not None:
        progress["recent_item_outcomes"] = recent_item_outcomes
    if outcome_counts is not None:
        progress["outcome_counts"] = outcome_counts
    return progress


def _link_outcome_dict(row: wikidata_upload.DeferredLinkOutcome) -> dict[str, Any]:
    return {
        "source_local_id": row.source_local_id,
        "property_id": row.property_id,
        "target_local_id": row.target_local_id,
        "status": row.status,
        "message": row.message,
        "qid": row.qid,
    }


def _resolve_existing_qid_map(
    native: list[Any],
    ledger: dict[str, str],
    *,
    is_test: bool,
) -> dict[str, str]:
    """Return ``{local_id: qid}`` for items that already carry a live/ledger QID."""
    from app.pipeline.wikidata_qid_ledger import (  # noqa: PLC0415
        ledger_namespace,
        lookup_ledger_qid,
    )

    ns = ledger_namespace(is_test=is_test)
    known: dict[str, str] = {}
    if is_test:
        local_counts = Counter(wikidata_studio.local_id_for_item(it) for it in native)
        key_counts = Counter(ledger_key_for_item(it, ns) for it in native)
        qid_counts = Counter(
            qid for key, qid in ledger.items()
            if key.startswith(f"{ns}:") and isinstance(qid, str)
            and re.fullmatch(r"Q[1-9][0-9]*", qid)
        )
        for it in native:
            lid = wikidata_studio.local_id_for_item(it)
            key = ledger_key_for_item(it, ns)
            qid = ledger.get(key)
            if (lid and local_counts[lid] == 1 and key_counts[key] == 1
                    and isinstance(qid, str) and re.fullmatch(r"Q[1-9][0-9]*", qid)
                    and qid_counts[qid] == 1):
                known[lid] = qid
        return known
    for it in native:
        lid = wikidata_studio.local_id_for_item(it)
        if not lid:
            continue
        qid = str(getattr(it, "existing_qid", "") or "").strip()
        if not qid and ledger:
            qid = str(lookup_ledger_qid(ledger, ledger_key_for_item(it, ns)) or "").strip()
        if qid.startswith("Q"):
            known[lid] = qid
    return known


def _register_test_targets(
    uploader: Any, native: list[Any], ledger: dict[str, str],
) -> dict[str, str]:
    known = _resolve_existing_qid_map(native, ledger, is_test=True)
    if any(known.get(lid) != qid for lid, qid in uploader._verified_test_targets.items()):
        raise ValueError("Test-wiki ledger proof changed during the job")
    for item in native:
        if wikidata_studio.local_id_for_item(item) in known:
            uploader.register_test_ledger_target(item, ledger)
    return known


def _test_ledger_conflicts(native: list[Any], ledger: dict[str, str]) -> bool:
    local_counts = Counter(wikidata_studio.local_id_for_item(item) for item in native)
    key_counts = Counter(ledger_key_for_item(item, "wikidata-test") for item in native)
    if any(not lid or count != 1 for lid, count in local_counts.items()):
        return True
    if any(count != 1 for count in key_counts.values()):
        return True
    known = _resolve_existing_qid_map(native, ledger, is_test=True)
    return any(
        ledger_key_for_item(item, "wikidata-test") in ledger
        and wikidata_studio.local_id_for_item(item) not in known
        for item in native
    )


async def _persist_test_outcomes(
    item: Any, outcomes: list[Any], audit_ctx: Any, native: list[Any],
    ledger: dict[str, str], created_qids: dict[str, str], lock: asyncio.Lock,
    uploaders: list[Any],
) -> None:
    from app.models.wikibase_entity_mapping import (  # noqa: PLC0415
        ENTITY_KIND_INSTANCE,
        WikibaseEntityMapping,
    )
    from app.pipeline.wikidata_qid_ledger import load_global_ledger  # noqa: PLC0415

    async with lock:
        async with session_scope() as db:
            await wikidata_upload.persist_upload_outcomes(
                db, [item], outcomes, audit_ctx=audit_ctx, is_test=True, record_ledger=False,
            )
            if db.get_bind().dialect.name == "postgresql":
                # Shards must serialize ledger claims before they publish target proof.
                await db.execute(text("SELECT pg_advisory_xact_lock(77298752415862491)"))
            current = await load_global_ledger(db, is_test=True, reject_ambiguous=True)
            key = ledger_key_for_item(item, "wikidata-test")
            lid = wikidata_studio.local_id_for_item(item)
            written_qids: list[str] = []
            for row in outcomes:
                if row.status not in {"created", "updated", "adopted", "exists"}:
                    continue
                qid = row.qid
                if (row.local_id != lid or not isinstance(qid, str)
                        or not re.fullmatch(r"Q[1-9][0-9]*", qid)):
                    raise ValueError("Invalid test-wiki upload outcome")
                if (sum(ledger_key_for_item(it, "wikidata-test") == key for it in native) != 1
                        or sum(wikidata_studio.local_id_for_item(it) == lid for it in native) != 1
                        or (key in current and current[key] != qid)
                        or any(k != key and v == qid for k, v in current.items())):
                    raise ValueError("Conflicting test-wiki ledger mapping")
                if key not in current:
                    db.add(WikibaseEntityMapping(
                        ontology_uri=key, entity_kind=ENTITY_KIND_INSTANCE, wikibase_id=qid,
                        run_id=None, local_key=lid, label=row.label or lid,
                    ))
                    current[key] = qid
                written_qids.append(qid)
            await db.commit()
            current = await load_global_ledger(db, is_test=True, reject_ambiguous=True)
            for qid in written_qids:
                if _resolve_existing_qid_map(native, current, is_test=True).get(lid) != qid:
                    raise ValueError("Test-wiki ledger mapping is ambiguous")
            ledger.clear()
            ledger.update(current)
            for uploader in uploaders:
                _register_test_targets(uploader, native, ledger)
            created_qids.clear()
            created_qids.update(_resolve_existing_qid_map(native, ledger, is_test=True))


async def _owner_run_record_ids(db: Any, run_id: uuid.UUID) -> set[str]:
    from app.models.run import RunRecord  # noqa: PLC0415
    from app.pipeline.marc_verify_context import canonical_control_number  # noqa: PLC0415

    rows = await db.execute(select(RunRecord.control_number).where(RunRecord.run_id == run_id))
    return {canonical_control_number(value) for value in rows.scalars().all()}


def _owner_targets(
    native: list[Any], ledger: dict[str, str], run_record_ids: set[str],
) -> MappingProxyType:
    from app.pipeline.marc_verify_context import canonical_control_number  # noqa: PLC0415

    local_counts = Counter(wikidata_studio.local_id_for_item(it) for it in native)
    candidates: dict[str, str] = {}
    qid_counts: Counter[str] = Counter()
    for item in native:
        local_id = wikidata_studio.local_id_for_item(item)
        built = getattr(item, "existing_qid", None)
        if isinstance(built, str) and re.fullmatch(r"Q[1-9][0-9]*", built):
            qid_counts[built] += 1
        entity_type = str(getattr(item, "entity_type", "") or "").lower()
        match = re.fullmatch(r"(?:QDraft_MS_|marc:)([0-9]{5,})", local_id or "")
        records = {canonical_control_number(record) for record in getattr(item, "records", []) or []}
        if (entity_type != "manuscript" or match is None
                or match.group(1) not in run_record_ids or records != {match.group(1)}):
            continue
        ledger_qid = ledger.get(f"wikidata:marc:{local_id}")
        if ledger_qid != built and isinstance(ledger_qid, str) and re.fullmatch(r"Q[1-9][0-9]*", ledger_qid):
            qid_counts[ledger_qid] += 1
        if not local_id or local_counts[local_id] != 1:
            continue
        if built and ledger_qid and built != ledger_qid:
            continue
        qid = ledger_qid
        if isinstance(qid, str) and re.fullmatch(r"Q[1-9][0-9]*", qid):
            candidates[local_id] = qid
    return MappingProxyType({lid: qid for lid, qid in candidates.items() if qid_counts[qid] == 1})


class StudioSourceChangedError(RuntimeError):
    def __init__(self, run_id: uuid.UUID) -> None:
        super().__init__(
            f"Studio source for run {run_id} changed during upload; "
            "no further writes. Rebuild the Studio source and upload again.",
        )


async def _assert_studio_source_unchanged(
    run_id: uuid.UUID,
    *,
    source: str,
    approved_only: bool,
    fingerprint: str,
    summary: dict[str, Any],
) -> None:
    from app.routers.wikidata_studio import (  # noqa: PLC0415
        STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS,
        _studio_cache_is_current,
    )

    async with session_scope() as db:
        current = await _studio_cache_is_current(
            db, run_id, source=source, approved_only=approved_only,
            fingerprint=fingerprint, summary=summary,
            timeout_seconds=STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS,
        )
    if not current:
        raise StudioSourceChangedError(run_id)


WRITE_FRESHNESS_BATCH_SIZE = 50


class _BatchFreshnessGate:
    """Run the Studio freshness check before the first write of each batch.

    The check runs only when the write count reaches a multiple of the batch
    size. A failed check raises before the write, and the next write checks
    again because the count did not advance.
    """

    def __init__(self, check: Callable[[], Awaitable[None]]) -> None:
        self._check = check
        self._writes = 0
        self._lock = asyncio.Lock()

    async def before_write(self) -> None:
        async with self._lock:
            if self._writes % WRITE_FRESHNESS_BATCH_SIZE == 0:
                await self._check()
            self._writes += 1


async def run_wikidata_upload_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        run_id = job.run_id
        project_id = job.project_id
        params = job.params or {}
        if "source" in params and params["source"] not in ("canonical", "legacy"):
            raise ValueError("source must be canonical or legacy")
        owner_only = params.get("owner_only", False)
        if type(owner_only) is not bool:
            raise ValueError("owner_only must be a boolean")
        if owner_only:
            for name in ("dry_run", "update_existing", "approved_only", "item_approved_only", "ai_verified_only"):
                if name in params and type(params[name]) is not bool:
                    raise ValueError(f"{name} must be a boolean")
            if "local_ids" in params:
                ids = params["local_ids"]
                if (not isinstance(ids, list) or not ids
                        or any(not isinstance(lid, str) or not lid.strip() for lid in ids)):
                    raise ValueError("local_ids must be a non-empty list of non-empty strings")
        mode = wikidata_upload.resolve_upload_mode(
            params.get("upload_target"),
            dry_run=params.get("dry_run"),
        )
        if owner_only and (mode.is_test or params.get("_step1_state")):
            raise ValueError("owner_only refuses test targets and shard state")
        approved_only = bool(params.get("approved_only", True))
        source = str(params.get("source") or "canonical")
        item_approved_only = bool(params.get("item_approved_only", False))
        update_existing = bool(params.get("update_existing", True))
        ai_verified_only = bool(params.get("ai_verified_only", False))
        token = str(params.get("_wikidata_token") or "")
        scoped_ids = params.get("local_ids")
        scope: set[str] | None = None
        if isinstance(scoped_ids, list) and scoped_ids:
            scope = {str(x) for x in scoped_ids if str(x).strip()}
        auth = SimpleNamespace(user=SimpleNamespace(id=job.created_by))

        from app.routers.wikidata_studio import (  # noqa: PLC0415
            STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS,
            _build_native_items,
            _get_studio_cache_row,
        )

        studio_cache = await _get_studio_cache_row(db, run_id, approved_only, source)
        build_options = {
            "approved_only": approved_only,
            "source": source,
            "freshness_timeout_seconds": STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS,
        }
        if owner_only:
            build_options["adopt_probe"] = False
        native = await _build_native_items(db, run_id, auth, **build_options)
        if studio_cache is None:
            raise StudioSourceChangedError(run_id)
        expected_fingerprint = studio_cache.input_fingerprint
        expected_summary = dict(studio_cache.summary or {})
        all_native = list(native) if owner_only else []
        override_rows = (
            await db.execute(
                select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
            )
        ).scalars().all()
        if owner_only:
            invalid_rank_ids = {
                row.local_id for row in override_rows
                if any(
                    isinstance(stmt, dict)
                    and (stmt.get("property_id") or stmt.get("property")) == "P3959"
                    and stmt.get("rank") not in ("normal", "preferred")
                    for stmt in (row.add_statements or [])
                )
            }
            for item in native:
                if wikidata_studio.local_id_for_item(item) in invalid_rank_ids:
                    for stmt in getattr(item, "statements", []) or []:
                        if getattr(stmt, "property_id", None) == "P3959":
                            stmt.rank = ""
        if item_approved_only:
            approved_ids = {r.local_id for r in override_rows if r.approved}
            native = [
                it for it in native
                if wikidata_studio.local_id_for_item(it) in approved_ids
            ]
        verified_ids: set[str] | None = None
        if ai_verified_only:
            verified_ids = {
                r.local_id
                for r in override_rows
                if isinstance(r.ai_verdict, dict)
                and str(r.ai_verdict.get("overall") or "").lower() in {"full", "pass"}
            }
        accept_by_local_id = {
            r.local_id: wikidata_upload.ForeignAccept(
                accept_foreign_modify=bool(r.accept_foreign_modify),
                accepted_foreign_qid=str(r.accepted_foreign_qid),
            )
            for r in override_rows
            if r.accept_foreign_modify and r.accepted_foreign_qid
        }
        if owner_only:
            from app.pipeline.wikidata_qid_ledger import load_global_ledger  # noqa: PLC0415
            ledger = await load_global_ledger(db, is_test=False, reject_ambiguous=True)
            run_record_ids = await _owner_run_record_ids(db, run_id)
        elif mode.is_test:
            from app.pipeline.wikidata_qid_ledger import load_global_ledger  # noqa: PLC0415
            ledger = await load_global_ledger(db, is_test=True, reject_ambiguous=True)
        else:
            ledger = await wikidata_upload.load_ledger_for_prepare(db, is_test=mode.is_test)

    from converter.wikidata.uploader import (  # noqa: PLC0415
        first_local_target,
        partition_unresolved_local,
        resolve_statement_locals,
        sort_items_for_upload,
    )

    async def _assert_source_current() -> None:
        await _assert_studio_source_unchanged(
            run_id, source=source, approved_only=approved_only,
            fingerprint=expected_fingerprint, summary=expected_summary,
        )

    write_gate = _BatchFreshnessGate(_assert_source_current)
    native = sort_items_for_upload(list(native))
    owner_targets = _owner_targets(all_native, ledger, run_record_ids) if owner_only else None
    native_by_id = {wikidata_studio.local_id_for_item(it): it for it in native}
    created_qids: dict[str, str] = _resolve_existing_qid_map(
        native, ledger, is_test=mode.is_test,
    )
    if mode.is_test and _test_ledger_conflicts(native, ledger):
        await finish_job(job_id, status=JOB_STATUS_FAILED,
                         error="Ambiguous test-wiki ledger or local identity; refusing upload.")
        return
    if owner_only:
        created_qids = dict(owner_targets)
        accept_by_local_id = {}
    test_ledger_lock = asyncio.Lock()
    pass1_native = native
    if scope is not None:
        pass1_native = [
            it for it in pass1_native
            if wikidata_studio.local_id_for_item(it) in scope
        ]
    if scope is None or owner_only:
        if verified_ids is not None:
            pass1_native = [
                it for it in pass1_native
                if wikidata_studio.local_id_for_item(it) in verified_ids
            ]
        if not update_existing:
            pass1_native = [
                it for it in pass1_native
                if wikidata_studio.local_id_for_item(it) not in created_qids
            ]
    deferred_by_local_id: dict[str, list[Any]] = {}

    item_total = len(pass1_native)
    link_total = 0
    label = {
        wikidata_upload.UPLOAD_TARGET_DRY_RUN: "Dry-run",
        wikidata_upload.UPLOAD_TARGET_TEST: "Test upload",
        wikidata_upload.UPLOAD_TARGET_LIVE: "Live upload",
    }.get(mode.target, "Upload")

    await update_job_progress(job_id, build_upload_progress(
        phase="uploading",
        message="Step 1 of 2: writing items",
        upload_target=mode.target,
        step=STEP_WRITE_ITEMS,
        item_done=0,
        item_total=item_total,
        link_done=0,
        link_total=0,
        current_label=f"{label}: {item_total} items…",
        eta_seconds=None,
        elapsed_seconds=0,
    ))

    if await is_cancel_requested(job_id):
        await finish_job(
            job_id,
            status=JOB_STATUS_CANCELLED,
            result={
                "outcomes": [],
                "link_outcomes": [],
                "processed": 0,
                "dry_run": mode.dry_run,
                "upload_target": mode.target,
                "moratorium_lifted": mode.moratorium_lifted,
                "test_mode": mode.test_mode,
            },
            progress=build_upload_progress(
                phase="cancelled",
                message="Cancelled by user",
                upload_target=mode.target,
                step=STEP_WRITE_ITEMS,
                item_done=0,
                item_total=item_total,
                link_done=0,
                link_total=0,
                current_label="Cancelled",
                eta_seconds=None,
                elapsed_seconds=0,
            ),
        )
        return

    if (not mode.dry_run or owner_only) and not token:
        await finish_job(job_id, status=JOB_STATUS_FAILED, error="missing Wikidata token")
        return

    shared_uploader: Any | None = None
    if token:
        from converter.wikidata.uploader import WikidataUploader  # noqa: PLC0415

        shared_uploader = WikidataUploader(
            token=token, is_test=mode.is_test, batch_mode=True, allow_live=mode.allow_live,
        )
        if owner_only or not mode.dry_run:
            try:
                await run_in_threadpool(
                    shared_uploader.ensure_owner_only_authenticated
                    if owner_only else shared_uploader.ensure_authenticated
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("wikidata upload login failed for %s", run_id)
                await finish_job(job_id, status=JOB_STATUS_FAILED,
                                 error=f"Wikidata login failed before any writes: {exc}")
                return

    quality_preflight = await run_in_threadpool(
        wikidata_upload.build_upload_corpus_quality_preflight,
        native,
    )
    if quality_preflight.execution_findings:
        sample = "; ".join(quality_preflight.execution_findings[:3])
        suffix = (
            f" (+{len(quality_preflight.execution_findings) - 3} more)"
            if len(quality_preflight.execution_findings) > 3 else ""
        )
        error = (
            "Wikidata corpus quality preflight blocked the full execution: "
            f"{sample}{suffix}"
        )
        await finish_job(
            job_id,
            status=JOB_STATUS_FAILED,
            error=error,
            result={
                "dry_run": mode.dry_run,
                "upload_target": mode.target,
                "moratorium_lifted": mode.moratorium_lifted,
                "test_mode": mode.test_mode,
                "outcomes": [],
                "link_outcomes": [],
                "quality_finding_count": len(quality_preflight.execution_findings),
                "quality_findings": list(quality_preflight.execution_findings[:50]),
            },
            progress=build_upload_progress(
                phase="failed",
                message="Blocked by the corpus quality preflight",
                upload_target=mode.target,
                step=STEP_WRITE_ITEMS,
                item_done=0,
                item_total=item_total,
                link_done=0,
                link_total=0,
                current_label="Quality preflight failed",
                eta_seconds=None,
                elapsed_seconds=0,
            ),
        )
        return

    # Per-slot uploaders: each instance owns one login session and
    # requests.Session is not safe for concurrent writers, so the item and
    # link stages lease a slot per in-flight write (4× overlap of the
    # network waits; the wiki sees the same per-slot rate limit).
    slot_uploaders: list[Any] = [shared_uploader]
    if token and not mode.dry_run and not owner_only:
        for _ in range(3):
            try:
                extra = WikidataUploader(
                    token=token,
                    is_test=mode.is_test,
                    batch_mode=True,
                    allow_live=mode.allow_live,
                )
                await run_in_threadpool(extra.ensure_authenticated)
                if shared_uploader is not None and hasattr(extra, "share_caches_from"):
                    extra.share_caches_from(shared_uploader)
                slot_uploaders.append(extra)
            except Exception:  # noqa: BLE001 — degrade to fewer slots
                break
    uploader_slots: asyncio.Queue = asyncio.Queue()
    for _up in slot_uploaders:
        if mode.is_test and _up is not None:
            _register_test_targets(_up, native, ledger)
        uploader_slots.put_nowait(_up)

    from app.pipeline.wikidata_existence import (  # noqa: PLC0415
        prefetch_entities_for_upload,
    )
    from app.pipeline.wikidata_upload import _make_reconciler  # noqa: PLC0415
    from converter.wikidata.reconciler import (  # noqa: PLC0415
        prewarm_reconciler_for_items,
    )

    outcomes: list[Any] = []
    link_outcomes: list[wikidata_upload.DeferredLinkOutcome] = []
    injected_state = params.get("_step1_state") or {}
    conflicting_shard_ids: set[str] = set()
    step1_items = pass1_native
    reconciler = _make_reconciler()
    if injected_state:
        # Sharded step-1 already ran in parallel Modal containers; rehydrate
        # its state and skip the serial loop. Step-2 (links) and the terminal
        # state stay in this function unchanged.
        outcomes.extend(
            wikidata_upload.UploadOutcome(**row)
            for row in injected_state.get("outcomes") or []
        )
        if not mode.is_test:
            created_qids.update({
                str(k): str(v)
                for k, v in (injected_state.get("created_qids") or {}).items()
            })
        else:
            for lid, qid in (injected_state.get("created_qids") or {}).items():
                if created_qids.get(str(lid)) != qid:
                    created_qids.pop(str(lid), None)
                    conflicting_shard_ids.add(str(lid))
        for _lid, _rows in (injected_state.get("deferred") or {}).items():
            deferred_by_local_id[str(_lid)] = wikidata_studio.native_item_from_payload(
                {"statements": _rows},
            ).statements
        link_total = sum(len(v) for v in deferred_by_local_id.values())
        step1_items = []
        prefetch_qids = sorted({
            str(created_qids.get(src_id) or "").strip()
            for src_id in deferred_by_local_id
            if str(created_qids.get(src_id) or "").strip().startswith("Q")
        })
    else:
        if not owner_only:
            await run_in_threadpool(prewarm_reconciler_for_items, reconciler, pass1_native)
        reconciler_qids = {
            str(v).strip()
            for v in (getattr(reconciler, "_cache", {}) or {}).values()
            if isinstance(v, str) and str(v).strip().startswith("Q")
        }
        prefetch_qids = sorted(
            ({
                str(getattr(it, "existing_qid", "") or "").strip()
                for it in pass1_native
                if str(getattr(it, "existing_qid", "") or "").strip().startswith("Q")
            } if not mode.is_test else set())
            | {
                str(created_qids.get(wikidata_studio.local_id_for_item(it)) or "").strip()
                for it in pass1_native
                if str(created_qids.get(wikidata_studio.local_id_for_item(it)) or "").strip().startswith("Q")
            }
            | (reconciler_qids if not mode.is_test else set())
        )

    existence_cache: dict[str, bool | None] = {}
    prefetched_entities: dict[str, dict[str, Any]] = {}
    if prefetch_qids:
        existence_cache, prefetched_entities = await run_in_threadpool(
            prefetch_entities_for_upload, prefetch_qids, is_test=mode.is_test,
        )
        logger.info(
            "Prefetched %d QIDs (test=%s): %d alive, %d missing, %d unknown",
            len(prefetch_qids),
            mode.is_test,
            sum(1 for v in existence_cache.values() if v is True),
            sum(1 for v in existence_cache.values() if v is False),
            sum(1 for v in existence_cache.values() if v is None),
        )
    if shared_uploader is not None:
        if prefetched_entities and hasattr(shared_uploader, "prime_entity_caches"):
            shared_uploader.prime_entity_caches(prefetched_entities)
        if not mode.dry_run and prefetch_qids and hasattr(shared_uploader, "warm_ownership_cache"):
            await run_in_threadpool(
                shared_uploader.warm_ownership_cache,
                prefetch_qids,
                prefetched_entities,
            )
        for _up in slot_uploaders[1:]:
            if _up is not None and hasattr(_up, "share_caches_from"):
                _up.share_caches_from(shared_uploader)
    audit_ctx = None if mode.dry_run else WikibaseAuditContext(
        actor_user_id=job.created_by,
        channel=CHANNEL_WIKIDATA_UPLOAD,
        project_id=project_id,
        run_id=run_id,
        job_id=job_id,
    )
    step1_started = time.monotonic()
    last_emit_at = 0.0

    async def _emit(payload: dict, *, force: bool = False) -> None:
        """Throttled progress writes.

        Per-item emissions carry a 200-outcome window and a NOTIFY —
        writing one per item made both stages crawl on the shared Postgres
        (run 47e03437). Step transitions and terminal states pass
        ``force``; per-item ticks drop at most 2 s of freshness.
        """
        nonlocal last_emit_at
        now = time.monotonic()
        if not force and now - last_emit_at < 2.0:
            return
        last_emit_at = now
        await update_job_progress(job_id, payload)

    inflight: list[Any] = []
    stopping = asyncio.Event()

    async def _settle_item_writes() -> None:
        stopping.set()
        while inflight:
            _, item, local_id, _, task = inflight[0]
            try:
                rows = await asyncio.shield(task)
            except Exception as exc:  # noqa: BLE001 — retain the other outcomes
                inflight.pop(0)
                logger.exception("wikidata upload failed while stopping %s", local_id)
                outcomes.append(wikidata_upload.UploadOutcome(
                    local_id=local_id, label=_item_display_label(item),
                    entity_type=str(getattr(item, "entity_type", "") or "other"),
                    qid=getattr(item, "existing_qid", None), status="failed",
                    message=f"Upload failed while stopping: {exc}", added_properties=[],
                ))
                continue
            inflight.pop(0)
            outcomes.extend(rows)
            if rows and not mode.is_test:
                last = rows[-1]
                wikidata_upload.remember_created_qid(
                    created_qids, local_id, last.qid, last.status,
                    dry_run=mode.dry_run, entity_type=last.entity_type,
                )

    async def _drain_one(entry: Any) -> bool:
        """Await one in-flight write and apply its outcome.

        Four writes overlap (per-slot uploaders own their sessions); results
        are applied in submission order so created_qids and the outcome
        list stay deterministic. Returns True when an auth failure aborted
        the run.
        """
        batch_idx, batch_item, batch_local_id, batch_current, batch_task = entry
        batch_outcomes = await asyncio.shield(batch_task)
        inflight.pop(0)
        outcomes.extend(batch_outcomes)
        if not batch_outcomes:
            elapsed = time.monotonic() - step1_started
            await _emit(build_upload_progress(
                phase="uploading",
                message="Step 1 of 2: writing items",
                upload_target=mode.target,
                step=STEP_WRITE_ITEMS,
                item_done=batch_idx + 1,
                item_total=item_total,
                link_done=0,
                link_total=link_total,
                current_label=batch_current,
                eta_seconds=estimate_remaining_seconds(batch_idx + 1, item_total, elapsed),
                elapsed_seconds=elapsed,
                processing_local_id=None,
                item_outcome=None,
                recent_item_outcomes=[
                    slim_upload_progress_outcome(o) for o in outcomes[-200:]
                ],
                outcome_counts=upload_outcome_counts(outcomes),
            ))
            return False
        last = batch_outcomes[-1]
        if not mode.is_test:
            wikidata_upload.remember_created_qid(
                created_qids, batch_local_id, last.qid, last.status,
                dry_run=mode.dry_run,
                entity_type=last.entity_type,
            )
        if owner_only and getattr(last, "audit_status", "") == "failed":
            await _settle_item_writes()
            await finish_job(
                job_id, status=JOB_STATUS_FAILED,
                error="Audit persistence failed after the item outcome; verify Wikidata before retry.",
                result={"dry_run": mode.dry_run, "upload_target": mode.target,
                        "outcomes": [o.__dict__ for o in outcomes], "link_outcomes": [],
                        "aborted_audit": True},
            )
            return True
        if (
            last.status in {"failed", "skipped"}
            and wikidata_upload._is_auth_failure_message(last.message)
        ):
            await _settle_item_writes()
            await finish_job(
                job_id,
                status=JOB_STATUS_FAILED,
                error=(
                    "Wikidata authentication failed; aborted remaining "
                    f"items to avoid login rate-limits. {last.message}"
                ),
                result={
                    "dry_run": mode.dry_run,
                    "upload_target": mode.target,
                    "moratorium_lifted": mode.moratorium_lifted,
                    "test_mode": mode.test_mode,
                    "outcomes": [o.__dict__ for o in outcomes],
                    "link_outcomes": [],
                    "aborted_auth": True,
                },
                progress=build_upload_progress(
                    phase="failed",
                    message="Aborted: Wikidata login failure",
                    upload_target=mode.target,
                    step=STEP_WRITE_ITEMS,
                    item_done=batch_idx + 1,
                    item_total=item_total,
                    link_done=0,
                    link_total=link_total,
                    current_label="Aborted",
                    eta_seconds=None,
                    elapsed_seconds=time.monotonic() - step1_started,
                    item_outcome=slim_upload_progress_outcome(last),
                    recent_item_outcomes=[
                        slim_upload_progress_outcome(o) for o in outcomes[-200:]
                    ],
                    outcome_counts=upload_outcome_counts(outcomes),
                ),
            )
            return True
        elapsed = time.monotonic() - step1_started
        await _emit(build_upload_progress(
            phase="uploading",
            message="Step 1 of 2: writing items",
            upload_target=mode.target,
            step=STEP_WRITE_ITEMS,
            item_done=batch_idx + 1,
            item_total=item_total,
            link_done=0,
            link_total=link_total,
            current_label=batch_current,
            eta_seconds=estimate_remaining_seconds(batch_idx + 1, item_total, elapsed),
            elapsed_seconds=elapsed,
            processing_local_id=None,
            item_outcome=slim_upload_progress_outcome(last),
            recent_item_outcomes=[
                slim_upload_progress_outcome(o) for o in outcomes[-200:]
            ],
            outcome_counts=upload_outcome_counts(outcomes),
        ))
        return False

    async def _finish_item_cancellation() -> None:
        await _settle_item_writes()
        await finish_job(
            job_id,
            status=JOB_STATUS_CANCELLED,
            result={
                "outcomes": [o.__dict__ for o in outcomes],
                "link_outcomes": [_link_outcome_dict(r) for r in link_outcomes],
                "processed": len(outcomes),
                "dry_run": mode.dry_run,
                "upload_target": mode.target,
                "moratorium_lifted": mode.moratorium_lifted,
                "test_mode": mode.test_mode,
            },
            progress=build_upload_progress(
                phase="cancelled", message="Cancelled by user", upload_target=mode.target,
                step=STEP_WRITE_ITEMS, item_done=len(outcomes), item_total=item_total,
                link_done=0, link_total=link_total, current_label="Cancelled",
                eta_seconds=None, elapsed_seconds=time.monotonic() - step1_started,
                recent_item_outcomes=[slim_upload_progress_outcome(o) for o in outcomes[-200:]],
                outcome_counts=upload_outcome_counts(outcomes),
            ),
        )

    try:
        for idx, item in enumerate(step1_items):
            if await is_cancel_requested(job_id):
                await _finish_item_cancellation()
                return
            local_id = wikidata_studio.local_id_for_item(item)
            label_txt = _item_display_label(item)
            entity_type = str(getattr(item, "entity_type", "") or "")
            current = f"{entity_type} · {label_txt or local_id}".strip(" ·")
            elapsed = time.monotonic() - step1_started
            eta = estimate_remaining_seconds(idx, item_total, elapsed)
            processing_row = {
                "local_id": local_id,
                "label": label_txt or None,
                "entity_type": entity_type or None,
                "status": "processing",
                "qid": None,
                "wikibase_id": None,
                "message": "Processing…",
            }
            await _emit(build_upload_progress(
                phase="uploading",
                message="Step 1 of 2: writing items",
                upload_target=mode.target,
                step=STEP_WRITE_ITEMS,
                item_done=idx,
                item_total=item_total,
                link_done=0,
                link_total=link_total,
                current_label=current,
                eta_seconds=eta,
                elapsed_seconds=elapsed,
                processing_local_id=local_id,
                item_outcome=processing_row,
                recent_item_outcomes=[
                    slim_upload_progress_outcome(o) for o in outcomes[-199:]
                ] + [processing_row],
                outcome_counts={**upload_outcome_counts(outcomes), "pending": 1},
            ))

            if owner_only:
                write_item, deferred = item, []
            else:
                item_for_write = (
                    replace(item, existing_qid=created_qids.get(local_id)) if mode.is_test else item
                )
                write_item, deferred = partition_unresolved_local(item_for_write, created_qids)
            if deferred:
                deferred_by_local_id[local_id] = deferred
                link_total = sum(len(v) for v in deferred_by_local_id.values())

            async def _do_write(payload: Any = write_item, original: Any = item) -> list[Any]:
                slot = await uploader_slots.get()
                try:
                    if stopping.is_set():
                        return []
                    if not mode.dry_run:
                        await write_gate.before_write()
                    if mode.is_test:
                        key = ledger_key_for_item(original, "wikidata-test")
                        item_ledger = (
                            {key: ledger[key]} if original.local_id in created_qids else {}
                        )
                    else:
                        item_ledger = ledger
                    res = await wikidata_upload.upload_items(
                        [payload], token=token or "", mode=mode,
                        audit_ctx=None, db=None, ledger=item_ledger,
                        run_id=run_id,
                        uploader=slot,
                        existence_cache=existence_cache,
                        created_qids=dict(created_qids) if mode.is_test else created_qids,
                        accept_by_local_id=accept_by_local_id,
                        quality_preflight=quality_preflight,
                        owner_targets=owner_targets,
                    )
                    if audit_ctx is not None and not mode.dry_run and res:
                        try:
                            if mode.is_test:
                                await _persist_test_outcomes(
                                    original, res, audit_ctx, native, ledger, created_qids,
                                    test_ledger_lock, slot_uploaders,
                                )
                            else:
                                async with session_scope() as db:
                                    await wikidata_upload.persist_upload_outcomes(
                                        db, [payload], res,
                                        audit_ctx=audit_ctx, is_test=False,
                                        record_ledger=not owner_only, strict_audit=owner_only,
                                    )
                        except Exception:  # noqa: BLE001 — preserve any completed write
                            if not owner_only:
                                raise
                            logger.exception("Owner-only audit persistence failed for %s", payload.local_id)
                            for row in res:
                                row.audit_status = "failed"
                                row.message += " Audit persistence failed; verify the remote outcome before retry."
                    return res
                finally:
                    uploader_slots.put_nowait(slot)

            inflight.append((idx, item, local_id, current, asyncio.create_task(_do_write())))
            if len(inflight) >= (1 if owner_only else 4):
                if await _drain_one(inflight[0]):
                    return
        while inflight:
            if await is_cancel_requested(job_id):
                await _finish_item_cancellation()
                return
            if await _drain_one(inflight[0]):
                return
        if step1_items and await is_cancel_requested(job_id):
            await _finish_item_cancellation()
            return
    except asyncio.CancelledError:
        await _settle_item_writes()
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("wikidata upload job failed for %s", run_id)
        await _settle_item_writes()
        await finish_job(
            job_id, status=JOB_STATUS_FAILED, error=str(exc),
            result={"outcomes": [o.__dict__ for o in outcomes], "link_outcomes": []},
        )
        return

    link_total = sum(len(v) for v in deferred_by_local_id.values())
    link_done = 0
    step2_started = time.monotonic()

    async def _finish_success() -> None:
        added = sum(1 for r in link_outcomes if r.status in {"linked", "would_link", "updated"})
        unresolved = sum(1 for r in link_outcomes if r.status == "unresolved")
        failed = sum(1 for r in link_outcomes if r.status in {"failed", "blocked"})
        await finish_job(
            job_id,
            status=JOB_STATUS_SUCCEEDED,
            result={
                "dry_run": mode.dry_run,
                "upload_target": mode.target,
                "moratorium_lifted": mode.moratorium_lifted,
                "test_mode": mode.test_mode,
                "outcomes": [o.__dict__ for o in outcomes],
                "link_outcomes": [_link_outcome_dict(r) for r in link_outcomes],
                "links_added": added,
                "links_unresolved": unresolved,
                "links_failed": failed,
            },
            progress=build_upload_progress(
                phase="done",
                message=f"{label} complete",
                upload_target=mode.target,
                step=STEP_ADD_LINKS + 1,
                item_done=item_total,
                item_total=item_total,
                link_done=link_total,
                link_total=link_total,
                current_label="Complete",
                eta_seconds=0,
                elapsed_seconds=time.monotonic() - step2_started,
                recent_item_outcomes=[
                    slim_upload_progress_outcome(o) for o in outcomes[-200:]
                ],
                outcome_counts=upload_outcome_counts(outcomes),
            ),
        )

    if not deferred_by_local_id:
        await _finish_success()
        return

    link_inflight: list[Any] = []

    def _record_stopping_links(entry: Any, last: Any | None, message: str) -> None:
        nonlocal link_done
        src_id, _, _, src_resolved, src_targets, _, src_qid, _ = entry
        status = getattr(last, "status", "failed")
        link_status = status if status in {"failed", "blocked", "skipped"} else "linked"
        qid = getattr(last, "qid", None) or src_qid
        for stmt, target in zip(src_resolved, src_targets, strict=True):
            link_outcomes.append(wikidata_upload.DeferredLinkOutcome(
                source_local_id=src_id, property_id=stmt.property_id,
                target_local_id=target, status=link_status, message=message, qid=qid,
            ))
            link_done += 1

    async def _settle_link_writes() -> None:
        stopping.set()
        while link_inflight:
            entry = link_inflight[0]
            try:
                rows = await asyncio.shield(entry[-1])
            except Exception as exc:  # noqa: BLE001 — retain the other outcomes
                link_inflight.pop(0)
                logger.exception("wikidata link upload failed while stopping %s", entry[0])
                _record_stopping_links(entry, None, f"Link upload failed while stopping: {exc}")
                continue
            link_inflight.pop(0)
            if rows:
                _record_stopping_links(entry, rows[-1], rows[-1].message)

    async def _drain_link_one(entry: Any) -> bool:
        """Await one in-flight connection write and apply its outcome.

        Four writes overlap via the per-slot uploaders; results apply in
        submission order so the link outcome list stays deterministic.
        Returns True when an auth failure aborted the run.
        """
        nonlocal link_done
        (src_id, src_label, src_item, src_resolved, src_targets,
         src_current, src_qid, task) = entry
        batch_outcomes = await asyncio.shield(task)
        link_inflight.pop(0)
        last = batch_outcomes[-1] if batch_outcomes else None
        status = last.status if last else "failed"
        message = last.message if last else "no outcome"
        qid = last.qid if last else src_qid
        if last and (
            last.status in {"failed", "skipped"}
            and wikidata_upload._is_auth_failure_message(last.message)
        ):
            _record_stopping_links(entry, last, message)
            await _settle_link_writes()
            await finish_job(
                job_id,
                status=JOB_STATUS_FAILED,
                error=(
                    "Wikidata authentication failed during connection pass; "
                    f"aborted. {last.message}"
                ),
                result={
                    "dry_run": mode.dry_run,
                    "upload_target": mode.target,
                    "moratorium_lifted": mode.moratorium_lifted,
                    "test_mode": mode.test_mode,
                    "outcomes": [o.__dict__ for o in outcomes],
                    "link_outcomes": [_link_outcome_dict(r) for r in link_outcomes],
                    "aborted_auth": True,
                },
                progress=build_upload_progress(
                    phase="failed",
                    message="Aborted: Wikidata login failure",
                    upload_target=mode.target,
                    step=STEP_ADD_LINKS,
                    item_done=item_total,
                    item_total=item_total,
                    link_done=link_done,
                    link_total=link_total,
                    current_label="Aborted",
                    eta_seconds=None,
                    elapsed_seconds=time.monotonic() - step2_started,
                ),
            )
            return True
        link_status = "linked"
        if status in {"blocked", "failed", "skipped"}:
            link_status = "failed" if status == "failed" else status
        pids = ", ".join(s.property_id for s in src_resolved)
        patch = {
            "local_id": src_id,
            "label": src_label or None,
            "entity_type": getattr(src_item, "entity_type", None),
            "status": "updated" if link_status == "linked" else status,
            "qid": qid,
            "wikibase_id": qid,
            "message": f"+{pids} {message}",
        }
        for stmt, target in zip(src_resolved, src_targets, strict=True):
            link_outcomes.append(wikidata_upload.DeferredLinkOutcome(
                source_local_id=src_id,
                property_id=stmt.property_id,
                target_local_id=target,
                status=link_status if link_status != "updated" else "linked",
                message=message,
                qid=qid,
            ))
            link_done += 1
        await _emit(build_upload_progress(
            phase="uploading",
            message="Step 2 of 2: adding connections",
            upload_target=mode.target,
            step=STEP_ADD_LINKS,
            item_done=item_total,
            item_total=item_total,
            link_done=link_done,
            link_total=link_total,
            current_label=src_current,
            eta_seconds=estimate_remaining_seconds(
                link_done, link_total, time.monotonic() - step2_started,
            ),
            elapsed_seconds=time.monotonic() - step2_started,
            processing_local_id=None,
            item_outcome=patch,
            recent_item_outcomes=[
                slim_upload_progress_outcome(o) for o in outcomes[-199:]
            ] + [patch],
            outcome_counts=upload_outcome_counts(outcomes),
        ))
        return False

    async def _finish_link_cancellation() -> None:
        await _settle_link_writes()
        await finish_job(
            job_id,
            status=JOB_STATUS_CANCELLED,
            result={
                "outcomes": [o.__dict__ for o in outcomes],
                "link_outcomes": [_link_outcome_dict(r) for r in link_outcomes],
                "processed": item_total,
                "dry_run": mode.dry_run,
                "upload_target": mode.target,
                "moratorium_lifted": mode.moratorium_lifted,
                "test_mode": mode.test_mode,
            },
            progress=build_upload_progress(
                phase="cancelled", message="Cancelled by user", upload_target=mode.target,
                step=STEP_ADD_LINKS, item_done=item_total, item_total=item_total,
                link_done=link_done, link_total=link_total, current_label="Cancelled",
                eta_seconds=None, elapsed_seconds=time.monotonic() - step2_started,
                recent_item_outcomes=[slim_upload_progress_outcome(o) for o in outcomes[-200:]],
                outcome_counts=upload_outcome_counts(outcomes),
            ),
        )

    def _link_in_retry_scope(source_id: str, stmts: list[Any]) -> bool:
        if scope is None:
            return True
        if source_id in scope:
            return True
        return any(first_local_target(s) in scope for s in stmts)

    outcome_by_id = {o.local_id: o for o in outcomes}
    skipped_person_names = {
        o.local_id: (o.label or "").strip()
        for o in outcomes
        if o.status == "skipped"
        and str(o.entity_type or "").lower() == "person"
        and (o.label or "").strip()
    }

    try:
        for source_id, stmts in deferred_by_local_id.items():
            if await is_cancel_requested(job_id):
                await _finish_link_cancellation()
                return
            if not _link_in_retry_scope(source_id, stmts):
                link_done += len(stmts)
                continue

            source_item = native_by_id.get(source_id)
            source_qid = created_qids.get(source_id)
            source_label = _item_display_label(source_item) if source_item is not None else source_id
            current = f"links · {source_label or source_id}"
            elapsed = time.monotonic() - step2_started
            await _emit(build_upload_progress(
                phase="uploading",
                message="Step 2 of 2: adding connections",
                upload_target=mode.target,
                step=STEP_ADD_LINKS,
                item_done=item_total,
                item_total=item_total,
                link_done=link_done,
                link_total=link_total,
                current_label=current,
                eta_seconds=estimate_remaining_seconds(link_done, link_total, elapsed)
                if link_done else None,
                elapsed_seconds=elapsed,
                processing_local_id=source_id,
                recent_item_outcomes=[
                    slim_upload_progress_outcome(o) for o in outcomes[-200:]
                ],
                outcome_counts=upload_outcome_counts(outcomes),
            ))

            if not source_qid or source_item is None:
                for stmt in stmts:
                    target = first_local_target(stmt)
                    link_outcomes.append(wikidata_upload.DeferredLinkOutcome(
                        source_local_id=source_id,
                        property_id=stmt.property_id,
                        target_local_id=target,
                        status="unresolved",
                        message=(
                            f"Source {source_id} has no QID from step 1; "
                            f"cannot add {stmt.property_id} → {target or '?'}"
                        ),
                    ))
                    link_done += 1
                continue

            if not wikidata_upload.pass2_may_update_source(
                outcome_by_id.get(source_id), source_qid,
                accept=accept_by_local_id.get(source_id), dry_run=mode.dry_run,
            ):
                for stmt in stmts:
                    target = first_local_target(stmt)
                    link_outcomes.append(wikidata_upload.DeferredLinkOutcome(
                        source_local_id=source_id,
                        property_id=stmt.property_id,
                        target_local_id=target,
                        status="skipped_foreign",
                        message=(
                            f"Pass 2 UPDATE refused for {source_id}: the target "
                            "does not match an authorized first-pass outcome."
                        ),
                        qid=source_qid if str(source_qid).startswith("Q") else None,
                    ))
                    link_done += 1
                continue

            if owner_only and (owner_targets.get(source_id) != source_qid or outcome_by_id[source_id].ownership != "own"):
                for stmt in stmts:
                    link_outcomes.append(wikidata_upload.DeferredLinkOutcome(
                        source_local_id=source_id, property_id=stmt.property_id,
                        target_local_id=first_local_target(stmt), status="blocked",
                        message="Owner-only target does not match the selected first-pass target.",
                    ))
                    link_done += 1
                continue

            resolved: list[Any] = []
            resolved_targets: list[str] = []
            for stmt in stmts:
                target = first_local_target(stmt)
                from converter.wikidata.quickstatements import reference_data  # noqa: PLC0415

                reference_data(stmt.references, stmt.reference_blocks)
                rewritten, leftover = resolve_statement_locals(
                    stmt, created_qids, test_namespace=mode.is_test,
                )
                if rewritten.reference_blocks:
                    rewritten.references = deepcopy([
                        snak for block in rewritten.reference_blocks for snak in block
                    ])
                if leftover:
                    fallback = (
                        _deferred_author_fallback(stmt, rewritten, skipped_person_names)
                        if target not in conflicting_shard_ids else None
                    )
                    if fallback is not None:
                        resolved.append(fallback)
                        resolved_targets.append(target)
                        continue
                    unresolved_target = leftover[0].removeprefix("__LOCAL:")
                    link_outcomes.append(wikidata_upload.DeferredLinkOutcome(
                        source_local_id=source_id,
                        property_id=stmt.property_id,
                        target_local_id=unresolved_target,
                        status="unresolved",
                        message=(
                            f"Target {unresolved_target} was not created; "
                            f"left {stmt.property_id} unresolved"
                        ),
                        qid=source_qid if str(source_qid).startswith("Q") else None,
                    ))
                    link_done += 1
                    continue
                resolved.append(rewritten)
                resolved_targets.append(target)

            if not resolved:
                continue

            if mode.dry_run:
                from converter.wikidata.uploader import owner_only_control_number  # noqa: PLC0415
                control_number = owner_only_control_number(source_item) if owner_only else None
                if owner_only and (not control_number or not await run_in_threadpool(
                    shared_uploader.owner_only_target_valid, source_qid, control_number,
                )):
                    for stmt, target in zip(resolved, resolved_targets, strict=True):
                        link_outcomes.append(wikidata_upload.DeferredLinkOutcome(
                            source_local_id=source_id, property_id=stmt.property_id,
                            target_local_id=target, status="blocked", qid=source_qid,
                            message="Owner-only target verification failed.",
                        ))
                        link_done += 1
                    continue
                pids = ", ".join(s.property_id for s in resolved)
                patch = {
                    "local_id": source_id,
                    "label": source_label or None,
                    "entity_type": getattr(source_item, "entity_type", None),
                    "status": "would_update",
                    "qid": source_qid if str(source_qid).startswith("Q") else None,
                    "wikibase_id": source_qid if str(source_qid).startswith("Q") else None,
                    "message": f"Would add connections {pids}",
                }
                for stmt, target in zip(resolved, resolved_targets, strict=True):
                    link_outcomes.append(wikidata_upload.DeferredLinkOutcome(
                        source_local_id=source_id,
                        property_id=stmt.property_id,
                        target_local_id=target,
                        status="would_link",
                        message=f"Would add {stmt.property_id} on {source_id}",
                        qid=patch["qid"],
                    ))
                    link_done += 1
                await _emit(build_upload_progress(
                    phase="uploading",
                    message="Step 2 of 2: adding connections",
                    upload_target=mode.target,
                    step=STEP_ADD_LINKS,
                    item_done=item_total,
                    item_total=item_total,
                    link_done=link_done,
                    link_total=link_total,
                    current_label=current,
                    eta_seconds=estimate_remaining_seconds(
                        link_done, link_total, time.monotonic() - step2_started,
                    ),
                    elapsed_seconds=time.monotonic() - step2_started,
                    processing_local_id=None,
                    item_outcome=patch,
                    recent_item_outcomes=[
                        slim_upload_progress_outcome(o) for o in outcomes[-199:]
                    ] + [patch],
                    outcome_counts=upload_outcome_counts(outcomes),
                ))
                continue

            clone = replace(
                source_item,
                existing_qid=source_qid,
                statements=resolved,
            )

            async def _do_link_write(payload: Any = clone) -> list[Any]:
                slot = await uploader_slots.get()
                try:
                    if stopping.is_set():
                        return []
                    if not mode.dry_run:
                        await write_gate.before_write()
                    res = await wikidata_upload.upload_items(
                        [payload], token=token or "", mode=mode,
                        audit_ctx=None, db=None, ledger=ledger,
                        run_id=run_id,
                        uploader=slot,
                        existence_cache=existence_cache,
                        created_qids=dict(created_qids) if mode.is_test else created_qids,
                        accept_by_local_id=accept_by_local_id,
                        quality_preflight=quality_preflight,
                        owner_targets=owner_targets,
                    )
                    if audit_ctx is not None and not mode.dry_run and res:
                        async with session_scope() as db:
                            await wikidata_upload.persist_upload_outcomes(
                                db, [payload], res,
                                audit_ctx=audit_ctx, is_test=mode.is_test,
                                record_ledger=not owner_only and not mode.is_test,
                            )
                    return res
                finally:
                    uploader_slots.put_nowait(slot)

            link_inflight.append((
                source_id, source_label, source_item, resolved, resolved_targets,
                current, source_qid, asyncio.create_task(_do_link_write()),
            ))
            if len(link_inflight) >= (1 if owner_only else 4):
                if await _drain_link_one(link_inflight[0]):
                    return
        while link_inflight:
            if await is_cancel_requested(job_id):
                await _finish_link_cancellation()
                return
            if await _drain_link_one(link_inflight[0]):
                return
        if await is_cancel_requested(job_id):
            await _finish_link_cancellation()
            return
    except asyncio.CancelledError:
        await _settle_link_writes()
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("wikidata upload link pass failed for %s", run_id)
        await _settle_link_writes()
        await finish_job(
            job_id, status=JOB_STATUS_FAILED, error=str(exc),
            result={
                "outcomes": [o.__dict__ for o in outcomes],
                "link_outcomes": [_link_outcome_dict(r) for r in link_outcomes],
            },
        )
        return

    await _finish_success()


async def load_wikidata_upload_item_ids(
    job_id: uuid.UUID,
) -> tuple[str, list[str], dict[str, Any]]:
    """Ordered step-1 item ids for shard planning.

    Returns ``(run_id, ordered local_ids, params)``. The load mirrors the
    serial job's scope rules (approved_only / item_approved_only / source /
    local_ids) so the shards cover exactly the serial step-1 scope.
    """
    from app.routers.wikidata_studio import (  # noqa: PLC0415
        STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS,
        _build_native_items,
    )
    from converter.wikidata.uploader import sort_items_for_upload  # noqa: PLC0415

    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            raise ValueError(f"job {job_id} not found")
        params = dict(job.params or {})
        mode = wikidata_upload.resolve_upload_mode(
            params.get("upload_target"), dry_run=params.get("dry_run"),
        )
        approved_only = bool(params.get("approved_only", True))
        source = str(params.get("source") or "canonical")
        item_approved_only = bool(params.get("item_approved_only", False))
        update_existing = bool(params.get("update_existing", True))
        ai_verified_only = bool(params.get("ai_verified_only", False))
        scoped_ids = params.get("local_ids")
        scope: set[str] | None = None
        if isinstance(scoped_ids, list) and scoped_ids:
            scope = {str(x) for x in scoped_ids if str(x).strip()}
        auth = SimpleNamespace(user=SimpleNamespace(id=job.created_by))
        native = await _build_native_items(
            db, job.run_id, auth, approved_only=approved_only, source=source,
            freshness_timeout_seconds=STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS,
        )
        override_rows = (
            await db.execute(
                select(WikidataItemOverride).where(
                    WikidataItemOverride.run_id == job.run_id,
                )
            )
        ).scalars().all()
        if item_approved_only:
            approved_ids = {r.local_id for r in override_rows if r.approved}
            native = [
                it for it in native
                if wikidata_studio.local_id_for_item(it) in approved_ids
            ]
        verified_ids: set[str] | None = None
        if ai_verified_only:
            verified_ids = {
                r.local_id
                for r in override_rows
                if isinstance(r.ai_verdict, dict)
                and str(r.ai_verdict.get("overall") or "").lower() in {"full", "pass"}
            }
        if mode.is_test:
            from app.pipeline.wikidata_qid_ledger import load_global_ledger  # noqa: PLC0415
            ledger = await load_global_ledger(db, is_test=True, reject_ambiguous=True)
        else:
            ledger = await wikidata_upload.load_ledger_for_prepare(db, is_test=False)
    from app.db import reset_engine  # noqa: PLC0415

    await reset_engine()
    ordered = sort_items_for_upload(list(native))
    if mode.is_test and _test_ledger_conflicts(ordered, ledger):
        raise ValueError("Ambiguous test-wiki ledger or local identity; refusing upload")
    known_qids = _resolve_existing_qid_map(ordered, ledger, is_test=mode.is_test)
    ids = [wikidata_studio.local_id_for_item(it) for it in ordered]
    if scope is not None:
        ids = [i for i in ids if i in scope]
    else:
        if verified_ids is not None:
            ids = [i for i in ids if i in verified_ids]
        if not update_existing:
            ids = [i for i in ids if i not in known_qids]
    return str(job.run_id), ids, params


async def run_wikidata_upload_shard(
    job_id: uuid.UUID,
    run_id: uuid.UUID,
    local_ids: list[str],
    on_progress: Any | None = None,
) -> dict[str, Any]:
    """One shard's step-1 writes. Returns serialisable state for the merge.

    Each shard rebuilds its slice's items (same pattern as the rule-verify
    shards), pre-warms the reconciler for the slice, and runs the per-item
    write path sequentially inside the shard — parallelism comes from the
    shard fan-out, not from in-shard concurrency.
    """
    from app.routers.wikidata_studio import (  # noqa: PLC0415
        STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS,
        _build_native_items,
        _get_studio_cache_row,
    )
    from converter.wikidata.reconciler import (  # noqa: PLC0415
        prewarm_reconciler_for_items,
    )
    from converter.wikidata.uploader import (  # noqa: PLC0415
        partition_unresolved_local,
        sort_items_for_upload,
    )
    from app.db import reset_engine  # noqa: PLC0415
    from app.pipeline.wikidata_existence import (  # noqa: PLC0415
        prefetch_entities_for_upload,
    )

    for _db_attempt in range(5):
        try:
            async with session_scope() as db:
                job = await db.get(RunJob, job_id)
                if job is None:
                    raise ValueError(f"job {job_id} not found")
                params = dict(job.params or {})
                if params.get("owner_only") is not False and "owner_only" in params:
                    raise ValueError("owner_only upload cannot run in a shard")
                mode = wikidata_upload.resolve_upload_mode(
                    params.get("upload_target"), dry_run=params.get("dry_run"),
                )
                approved_only = bool(params.get("approved_only", True))
                source = str(params.get("source") or "canonical")
                item_approved_only = bool(params.get("item_approved_only", False))
                token = str(params.get("_wikidata_token") or "")
                auth = SimpleNamespace(user=SimpleNamespace(id=job.created_by))
                studio_cache = await _get_studio_cache_row(db, run_id, approved_only, source)
                native = await _build_native_items(
                    db, run_id, auth, approved_only=approved_only, source=source,
                    freshness_timeout_seconds=STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS,
                )
                if studio_cache is None:
                    raise StudioSourceChangedError(run_id)
                expected_fingerprint = studio_cache.input_fingerprint
                expected_summary = dict(studio_cache.summary or {})

                async def _assert_shard_source_current() -> None:
                    await _assert_studio_source_unchanged(
                        run_id, source=source, approved_only=approved_only,
                        fingerprint=expected_fingerprint, summary=expected_summary,
                    )

                shard_write_gate = _BatchFreshnessGate(_assert_shard_source_current)
                override_rows = (
                    await db.execute(
                        select(WikidataItemOverride).where(
                            WikidataItemOverride.run_id == run_id,
                        )
                    )
                ).scalars().all()
                if item_approved_only:
                    approved_ids = {r.local_id for r in override_rows if r.approved}
                    native = [
                        it for it in native
                        if wikidata_studio.local_id_for_item(it) in approved_ids
                    ]
                accept_by_local_id = {
                    r.local_id: wikidata_upload.ForeignAccept(
                        accept_foreign_modify=bool(r.accept_foreign_modify),
                        accepted_foreign_qid=str(r.accepted_foreign_qid),
                    )
                    for r in override_rows
                    if r.accept_foreign_modify and r.accepted_foreign_qid
                }
                if mode.is_test:
                    from app.pipeline.wikidata_qid_ledger import load_global_ledger  # noqa: PLC0415
                    ledger = await load_global_ledger(db, is_test=True, reject_ambiguous=True)
                else:
                    ledger = await wikidata_upload.load_ledger_for_prepare(db, is_test=False)
            break
        except Exception as exc:  # noqa: BLE001
            if _db_attempt >= 4 or "too many connections" not in str(exc).lower():
                raise
            await asyncio.sleep(2.0 * (_db_attempt + 1))
    await reset_engine()

    scope = {str(x) for x in local_ids if str(x)}
    items = [
        it for it in sort_items_for_upload(list(native))
        if wikidata_studio.local_id_for_item(it) in scope
    ]
    created_qids: dict[str, str] = _resolve_existing_qid_map(
        native, ledger, is_test=mode.is_test,
    )
    if mode.is_test and _test_ledger_conflicts(native, ledger):
        raise ValueError("Ambiguous test-wiki ledger or local identity; refusing upload")
    reconciler = wikidata_upload._make_reconciler()
    await run_in_threadpool(prewarm_reconciler_for_items, reconciler, items)
    reconciler_qids = {
        str(v).strip()
        for v in (getattr(reconciler, "_cache", {}) or {}).values()
        if isinstance(v, str) and str(v).strip().startswith("Q")
    }
    prefetch_qids = sorted(
        ({
            str(getattr(it, "existing_qid", "") or "").strip()
            for it in items
            if str(getattr(it, "existing_qid", "") or "").strip().startswith("Q")
        } if not mode.is_test else set())
        | {
            str(created_qids.get(wikidata_studio.local_id_for_item(it)) or "").strip()
            for it in items
            if str(created_qids.get(wikidata_studio.local_id_for_item(it)) or "").strip().startswith("Q")
        }
        | (reconciler_qids if not mode.is_test else set())
    )
    existence_cache: dict[str, bool | None] = {}
    prefetched_entities: dict[str, dict[str, Any]] = {}
    if prefetch_qids:
        existence_cache, prefetched_entities = await run_in_threadpool(
            prefetch_entities_for_upload, prefetch_qids, is_test=mode.is_test,
        )
    quality_preflight = await run_in_threadpool(
        wikidata_upload.build_upload_corpus_quality_preflight,
        native,
    )
    audit_ctx = None if mode.dry_run else WikibaseAuditContext(
        actor_user_id=job.created_by,
        channel=CHANNEL_WIKIDATA_UPLOAD,
        project_id=job.project_id,
        run_id=run_id,
        job_id=job_id,
    )
    deferred: dict[str, list[Any]] = {}
    outcomes: list[dict[str, Any]] = []

    shared_uploader: Any | None = None
    slot_uploaders: list[Any] = []
    if token:
        from converter.wikidata.uploader import WikidataUploader  # noqa: PLC0415

        shared_uploader = WikidataUploader(
            token=token,
            is_test=mode.is_test,
            batch_mode=True,
            allow_live=mode.allow_live,
        )
        if not mode.dry_run:
            await run_in_threadpool(shared_uploader.ensure_authenticated)
        if prefetched_entities and hasattr(shared_uploader, "prime_entity_caches"):
            shared_uploader.prime_entity_caches(prefetched_entities)
        if not mode.dry_run and prefetch_qids and hasattr(shared_uploader, "warm_ownership_cache"):
            await run_in_threadpool(
                shared_uploader.warm_ownership_cache,
                prefetch_qids,
                prefetched_entities,
            )
        slot_uploaders.append(shared_uploader)
        if not mode.dry_run:
            for _ in range(2):
                try:
                    extra = WikidataUploader(
                        token=token,
                        is_test=mode.is_test,
                        batch_mode=True,
                        allow_live=mode.allow_live,
                    )
                    await run_in_threadpool(extra.ensure_authenticated)
                    if hasattr(extra, "share_caches_from"):
                        extra.share_caches_from(shared_uploader)
                    slot_uploaders.append(extra)
                except Exception:  # noqa: BLE001 — degrade to fewer slots
                    break
    if not slot_uploaders:
        slot_uploaders.append(None)
    uploader_slots: asyncio.Queue = asyncio.Queue()
    for _up in slot_uploaders:
        if mode.is_test and _up is not None:
            _register_test_targets(_up, native, ledger)
        uploader_slots.put_nowait(_up)

    # Three items overlap per shard: the uncertain-duplicate confirm is a
    # per-item LLM call (~11 s) and sequential processing made a 80-item
    # shard take 25+ min. Cross-item local links simply defer to step-2.
    lanes = asyncio.Semaphore(3)
    outcome_objs: list[Any] = []
    test_ledger_lock = asyncio.Lock()

    async def _one(item: Any) -> list[dict[str, Any]]:
        async with lanes:
            slot = await uploader_slots.get()
            try:
                local_id = wikidata_studio.local_id_for_item(item)
                item_for_write = (
                    replace(item, existing_qid=created_qids.get(local_id)) if mode.is_test else item
                )
                write_item, deferred_locals = partition_unresolved_local(
                    item_for_write, created_qids,
                )
                if deferred_locals:
                    deferred[local_id] = deferred_locals
                if mode.is_test:
                    key = ledger_key_for_item(item, "wikidata-test")
                    item_ledger = {key: ledger[key]} if local_id in created_qids else {}
                else:
                    item_ledger = ledger
                if not mode.dry_run:
                    await shard_write_gate.before_write()
                results = await wikidata_upload.upload_items(
                    [write_item], token=token or "", mode=mode,
                    audit_ctx=None, db=None, ledger=item_ledger,
                    run_id=run_id,
                    uploader=slot,
                    accept_by_local_id=accept_by_local_id,
                    existence_cache=existence_cache,
                    created_qids=dict(created_qids) if mode.is_test else created_qids,
                    quality_preflight=quality_preflight,
                )
                outcome_objs.extend(results)
                if mode.is_test and audit_ctx is not None and results:
                    await _persist_test_outcomes(
                        item, results, audit_ctx, native, ledger, created_qids, test_ledger_lock,
                        slot_uploaders,
                    )
                elif not mode.is_test:
                    for row in results:
                        wikidata_upload.remember_created_qid(
                            created_qids,
                            local_id,
                            row.qid,
                            row.status,
                            dry_run=mode.dry_run,
                            entity_type=row.entity_type,
                        )
                return [dict(row.__dict__) for row in results]
            finally:
                uploader_slots.put_nowait(slot)

    for batch in (items[i:i + 3] for i in range(0, len(items), 3)):
        for rows in await asyncio.gather(*(_one(it) for it in batch)):
            outcomes.extend(rows)
            if on_progress is not None:
                try:
                    on_progress(len(outcomes))
                except Exception:  # noqa: BLE001 — telemetry only
                    pass

    if audit_ctx is not None and not mode.dry_run and outcome_objs and not mode.is_test:
        for _db_attempt in range(5):
            try:
                async with session_scope() as db:
                    await wikidata_upload.persist_upload_outcomes(
                        db, items, outcome_objs,
                        audit_ctx=audit_ctx, is_test=mode.is_test,
                    )
                break
            except Exception as exc:  # noqa: BLE001
                if _db_attempt >= 4 or "too many connections" not in str(exc).lower():
                    raise
                await asyncio.sleep(2.0 * (_db_attempt + 1))
        await reset_engine()
    return {
        "outcomes": outcomes,
        "created_qids": dict(created_qids),
        "deferred": {
            lid: [dict(s.__dict__) for s in rows]
            for lid, rows in deferred.items()
        },
    }
