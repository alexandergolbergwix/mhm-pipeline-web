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
from typing import Any

from fastapi.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


@dataclass
class UploadOutcome:
    local_id: str
    label: str
    entity_type: str
    qid: str | None
    status: str             # success | updated | exists | skipped | blocked | failed
    message: str
    added_properties: list[str]


@dataclass
class ReconcileOutcome:
    local_id: str
    label: str
    entity_type: str
    existing_qid: str | None
    method: str             # "p3959/shelfmark" | "identifier" | "none" | "error"
    message: str


@dataclass
class PreparedItem:
    """An item after reconciliation + validation, ready for an upload decision."""

    item: Any
    local_id: str
    label: str
    entity_type: str
    existing_qid: str | None
    method: str             # how existing_qid was resolved
    blocked: bool
    block_status: str       # "" | "blocked"
    block_message: str


# ── Shared reconcile + validation core ──────────────────────────────────


def _make_reconciler() -> Any:
    """Construct a live ``WikidataReconciler``.

    Isolated behind a module-level function so tests can monkeypatch it to
    inject a fake reconciler without touching the network.
    """
    from converter.wikidata.reconciler import WikidataReconciler  # noqa: PLC0415

    return WikidataReconciler()


def _reconcile_for_upload(
    reconciler: Any, item: Any, entity_type: str,
) -> tuple[str | None, str]:
    """Look up an existing Wikidata QID for *item*. Returns ``(qid, method)``.

    Manuscripts reconcile by **P3959** (the NNL catalog id our items actually
    carry) then shelfmark; persons via the conflict-checked identifier path
    (VIAF / NLI / LCCN / GND / ISNI). Works have no deterministic identifier
    path and are left to CREATE.

    Raises:
        ReconciliationUnavailableError: a SPARQL lookup could not be completed, so
        the caller must fail closed and refuse to CREATE on uncertainty.
    """
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
        # Works (the abstract texts a manuscript contains, linked via the
        # P1574 exemplar-of chain) have no deterministic identifier, so we
        # reconcile by label + author using the conflict-aware matcher: a
        # candidate whose P50 author DIFFERS from ours is rejected as a
        # different work, never merged.
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


def _prepare_for_upload(items: list[Any], reconciler: Any) -> list[PreparedItem]:
    """Resolve ``existing_qid`` (fail-closed) and run the validator gate.

    For every item with no builder-supplied QID we reconcile against live
    Wikidata. If the lookup CANNOT be completed we mark the item ``blocked`` —
    never created — so a transient WDQS outage can't mint duplicates. After
    reconciliation every item is run through ``validate_item``; any
    ERROR-severity issue blocks the write regardless of create-vs-update.

    Mutates ``item.existing_qid`` in place on a confirmed match so the
    downstream uploader takes UPDATE semantics.
    """
    from converter.wikidata.item_validator import validate_item  # noqa: PLC0415
    from converter.wikidata.reconciler import (  # noqa: PLC0415
        ReconciliationUnavailableError,
    )

    prepared: list[PreparedItem] = []
    for i, item in enumerate(items):
        local_id = _local_id(item, i)
        label = _label(item)
        et = getattr(item, "entity_type", "") or "other"
        existing = getattr(item, "existing_qid", None)
        method = "prebuilt" if existing else "none"

        # 1. Reconcile against live Wikidata when the builder didn't already
        #    supply a QID. Fail CLOSED on lookup error.
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
                ))
                continue
            if existing:
                item.existing_qid = existing

        # 2. Validator hard gate — block any ERROR-severity item before write.
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
            ))
            continue

        prepared.append(PreparedItem(
            item=item, local_id=local_id, label=label, entity_type=et,
            existing_qid=existing, method=method, blocked=False,
            block_status="", block_message="",
        ))
    return prepared


# ── Reconcile (preview endpoint) ─────────────────────────────────────────


async def reconcile_items(items: list[Any]) -> list[ReconcileOutcome]:
    """SPARQL-query Wikidata for each item to find existing matches.

    Returns one outcome per item. NEVER writes to Wikidata. This is the
    *preview* surface; the authoritative reconcile happens inside
    :func:`upload_items` so the upload decision can never drift from a stale
    preview. Both share :func:`_reconcile_for_upload`.
    """
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
                # Store back so a subsequent rebuild within this request sees it.
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
        except Exception as exc:  # noqa: BLE001 — never let one bad lookup kill the batch
            logger.warning("reconcile failed for %r: %s", label, exc)
            out.append(ReconcileOutcome(
                local_id=local_id, label=label, entity_type=et,
                existing_qid=None, method="error",
                message=f"Lookup failed: {exc}",
            ))

    return out


# ── Upload ──────────────────────────────────────────────────────────────


async def upload_items(
    items: list[Any], *,
    token: str,
    dry_run: bool,
) -> list[UploadOutcome]:
    """Upload (or dry-run) every item via the real WikidataUploader.

    Both dry-run and live reconcile each item against live Wikidata first
    (read-only SPARQL) and run the validator gate, so the dry-run preview is
    an accurate description of what the live run would do — including which
    items are BLOCKED.

    Live uploads respect the moratorium (``MORATORIUM_LIFTED=true``) and
    test-mode env (``WIKIDATA_TEST_MODE=true`` points at test.wikidata.org,
    bypassing the moratorium).
    """
    return await run_in_threadpool(_upload_sync, items, token, dry_run)


def _upload_sync(
    items: list[Any], token: str, dry_run: bool,
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
        return _dry_run(items)

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

    # Authoritative reconcile + validation BEFORE any write. Blocked items
    # never reach uploader.upload_item().
    reconciler = _make_reconciler()
    prepared = _prepare_for_upload(items, reconciler)

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
            out.append(
                UploadOutcome(
                    local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                    qid=result.qid, status=result.status, message=result.message,
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


def _dry_run(items: list[Any]) -> list[UploadOutcome]:
    reconciler = _make_reconciler()
    prepared = _prepare_for_upload(items, reconciler)

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
        out.append(
            UploadOutcome(
                local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                qid=p.existing_qid,
                status="exists" if p.existing_qid else "success",
                message=(
                    f"Dry-run: would UPDATE {p.existing_qid} "
                    f"(matched via {p.method}; Rule-38 guards run live)"
                    if p.existing_qid
                    else f"Dry-run: would CREATE with {len(stmts)} statement(s) "
                         "(reconciliation found no existing item)"
                ),
                added_properties=[
                    getattr(s, "property", None) or getattr(s, "property_id", None) or ""
                    for s in stmts
                ],
            ),
        )
    return out


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
