"""Drive WikidataReconciler + WikidataUploader from the web.

All four guards from the desktop pipeline are kept INTACT:

1. ``_is_our_item()`` — refuses to modify items whose first revision
   wasn't authored by the authenticated user (Rule 38 gate 1).
2. ``_assert_modifiable()`` inside ``_build_wbi_item`` (gate 2).
3. ``_would_create_identity_conflict()`` per statement (gate 3).
4. Pre-write ``_assert_modifiable()`` immediately before
   ``wbi_item.write()`` (gate 4).

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
    status: str             # success | updated | exists | skipped | failed
    message: str
    added_properties: list[str]


@dataclass
class ReconcileOutcome:
    local_id: str
    label: str
    entity_type: str
    existing_qid: str | None
    method: str             # "viaf" | "nli" | "label" | "none"
    message: str


# ── Reconcile ───────────────────────────────────────────────────────────


async def reconcile_items(items: list[Any]) -> list[ReconcileOutcome]:
    """SPARQL-query Wikidata for each item to find existing matches.
    Returns one outcome per item. NEVER writes to Wikidata.
    """
    return await run_in_threadpool(_reconcile_sync, items)


def _reconcile_sync(items: list[Any]) -> list[ReconcileOutcome]:
    from converter.wikidata.reconciler import WikidataReconciler  # noqa: PLC0415

    rec = WikidataReconciler()
    out: list[ReconcileOutcome] = []
    for i, item in enumerate(items):
        local_id = _local_id(item, i)
        label = _label(item)
        et = getattr(item, "entity_type", "") or "other"
        qid: str | None = None
        method = "none"

        try:
            if et == "person":
                # Try all five identity properties in order: VIAF, NLI/J9U,
                # LCCN, GND, ISNI. This mirrors reconcile_person and prevents
                # duplicate item creation when one authority has a match even
                # if VIAF/NLI aren't available. The five-ID check was the fix
                # for the April 2026 mass-duplicate incident.
                viaf_id = _find_statement_value(item, "P214")
                nli_id = _find_statement_value(item, "P8189")
                lc_id = _find_statement_value(item, "P244")
                gnd_id = _find_statement_value(item, "P227")
                isni = _find_statement_value(item, "P213")
                if viaf_id:
                    qid = rec.reconcile_person_by_viaf(str(viaf_id))
                    if qid: method = "viaf"
                if not qid and nli_id:
                    qid = rec.reconcile_person_by_nli_id(str(nli_id))
                    if qid: method = "nli"
                if not qid and lc_id:
                    qid = rec.reconcile_person_by_external_id("P244", str(lc_id))
                    if qid: method = "lccn"
                if not qid and gnd_id:
                    qid = rec.reconcile_person_by_external_id("P227", str(gnd_id))
                    if qid: method = "gnd"
                if not qid and isni:
                    qid = rec.reconcile_person_by_external_id("P213", str(isni))
                    if qid: method = "isni"
            elif et == "manuscript":
                # Manuscript by NLI control number (P8189 or label fallback).
                nli_id = _find_statement_value(item, "P8189")
                if nli_id:
                    qid = rec.reconcile_manuscript_by_nli_id(str(nli_id))
                    if qid: method = "nli"
            # works don't have a deterministic identifier path; leave as-is

            if qid:
                # Store back so the next Studio build / upload uses it.
                item.existing_qid = qid

            out.append(
                ReconcileOutcome(
                    local_id=local_id, label=label, entity_type=et,
                    existing_qid=qid, method=method,
                    message=(
                        f"Found {qid} via {method}" if qid else "No existing Wikidata item found"
                    ),
                ),
            )
        except Exception as exc:  # noqa: BLE001 — never let one bad lookup kill the batch
            logger.warning("reconcile failed for %r: %s", label, exc)
            out.append(
                ReconcileOutcome(
                    local_id=local_id, label=label, entity_type=et,
                    existing_qid=None, method="error",
                    message=f"Lookup failed: {exc}",
                ),
            )

    return out


# ── Upload ──────────────────────────────────────────────────────────────


async def upload_items(
    items: list[Any], *,
    token: str,
    dry_run: bool,
) -> list[UploadOutcome]:
    """Upload (or dry-run) every item via the real WikidataUploader.

    Dry-run reports what WOULD happen (gates that fire, items skipped,
    items eligible for creation) without making any network writes.

    Live uploads respect the moratorium (``MORATORIUM_LIFTED=true``) and
    test-mode env (``WIKIDATA_TEST_MODE=true`` points at
    test.wikidata.org, bypassing the moratorium).
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

    uploader = WikidataUploader(token=token, is_test=is_test, batch_mode=True)

    out: list[UploadOutcome] = []
    for i, item in enumerate(items):
        local_id = _local_id(item, i)
        label = _label(item)
        et = getattr(item, "entity_type", "") or "other"
        try:
            result = uploader.upload_item(item)
            out.append(
                UploadOutcome(
                    local_id=local_id, label=label, entity_type=et,
                    qid=result.qid, status=result.status, message=result.message,
                    added_properties=list(result.added_properties or []),
                ),
            )
        except UnauthorisedModificationError as exc:
            out.append(
                UploadOutcome(
                    local_id=local_id, label=label, entity_type=et,
                    qid=exc.qid, status="skipped",
                    message=f"Rule-38 guard fired ({exc.stage}): refusing to "
                            f"modify {exc.qid} (not authored by us)",
                    added_properties=[],
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("upload failed for %r", label)
            out.append(
                UploadOutcome(
                    local_id=local_id, label=label, entity_type=et,
                    qid=getattr(item, "existing_qid", None),
                    status="failed", message=str(exc), added_properties=[],
                ),
            )
    return out


def _dry_run(items: list[Any]) -> list[UploadOutcome]:
    out: list[UploadOutcome] = []
    for i, item in enumerate(items):
        local_id = _local_id(item, i)
        label = _label(item)
        et = getattr(item, "entity_type", "") or "other"
        existing = getattr(item, "existing_qid", None)
        stmts = getattr(item, "statements", []) or []
        out.append(
            UploadOutcome(
                local_id=local_id, label=label, entity_type=et,
                qid=existing,
                status="exists" if existing else "success",
                message=(
                    f"Dry-run: would UPDATE {existing} (Rule-38 guards run live)"
                    if existing
                    else f"Dry-run: would CREATE with {len(stmts)} statement(s)"
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
