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

The default upload target is dry-run (moratorium active). Curators pick
``upload_target`` in Wikidata Studio: ``dry_run`` | ``test`` | ``live``.
Live writes to ``wikidata.org`` require an explicit ``live`` choice (or
legacy ``MORATORIUM_LIFTED=true``). ``test`` points at test.wikidata.org
and bypasses the production moratorium.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

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

UPLOAD_TARGET_DRY_RUN = "dry_run"
UPLOAD_TARGET_TEST = "test"
UPLOAD_TARGET_LIVE = "live"
UploadTarget = Literal["dry_run", "test", "live"]
VALID_UPLOAD_TARGETS = frozenset({
    UPLOAD_TARGET_DRY_RUN, UPLOAD_TARGET_TEST, UPLOAD_TARGET_LIVE,
})

# Per-user Settings key names (``api_keys.key_name``). Bot passwords are
# per-wiki — live and test must not share one secret.
WIKIDATA_SECRET_LIVE = "wikidata"
WIKIDATA_SECRET_TEST = "wikidata_test"


def wikidata_secret_key_for_target(upload_target: str | None) -> str:
    """Return the Settings secret name for this upload target."""
    if (upload_target or "").strip().lower() == UPLOAD_TARGET_TEST:
        return WIKIDATA_SECRET_TEST
    return WIKIDATA_SECRET_LIVE


@dataclass(frozen=True)
class UploadMode:
    target: UploadTarget
    dry_run: bool
    is_test: bool
    allow_live: bool

    @property
    def moratorium_lifted(self) -> bool:
        return self.allow_live or self.is_test

    @property
    def test_mode(self) -> bool:
        return self.is_test


def resolve_upload_mode(
    upload_target: str | None = None,
    *,
    dry_run: bool | None = None,
) -> UploadMode:
    """Resolve curator/job upload mode.

    Prefer explicit ``upload_target``. Legacy callers that only pass
    ``dry_run`` keep env-based live/test gating when ``dry_run=False``.
    """
    raw = (upload_target or "").strip().lower()
    if raw in VALID_UPLOAD_TARGETS:
        if raw == UPLOAD_TARGET_DRY_RUN:
            return UploadMode(UPLOAD_TARGET_DRY_RUN, True, False, False)
        if raw == UPLOAD_TARGET_TEST:
            return UploadMode(UPLOAD_TARGET_TEST, False, True, False)
        return UploadMode(UPLOAD_TARGET_LIVE, False, False, True)

    use_dry = True if dry_run is None else bool(dry_run)
    if use_dry:
        return UploadMode(UPLOAD_TARGET_DRY_RUN, True, False, False)
    is_test = os.environ.get("WIKIDATA_TEST_MODE", "").lower() == "true"
    allow_live = os.environ.get("MORATORIUM_LIFTED", "").lower() == "true"
    if is_test:
        return UploadMode(UPLOAD_TARGET_TEST, False, True, False)
    if allow_live:
        return UploadMode(UPLOAD_TARGET_LIVE, False, False, True)
    return UploadMode(UPLOAD_TARGET_LIVE, False, False, False)


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
class ForeignAccept:
    """Per-item curator accept to modify a foreign Wikidata QID."""

    accept_foreign_modify: bool = False
    accepted_foreign_qid: str | None = None


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
    ownership: str = "absent"  # own | foreign | unknown | absent
    allow_foreign_modify: bool = False


def _clear_qid_for_create(prepared: PreparedItem, *, suffix: str) -> PreparedItem:
    qid = prepared.existing_qid
    prepared.existing_qid = None
    try:
        prepared.item.existing_qid = None
    except Exception:  # noqa: BLE001
        pass
    prepared.method = f"{prepared.method}+{suffix}"
    prepared.ownership = "absent"
    prepared.adopt_candidate = False
    prepared.blocked = False
    prepared.block_status = ""
    prepared.block_message = ""
    logger.info(
        "Cleared QID %s for CREATE (%s, local_id=%s)",
        qid, suffix, prepared.local_id,
    )
    return prepared


