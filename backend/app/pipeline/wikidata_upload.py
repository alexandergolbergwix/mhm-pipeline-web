"""Drive WikidataReconciler + WikidataUploader from the web.

All four guards from the desktop pipeline are kept INTACT:

1. ``_is_our_item()`` — refuses to modify items whose first revision
   wasn't authored by the authenticated user (Rule 38 gate 1).
2. ``_assert_modifiable()`` inside ``_build_wbi_item`` (gate 2).
3. ``_would_create_identity_conflict()`` per statement (gate 3).
4. Pre-write ``_assert_modifiable()`` immediately before
   ``wbi_item.write()`` (gate 4).

On top of those (which guard against modifying *community* items), the
upload path here adds the guards that prevent the OTHER 2026-04 failure
mode — mass *duplicate creation* (the subject of the bulk-deletion
request):

A. **Reconcile-before-create, fail closed.** Every item with no
   builder-supplied QID is reconciled against live Wikidata *inside the
   upload call itself* (manuscripts by P3959, persons by the
   conflict-checked identifier path). If the lookup cannot be completed
   (WDQS outage / 429 / timeout), the item is BLOCKED, never created — a
   transient outage must never be read as "no existing item → create".
B. **Validator hard gate.** Every item is run through
   ``item_validator.validate_item`` before any write; any ERROR-severity
   issue (no identifier, placeholder label, P50-on-manuscript, …) blocks
   the write. The moat now sits IN the upload path, not just as an
   advisory build-time badge.

The moratorium (Rule 25) is also respected: live writes to
``wikidata.org`` refuse to run unless ``MORATORIUM_LIFTED=true`` is set
in the environment. Set ``WIKIDATA_TEST_MODE=true`` to point at
test.wikidata.org instead — that bypasses the moratorium.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wikibase_cloud_write import (
    OPERATION_ADOPT,
    OPERATION_BLOCKED,
    OPERATION_CREATE,
    OPERATION_FAILED,
    OPERATION_SKIP,
    OPERATION_UPDATE,
    TARGET_ITEM,
)
from app.pipeline.wikidata_qid_ledger import (
    ledger_key_for_item,
    ledger_namespace,
    lookup_ledger_qid,
    record_ledger_mapping,
)
from app.services.wikibase_audit import record_wikibase_write

if TYPE_CHECKING:
    from app.services.wikibase_audit import WikibaseAuditContext

logger = logging.getLogger(__name__)


@dataclass
class UploadOutcome:
    local_id: str
    label: str
    entity_type: str
    qid: str | None
    # success | updated | exists | skipped | blocked | failed | adopted | would_adopt
    status: str
    message: str
    added_properties: list[str]


@dataclass
class ReconcileOutcome:
    local_id: str
    label: str
    entity_type: str
    existing_qid: str | None
    method: str             # "p3959/shelfmark" | "identifier" | "none" | "error" | "ledger"
    message: str


@dataclass
class PreparedItem:
    """An item after reconciliation + validation, ready for an upload decision."""

    item: Any
    local_id: str
    label: str
    entity_type: str
    existing_qid: str | None
    method: str
    blocked: bool
    block_status: str
    block_message: str
    had_builder_qid: bool = False
    adopt_candidate: bool = False


# ── Shared reconcile + validation core ──────────────────────────────────


def _make_reconciler() -> Any:
    from converter.wikidata.reconciler import WikidataReconciler  # noqa: PLC0415

    return WikidataReconciler()


def _reconcile_for_upload(
    reconciler: Any, item: Any, entity_type: str,
) -> tuple[str | None, str]:
    if entity_type == "person":
        viaf = _find_statement_value(item, "P214")
        nli = _find_statement_value(item, "P8189")
        lc = _find_statement_value(item, "P244")
        gnd = _find_statement_value(item, "P227")
        isni = _find_statement_value(item, "P213")
        qid = reconciler.reconcile_person_by_identifiers(
            str(viaf) if viaf else None,
            str(nli) if nli else None,
            lc_id=str(lc) if lc else None,
            gnd_id=str(gnd) if gnd else None,
            isni=str(isni) if isni else None,
        )
        return (qid, "identifier" if qid else "none")

    if entity_type == "manuscript":
        nnl = _find_statement_value(item, "P3959")
        shelf = _find_statement_value(item, "P217")
        qid = reconciler.reconcile_manuscript_by_identifiers(
            str(nnl) if nnl else None,
            str(shelf) if shelf else None,
        )
        return (qid, "p3959/shelfmark" if qid else "none")

    if entity_type == "work":
        labels = getattr(item, "labels", {}) or {}
        title = labels.get("he") or labels.get("en") or ""
        author_qid = _find_statement_value(item, "P50")
        if title:
            has_hebrew = any("\u0590" <= c <= "\u05ff" for c in str(title))
            qid = reconciler.reconcile_work_by_label_and_author(
                str(title),
                lang="he" if has_hebrew else "en",
                author_qid=str(author_qid) if author_qid else None,
            )
            return (qid, "label+author" if qid else "none")
        return (None, "none")

    return (None, "none")


def _prepare_for_upload(
    items: list[Any],
    reconciler: Any,
    *,
    ledger: dict[str, str] | None = None,
    ledger_ns: str | None = None,
) -> list[PreparedItem]:
    from converter.wikidata.item_validator import validate_item  # noqa: PLC0415
    from converter.wikidata.reconciler import (  # noqa: PLC0415
        ReconciliationUnavailableError,
    )

    ns = ledger_ns or ledger_namespace()
    prepared: list[PreparedItem] = []
    for i, item in enumerate(items):
        local_id = _local_id(item, i)
        label = _label(item)
        et = getattr(item, "entity_type", "") or "other"
        had_builder_qid = bool(getattr(item, "existing_qid", None))
        existing = getattr(item, "existing_qid", None)
        method = "prebuilt" if existing else "none"
        adopt_candidate = False

        if not existing and ledger:
            key = ledger_key_for_item(item, ns)
            ledger_qid = lookup_ledger_qid(ledger, key)
            if ledger_qid:
                existing = ledger_qid
                method = "ledger"
                adopt_candidate = True
                item.existing_qid = ledger_qid

        if not existing:
            try:
                existing, method = _reconcile_for_upload(reconciler, item, et)
            except ReconciliationUnavailableError as exc:
                prepared.append(PreparedItem(
                    item=item, local_id=local_id, label=label, entity_type=et,
                    existing_qid=None, method="error", blocked=True,
                    block_status="blocked",
                    block_message=(
                        "Reconciliation lookup could not be completed "
                        f"({exc}) — refusing to CREATE. A transient Wikidata "
                        "Query Service outage must never be read as 'no "
                        "existing item'. Retry when WDQS is reachable."
                    ),
                    had_builder_qid=had_builder_qid,
                ))
                continue
            if existing:
                adopt_candidate = not had_builder_qid
                item.existing_qid = existing

        errors = [
            iss for iss in validate_item(item)
            if getattr(iss, "severity", "") == "error"
        ]
        if errors:
            codes = ", ".join(sorted({getattr(e, "code", "") for e in errors}))
            prepared.append(PreparedItem(
                item=item, local_id=local_id, label=label, entity_type=et,
                existing_qid=existing, method=method, blocked=True,
                block_status="blocked",
                block_message=(
                    f"Blocked by validator (ERROR: {codes}). Fix via item "
                    "overrides and rebuild before uploading — error-severity "
                    "items are never created or updated."
                ),
                had_builder_qid=had_builder_qid,
                adopt_candidate=adopt_candidate,
            ))
            continue

        prepared.append(PreparedItem(
            item=item, local_id=local_id, label=label, entity_type=et,
            existing_qid=existing, method=method, blocked=False,
            block_status="", block_message="",
            had_builder_qid=had_builder_qid,
            adopt_candidate=adopt_candidate,
        ))
    return prepared


async def reconcile_items(items: list[Any]) -> list[ReconcileOutcome]:
    return await run_in_threadpool(_reconcile_sync, items)


def _reconcile_sync(items: list[Any]) -> list[ReconcileOutcome]:
    from converter.wikidata.reconciler import (  # noqa: PLC0415
        ReconciliationUnavailableError,
    )

    reconciler = _make_reconciler()
    out: list[ReconcileOutcome] = []
    for i, item in enumerate(items):
        local_id = _local_id(item, i)
        label = _label(item)
        et = getattr(item, "entity_type", "") or "other"

        try:
            qid, method = _reconcile_for_upload(reconciler, item, et)
            if qid:
                item.existing_qid = qid
            out.append(ReconcileOutcome(
                local_id=local_id, label=label, entity_type=et,
                existing_qid=qid, method=method,
                message=(
                    f"Found {qid} via {method}" if qid
                    else "No existing Wikidata item found"
                ),
            ))
        except ReconciliationUnavailableError as exc:
            out.append(ReconcileOutcome(
                local_id=local_id, label=label, entity_type=et,
                existing_qid=None, method="error",
                message=(
                    f"Lookup unavailable ({exc}) — this item would be BLOCKED "
                    "from creation until WDQS is reachable (fail-closed)."
                ),
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning("reconcile failed for %r: %s", label, exc)
            out.append(ReconcileOutcome(
                local_id=local_id, label=label, entity_type=et,
                existing_qid=None, method="error",
                message=f"Lookup failed: {exc}",
            ))

    return out


async def reconcile_single_item(
    db: AsyncSession,
    item: Any,
    *,
    record_ledger: bool = True,
) -> ReconcileOutcome:
    """Reconcile one item; optionally persist a ledger mapping on match."""
    ledger = await load_ledger_for_prepare(db)
    outcomes = await run_in_threadpool(
        _reconcile_sync_with_ledger, [item], ledger,
    )
    outcome = outcomes[0]
    if record_ledger and outcome.existing_qid:
        key = ledger_key_for_item(item)
        await record_ledger_mapping(
            db, key, outcome.existing_qid,
            local_key=_local_id(item, 0),
            label=_label(item),
        )
    return outcome


def _reconcile_sync_with_ledger(
    items: list[Any], ledger: dict[str, str],
) -> list[ReconcileOutcome]:
    from converter.wikidata.reconciler import ReconciliationUnavailableError  # noqa: PLC0415

    ns = ledger_namespace()
    reconciler = _make_reconciler()
    out: list[ReconcileOutcome] = []
    for i, item in enumerate(items):
        local_id = _local_id(item, i)
        label = _label(item)
        et = getattr(item, "entity_type", "") or "other"
        key = ledger_key_for_item(item, ns)
        ledger_qid = lookup_ledger_qid(ledger, key)
        if ledger_qid:
            item.existing_qid = ledger_qid
            out.append(ReconcileOutcome(
                local_id=local_id, label=label, entity_type=et,
                existing_qid=ledger_qid, method="ledger",
                message=f"Found {ledger_qid} via ledger",
            ))
            continue
        try:
            qid, method = _reconcile_for_upload(reconciler, item, et)
            if qid:
                item.existing_qid = qid
            out.append(ReconcileOutcome(
                local_id=local_id, label=label, entity_type=et,
                existing_qid=qid, method=method,
                message=(
                    f"Found {qid} via {method}" if qid
                    else "No existing Wikidata item found"
                ),
            ))
        except ReconciliationUnavailableError:
            raise
        except ReconciliationUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            out.append(ReconcileOutcome(
                local_id=local_id, label=label, entity_type=et,
                existing_qid=None, method="error",
                message=f"Lookup failed: {exc}",
            ))
    return out


async def load_ledger_for_prepare(db: AsyncSession) -> dict[str, str]:
    from app.pipeline.wikidata_qid_ledger import load_global_ledger  # noqa: PLC0415

    return await load_global_ledger(db)


async def upload_items(
    items: list[Any], *,
    token: str,
    dry_run: bool,
    audit_ctx: WikibaseAuditContext | None = None,
    db: AsyncSession | None = None,
    ledger: dict[str, str] | None = None,
) -> list[UploadOutcome]:
    if ledger is None and db is not None:
        ledger = await load_ledger_for_prepare(db)
    ns = ledger_namespace()
    outcomes = await run_in_threadpool(
        _upload_sync, items, token, dry_run, ledger or {}, ns,
    )
    if db is not None and audit_ctx is not None and not dry_run:
        for outcome in outcomes:
            await _record_outcome_audit(db, audit_ctx, outcome)
            if outcome.qid and outcome.status in ("created", "adopted"):
                item = next(
                    (it for it in items if _local_id(it, 0) == outcome.local_id),
                    None,
                )
                if item is not None:
                    await record_ledger_mapping(
                        db,
                        ledger_key_for_item(item, ns),
                        outcome.qid,
                        local_key=outcome.local_id,
                        label=outcome.label,
                    )
    return outcomes


async def push_single_item(
    db: AsyncSession,
    item: Any,
    *,
    token: str,
    audit_ctx: WikibaseAuditContext | None = None,
) -> UploadOutcome:
    """Live create-or-update for exactly one native WikidataItem."""
    ledger = await load_ledger_for_prepare(db)
    outcomes = await upload_items(
        [item], token=token, dry_run=False,
        audit_ctx=audit_ctx, db=db, ledger=ledger,
    )
    return outcomes[0]


def _upload_sync(
    items: list[Any],
    token: str,
    dry_run: bool,
    ledger: dict[str, str],
    ledger_ns: str,
) -> list[UploadOutcome]:
    from converter.wikidata.uploader import (  # noqa: PLC0415
        UnauthorisedModificationError,
        WikidataUploader,
    )

    is_test = os.environ.get("WIKIDATA_TEST_MODE", "").lower() == "true"
    moratorium_lifted = (
        os.environ.get("MORATORIUM_LIFTED", "").lower() == "true"
    )

    if dry_run:
        return _dry_run(items, ledger, ledger_ns)

    if not is_test and not moratorium_lifted:
        return [
            UploadOutcome(
                local_id=_local_id(it, i),
                label=_label(it),
                entity_type=getattr(it, "entity_type", "") or "other",
                qid=getattr(it, "existing_qid", None),
                status="skipped",
                message=(
                    "Live upload refused — set MORATORIUM_LIFTED=true in "
                    "the environment to enable production writes, or "
                    "WIKIDATA_TEST_MODE=true to point at test.wikidata.org."
                ),
                added_properties=[],
            )
            for i, it in enumerate(items)
        ]

    reconciler = _make_reconciler()
    prepared = _prepare_for_upload(items, reconciler, ledger=ledger, ledger_ns=ledger_ns)

    uploader = WikidataUploader(token=token, is_test=is_test, batch_mode=True)

    out: list[UploadOutcome] = []
    for p in prepared:
        if p.blocked:
            out.append(UploadOutcome(
                local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                qid=p.existing_qid, status=p.block_status,
                message=p.block_message, added_properties=[],
            ))
            continue
        try:
            result = uploader.upload_item(p.item)
            status = result.status
            if p.adopt_candidate and status == "updated":
                status = "adopted"
            elif status == "success":
                status = "created"
            out.append(
                UploadOutcome(
                    local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                    qid=result.qid, status=status, message=result.message,
                    added_properties=list(result.added_properties or []),
                ),
            )
        except UnauthorisedModificationError as exc:
            out.append(
                UploadOutcome(
                    local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                    qid=exc.qid, status="skipped",
                    message=f"Rule-38 guard fired ({exc.stage}): refusing to "
                            f"modify {exc.qid} (not authored by us)",
                    added_properties=[],
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("upload failed for %r", p.label)
            out.append(
                UploadOutcome(
                    local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                    qid=p.existing_qid,
                    status="failed", message=str(exc), added_properties=[],
                ),
            )
    return out


def _dry_run(
    items: list[Any],
    ledger: dict[str, str],
    ledger_ns: str,
) -> list[UploadOutcome]:
    reconciler = _make_reconciler()
    prepared = _prepare_for_upload(items, reconciler, ledger=ledger, ledger_ns=ledger_ns)

    out: list[UploadOutcome] = []
    for p in prepared:
        stmts = getattr(p.item, "statements", []) or []
        if p.blocked:
            out.append(UploadOutcome(
                local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                qid=p.existing_qid, status="blocked",
                message=f"Dry-run: would be BLOCKED — {p.block_message}",
                added_properties=[],
            ))
            continue
        if p.adopt_candidate and p.existing_qid:
            status = "would_adopt"
            message = (
                f"Dry-run: would ADOPT {p.existing_qid} "
                f"(matched via {p.method}; Rule-38 guards run live)"
            )
        elif p.existing_qid:
            status = "exists"
            message = (
                f"Dry-run: would UPDATE {p.existing_qid} "
                f"(matched via {p.method}; Rule-38 guards run live)"
            )
        else:
            status = "success"
            message = (
                f"Dry-run: would CREATE with {len(stmts)} statement(s) "
                "(reconciliation found no existing item)"
            )
        out.append(
            UploadOutcome(
                local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                qid=p.existing_qid,
                status=status,
                message=message,
                added_properties=[
                    getattr(s, "property", None) or getattr(s, "property_id", None) or ""
                    for s in stmts
                ],
            ),
        )
    return out


async def _record_outcome_audit(
    db: AsyncSession,
    ctx: WikibaseAuditContext,
    outcome: UploadOutcome,
) -> None:
    operation = _audit_operation_for_status(outcome.status)
    if operation is None:
        return
    await record_wikibase_write(
        db, ctx,
        operation=operation,
        target_kind=TARGET_ITEM,
        target_key=outcome.local_id,
        wikibase_id=outcome.qid,
        outcome_message=outcome.message or "ok",
    )


def _audit_operation_for_status(status: str) -> str | None:
    return {
        "blocked": OPERATION_BLOCKED,
        "skipped": OPERATION_SKIP,
        "failed": OPERATION_FAILED,
        "created": OPERATION_CREATE,
        "success": OPERATION_CREATE,
        "updated": OPERATION_UPDATE,
        "adopted": OPERATION_ADOPT,
        "exists": OPERATION_UPDATE,
    }.get(status)


def prepare_items_for_export(
    items: list[Any],
    *,
    ledger: dict[str, str] | None = None,
) -> tuple[list[PreparedItem], list[PreparedItem]]:
    """Run reconcile + validator for QS gating. Returns (eligible, blocked)."""
    reconciler = _make_reconciler()
    prepared = _prepare_for_upload(
        items, reconciler,
        ledger=ledger or {},
        ledger_ns=ledger_namespace(),
    )
    eligible = [p for p in prepared if not p.blocked]
    blocked = [p for p in prepared if p.blocked]
    return eligible, blocked


# ── small helpers ───────────────────────────────────────────────────────


def _local_id(item: Any, idx: int) -> str:
    for attr in ("local_id", "id", "key"):
        v = getattr(item, attr, None)
        if v:
            return str(v)
    return f"item-{idx:04d}"


def _label(item: Any) -> str:
    labels = getattr(item, "labels", None) or {}
    if isinstance(labels, dict):
        return labels.get("en") or labels.get("he") or next(iter(labels.values()), "") or "(no label)"
    return "(no label)"


def _find_statement_value(item: Any, prop: str) -> Any | None:
    for s in getattr(item, "statements", []) or []:
        if getattr(s, "property", None) == prop or getattr(s, "property_id", None) == prop:
            return getattr(s, "value", None) or getattr(s, "value_id", None)
    return None