def _apply_person_identity_gate(
    prepared: PreparedItem,
    *,
    qid: str,
    is_test: bool,
    ownership_checker: Any | None,
) -> PreparedItem:
    """Refuse UPDATE of a live person whose labels clash with the Studio heading (W-190)."""
    from app.pipeline.wikidata_duplicate_probe import (  # noqa: PLC0415
        person_heading_conflicts_live_label,
    )
    from app.pipeline.wikidata_existence import (  # noqa: PLC0415
        classify_ownership_with_uploader,
        fetch_entity_labels,
    )

    labels = fetch_entity_labels([qid], is_test=is_test).get(qid) or {}
    live_en = str(labels.get("en") or "")
    live_he = str(labels.get("he") or "")
    if not live_en and not live_he:
        prepared.blocked = True
        prepared.block_status = "blocked"
        prepared.block_message = (
            f"Could not read labels for person QID {qid}; refusing UPDATE "
            "(Rule W-190)."
        )
        prepared.ownership = "unknown"
        return prepared
    if not person_heading_conflicts_live_label(
        prepared.item, live_en=live_en, live_he=live_he,
    ):
        return prepared

    ownership = "unknown"
    if ownership_checker is not None:
        ownership = classify_ownership_with_uploader(ownership_checker, qid)
    prepared.ownership = ownership
    if ownership == "own":
        prepared.blocked = True
        prepared.block_status = "blocked"
        prepared.block_message = (
            f"Person QID {qid} ({live_en or live_he}) clashes with the Studio "
            "heading. Refusing UPDATE of an item we already wrote — unlink the "
            "QID before CREATE (Rule W-190)."
        )
        return prepared
    return _clear_qid_for_create(prepared, suffix="cleared_identity_clash")


def _apply_existence_and_ownership(
    prepared: PreparedItem,
    *,
    accept: ForeignAccept | None,
    ownership_checker: Any | None,
    is_test: bool,
    existence_cache: dict[str, bool | None] | None = None,
) -> PreparedItem:
    """Confirm QID alive + enforce create-or-own / explicit-foreign-accept policy."""
    from app.pipeline.wikidata_existence import (  # noqa: PLC0415
        accept_allows_foreign_modify,
        classify_ownership_with_uploader,
        confirm_qid_alive,
    )

    if prepared.blocked:
        return prepared
    qid = prepared.existing_qid
    if not qid:
        prepared.ownership = "absent"
        return prepared

    clean_qid = str(qid).strip()
    alive: bool | None
    if existence_cache is not None and clean_qid in existence_cache:
        alive = existence_cache[clean_qid]
    else:
        alive = confirm_qid_alive(clean_qid, is_test=is_test)
    if alive is None:
        alive = confirm_qid_alive(clean_qid, is_test=is_test, retries=4)
    if alive is False:
        if is_test:
            # Live/reconciled QIDs often do not exist on test.wikidata.org.
            # Blocking CREATE there stranded the canary (export-40). Clear the
            # ghost QID and CREATE a test item instead (Rule W-181).
            logger.info(
                "test upload: QID %s missing on test.wikidata.org — clearing "
                "for CREATE (local_id=%s)",
                qid, prepared.local_id,
            )
            prepared.existing_qid = None
            try:
                prepared.item.existing_qid = None
            except Exception:  # noqa: BLE001
                pass
            prepared.method = f"{prepared.method}+cleared_missing_on_test"
            prepared.ownership = "absent"
            prepared.adopt_candidate = False
            prepared.blocked = False
            prepared.block_status = ""
            prepared.block_message = ""
            return prepared
        # Stale ledger/reconcile hit — clear QID so we do not UPDATE a ghost,
        # but BLOCK CREATE until the curator re-reconciles (fail closed).
        prepared.blocked = True
        prepared.block_status = "blocked"
        prepared.block_message = (
            f"Reconciled QID {qid} is missing on Wikidata (wbgetentities). "
            "Refusing CREATE/UPDATE until reconcile is re-run."
        )
        prepared.ownership = "unknown"
        return prepared
    if alive is None:
        prepared.blocked = True
        prepared.block_status = "blocked"
        prepared.block_message = (
            f"Could not confirm that {qid} still exists (Action API unavailable). "
            "Fail-closed: refusing write until the entity can be verified."
        )
        prepared.ownership = "unknown"
        return prepared

    if str(prepared.entity_type or "").lower() == "person":
        prepared = _apply_person_identity_gate(
            prepared,
            qid=clean_qid,
            is_test=is_test,
            ownership_checker=ownership_checker,
        )
        if prepared.blocked or not prepared.existing_qid:
            return prepared

    acc = accept or ForeignAccept()
    if (
        not is_test
        and accept_allows_foreign_modify(
            existing_qid=qid,
            accept_foreign_modify=bool(acc.accept_foreign_modify),
            accepted_foreign_qid=acc.accepted_foreign_qid,
        )
    ):
        # Explicit per-QID accept is sufficient even without a live ownership
        # classify (e.g. dry-run before token unwrap). Still fail-closed on
        # missing/ghost QIDs above. Live only — test never UPDATEs foreign items.
        prepared.ownership = "foreign"
        prepared.allow_foreign_modify = True
        return prepared

    if ownership_checker is None:
        prepared.ownership = "unknown"
        prepared.blocked = True
        prepared.block_status = "blocked"
        prepared.block_message = (
            f"Existing item {qid} matched via {prepared.method}, but no Wikidata "
            "token is available to verify ownership. Default policy: only CREATE "
            "new items or UPDATE items you created — provide a token, or set "
            "accept_foreign_modify bound to this QID after review."
        )
        return prepared

    ownership = classify_ownership_with_uploader(ownership_checker, qid)
    prepared.ownership = ownership
    if ownership == "own":
        return prepared

    if ownership == "foreign" and is_test:
        logger.info(
            "test upload: foreign QID %s on test.wikidata.org — clearing "
            "for CREATE (local_id=%s)",
            qid,
            prepared.local_id,
        )
        prepared.existing_qid = None
        try:
            prepared.item.existing_qid = None
        except Exception:  # noqa: BLE001
            pass
        prepared.method = f"{prepared.method}+cleared_foreign_on_test"
        prepared.ownership = "absent"
        prepared.adopt_candidate = False
        prepared.blocked = False
        prepared.block_status = ""
        prepared.block_message = ""
        return prepared

    prepared.blocked = True
    prepared.block_status = "skipped"
    if ownership == "foreign":
        prepared.block_message = (
            f"Existing Wikidata item {qid} was not created by your account. "
            "Default policy forbids modifying community items. To proceed, "
            "open the item and explicitly accept modification of this QID "
            f"(accept_foreign_modify + accepted_foreign_qid={qid}). "
            "A duplicate CREATE is also refused."
        )
    else:
        prepared.block_message = (
            f"Could not prove ownership of {qid}. Fail-closed: refusing "
            "UPDATE/CREATE. Retry with a valid token or accept foreign modify."
        )
    return prepared


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
    accept_by_local_id: dict[str, ForeignAccept] | None = None,
    ownership_checker: Any | None = None,
    is_test: bool = False,
    enforce_ownership: bool = False,
    existence_cache: dict[str, bool | None] | None = None,
) -> list[PreparedItem]:
    from converter.wikidata.item_validator import validate_item  # noqa: PLC0415
    from converter.wikidata.reconciler import (  # noqa: PLC0415
        ReconciliationUnavailableError,
    )

    ns = ledger_ns or ledger_namespace()
    accepts = accept_by_local_id or {}
    prepared: list[PreparedItem] = []
    for i, item in enumerate(items):
        local_id = _local_id(item, i)
        label = _label(item)
        et = getattr(item, "entity_type", "") or "other"
        had_builder_qid = bool(getattr(item, "existing_qid", None))
        existing = getattr(item, "existing_qid", None)
        method = "prebuilt" if existing else "none"
        adopt_candidate = False

        if et == "person":
            from app.pipeline.wikidata_canonical_enrichment import (  # noqa: PLC0415
                _UPLOAD_SKIP_MESSAGE,
                is_publishable_person_item,
                recover_person_identifiers_from_evidence,
            )

            recover_person_identifiers_from_evidence(item)
            if not is_publishable_person_item(item):
                prepared.append(PreparedItem(
                    item=item, local_id=local_id, label=label, entity_type=et,
                    existing_qid=existing, method=method, blocked=False,
                    block_status="skipped",
                    block_message=_UPLOAD_SKIP_MESSAGE,
                    had_builder_qid=had_builder_qid,
                ))
                continue

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
                if is_test:
                    # Test wiki is disposable; do not strand the canary on
                    # production WDQS 429/502 (Rule W-181). Proceed as CREATE.
                    logger.warning(
                        "test upload: WDQS unavailable for %s — CREATE without "
                        "reconcile (%s)",
                        local_id, exc,
                    )
                    existing = None
                    method = "wdqs_unavailable_test_create"
                else:
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
            row = PreparedItem(
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
            )
            prepared.append(row)
            continue

        row = PreparedItem(
            item=item, local_id=local_id, label=label, entity_type=et,
            existing_qid=existing, method=method, blocked=False,
            block_status="", block_message="",
            had_builder_qid=had_builder_qid,
            adopt_candidate=adopt_candidate,
            ownership="absent" if not existing else "unknown",
        )
        if enforce_ownership:
            row = _apply_existence_and_ownership(
                row,
                accept=accepts.get(local_id),
                ownership_checker=ownership_checker,
                is_test=is_test,
                existence_cache=existence_cache,
            )
        prepared.append(row)
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
        except Exception as exc:  # noqa: BLE001
            out.append(ReconcileOutcome(
                local_id=local_id, label=label, entity_type=et,
                existing_qid=None, method="error",
                message=f"Lookup failed: {exc}",
            ))
    return out


async def load_ledger_for_prepare(
    db: AsyncSession, *, is_test: bool | None = None,
) -> dict[str, str]:
    from app.pipeline.wikidata_qid_ledger import load_global_ledger  # noqa: PLC0415

    return await load_global_ledger(db, is_test=is_test)


async def load_foreign_accept_map(
    db: AsyncSession, run_id: uuid.UUID,
) -> dict[str, ForeignAccept]:
    """Load per-item foreign-modify accepts from WikidataItemOverride rows."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.item_override import WikidataItemOverride  # noqa: PLC0415

    rows = (
        await db.execute(
            select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
        )
    ).scalars().all()
    out: dict[str, ForeignAccept] = {}
    for row in rows:
        out[str(row.local_id)] = ForeignAccept(
            accept_foreign_modify=bool(row.accept_foreign_modify),
            accepted_foreign_qid=(
                str(row.accepted_foreign_qid).strip()
                if row.accepted_foreign_qid else None
            ),
        )
    return out



async def upload_items(
    items: list[Any], *,
    token: str,
    dry_run: bool | None = None,
    audit_ctx: WikibaseAuditContext | None = None,
    db: AsyncSession | None = None,
    ledger: dict[str, str] | None = None,
    accept_by_local_id: dict[str, ForeignAccept] | None = None,
    run_id: uuid.UUID | None = None,
    upload_target: str | None = None,
    mode: UploadMode | None = None,
    uploader: Any | None = None,
    existence_cache: dict[str, bool | None] | None = None,
    created_qids: dict[str, str] | None = None,
) -> list[UploadOutcome]:
    resolved = mode or resolve_upload_mode(upload_target, dry_run=dry_run)
    if ledger is None and db is not None:
        ledger = await load_ledger_for_prepare(db, is_test=resolved.is_test)
    if accept_by_local_id is None and db is not None and run_id is not None:
        accept_by_local_id = await load_foreign_accept_map(db, run_id)
    ns = ledger_namespace(is_test=resolved.is_test)
    outcomes = await run_in_threadpool(
        _upload_sync,
        items,
        token,
        resolved.dry_run,
        ledger or {},
        ns,
        accept_by_local_id or {},
        resolved.is_test,
        resolved.allow_live,
        uploader,
        existence_cache,
        created_qids,
    )
    if db is not None and audit_ctx is not None and not resolved.dry_run:
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
    run_id: uuid.UUID | None = None,
    upload_target: str = UPLOAD_TARGET_TEST,
) -> UploadOutcome:
    """Live create-or-update for exactly one native WikidataItem."""
    mode = resolve_upload_mode(upload_target, dry_run=False)
    if mode.dry_run:
        mode = resolve_upload_mode(UPLOAD_TARGET_TEST)
    ledger = await load_ledger_for_prepare(db, is_test=mode.is_test)
    rid = run_id or (audit_ctx.run_id if audit_ctx is not None else None)
    outcomes = await upload_items(
        [item], token=token, mode=mode,
        audit_ctx=audit_ctx, db=db, ledger=ledger,
        run_id=rid,
    )
    return outcomes[0]


def _upload_sync(
    items: list[Any],
    token: str,
    dry_run: bool,
    ledger: dict[str, str],
    ledger_ns: str,
    accept_by_local_id: dict[str, ForeignAccept] | None = None,
    is_test: bool | None = None,
    allow_live: bool = False,
    uploader: Any | None = None,
    existence_cache: dict[str, bool | None] | None = None,
    created_qids: dict[str, str] | None = None,
) -> list[UploadOutcome]:
    from converter.wikidata.uploader import (  # noqa: PLC0415
        UnauthorisedModificationError,
        WikidataUploader,
        sort_items_for_upload,
    )

    if is_test is None:
        is_test = os.environ.get("WIKIDATA_TEST_MODE", "").lower() == "true"
    if not allow_live:
        allow_live = os.environ.get("MORATORIUM_LIFTED", "").lower() == "true"
    accepts = accept_by_local_id or {}
    items = sort_items_for_upload(list(items))
    session_qids = created_qids if created_qids is not None else {}

    # Prefer a shared uploader (Rule W-179) so batch jobs log in once.
    ownership_checker: Any | None = uploader
    if ownership_checker is None and token:
        try:
            ownership_checker = WikidataUploader(
                token=token,
                is_test=is_test,
                batch_mode=True,
                allow_live=allow_live,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not init WikidataUploader for ownership: %s", exc)
            ownership_checker = None

    if dry_run:
        return _dry_run(
            items, ledger, ledger_ns,
            accept_by_local_id=accepts,
            ownership_checker=ownership_checker,
            is_test=is_test,
            existence_cache=existence_cache,
        )

    if not is_test and not allow_live:
        return [
            UploadOutcome(
                local_id=_local_id(it, i),
                label=_label(it),
                entity_type=getattr(it, "entity_type", "") or "other",
                qid=getattr(it, "existing_qid", None),
                status="skipped",
                message=(
                    "Live upload refused — choose upload target "
                    "'test' (test.wikidata.org) or 'live' (wikidata.org) "
                    "in Wikidata Studio, or set MORATORIUM_LIFTED=true."
                ),
                added_properties=[],
            )
            for i, it in enumerate(items)
        ]

    reconciler = _make_reconciler()
    prepared = _prepare_for_upload(
        items, reconciler, ledger=ledger, ledger_ns=ledger_ns,
        accept_by_local_id=accepts,
        ownership_checker=ownership_checker,
        is_test=is_test,
        enforce_ownership=True,
        existence_cache=existence_cache,
    )

    write_uploader = ownership_checker or WikidataUploader(
        token=token, is_test=is_test, batch_mode=True, allow_live=allow_live,
    )
    if is_test:
        warmer = getattr(write_uploader, "warm_test_maps_for_items", None)
        if callable(warmer):
            warmer([p.item for p in prepared if not p.blocked])

    out: list[UploadOutcome] = []
    for p in prepared:
        if p.block_status == "skipped" and not p.blocked:
            out.append(UploadOutcome(
                local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                qid=p.existing_qid, status="skipped",
                message=p.block_message, added_properties=[],
            ))
            continue
        if p.blocked:
            out.append(UploadOutcome(
                local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                qid=p.existing_qid, status=p.block_status or "blocked",
                message=p.block_message, added_properties=[],
            ))
            continue
        try:
            if p.allow_foreign_modify and p.existing_qid and not is_test:
                write_uploader.register_foreign_accept(p.existing_qid)
            result = write_uploader.upload_item(
                p.item, created_qids=session_qids,
            )
            status = result.status
            if p.adopt_candidate and status == "updated":
                status = "adopted"
            elif status == "success":
                status = "created"
            if (
                result.qid
                and status in {"created", "updated", "adopted", "exists"}
            ):
                session_qids[p.local_id] = result.qid
            elif (
                result.qid
                and status == "skipped"
                and not is_test
            ):
                session_qids[p.local_id] = result.qid
            out.append(
                UploadOutcome(
                    local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                    qid=result.qid, status=status, message=result.message,
                    added_properties=list(result.added_properties or []),
                ),
            )
            if status == "failed" and _is_auth_failure_message(result.message):
                break
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
            if _is_auth_failure_message(str(exc)):
                # Stop the batch immediately — further logins will rate-limit
                # (Rule W-179). Callers that share an uploader should abort the job.
                break
    return out


def _is_auth_failure_message(message: str) -> bool:
    low = (message or "").lower()
    return (
        "login failed" in low
        or "incorrect username or password" in low
        or "too many recent login attempts" in low
        or "anonymous token was returned" in low
        or "invalid authentication format" in low
        or "additional verification step" in low
        or "permissiondenied" in low
        or "permissions needed" in low
        or "you are no longer logged in" in low
        or "notloggedin" in low
        or "assertuserfailed" in low
        or "globally blocked" in low
        or "blocked globally" in low
        or "open proxy" in low
    )


def _dry_run(
    items: list[Any],
    ledger: dict[str, str],
    ledger_ns: str,
    *,
    accept_by_local_id: dict[str, ForeignAccept] | None = None,
    ownership_checker: Any | None = None,
    is_test: bool = False,
    existence_cache: dict[str, bool | None] | None = None,
) -> list[UploadOutcome]:
    reconciler = _make_reconciler()
    prepared = _prepare_for_upload(
        items, reconciler, ledger=ledger, ledger_ns=ledger_ns,
        accept_by_local_id=accept_by_local_id or {},
        ownership_checker=ownership_checker,
        is_test=is_test,
        enforce_ownership=True,
        existence_cache=existence_cache,
    )

    out: list[UploadOutcome] = []
    for p in prepared:
        stmts = getattr(p.item, "statements", []) or []
        if p.block_status == "skipped" and not p.blocked:
            out.append(UploadOutcome(
                local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                qid=p.existing_qid, status="skipped",
                message=p.block_message, added_properties=[],
            ))
            continue
        if p.blocked:
            out.append(UploadOutcome(
                local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                qid=p.existing_qid, status=p.block_status or "blocked",
                message=f"Dry-run: would be {p.block_status or 'BLOCKED'} — {p.block_message}",
                added_properties=[],
            ))
            continue
        if p.ownership == "own" and p.existing_qid:
            status = "exists"
            message = (
                f"Dry-run: would UPDATE {p.existing_qid} "
                f"(owned by your account; matched via {p.method})"
            )
        elif p.allow_foreign_modify and p.existing_qid:
            status = "would_adopt"
            message = (
                f"Dry-run: would UPDATE foreign {p.existing_qid} "
                f"(explicit accept_foreign_modify; matched via {p.method})"
            )
        elif p.adopt_candidate and p.existing_qid:
            status = "would_adopt"
            message = (
                f"Dry-run: would ADOPT {p.existing_qid} "
                f"(matched via {p.method}; ownership={p.ownership})"
            )
        elif p.existing_qid:
            status = "exists"
            message = (
                f"Dry-run: would UPDATE {p.existing_qid} "
                f"(matched via {p.method}; ownership={p.ownership})"
            )
        else:
            status = "success"
            message = (
                f"Dry-run: would CREATE with {len(stmts)} statement(s) "
                "(smart reconcile found no existing item)"
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
