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

import asyncio
import logging
import os
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
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
    ownership: str = ""
    accepted_foreign_qid: str | None = None
    audit_status: str = ""


@dataclass
class DeferredLinkOutcome:
    source_local_id: str
    property_id: str
    target_local_id: str
    status: str
    message: str
    qid: str | None = None


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
    link_qid: str | None = None


@dataclass(frozen=True, slots=True)
class UploadQualityDecision:
    """The quality status for one entity in a checked upload corpus."""

    eligible: bool
    findings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UploadCorpusQualityPreflight:
    """One complete quality result that guards later per-item upload calls."""

    decisions: dict[str, UploadQualityDecision]
    execution_findings: tuple[str, ...] = ()


def build_upload_corpus_quality_preflight(
    items: list[Any],
) -> UploadCorpusQualityPreflight:
    """Map full-corpus hard Findings to entities before any item write."""
    from app.pipeline.wikidata_canonical_enrichment import (  # noqa: PLC0415
        is_publishable_person_item,
        recover_person_identifiers_from_evidence,
    )
    from app.pipeline.wikidata_export_quality_gate import (  # noqa: PLC0415
        FindingSeverity,
        wikidata_export_quality_findings,
    )

    local_ids = [_local_id(item, index) for index, item in enumerate(items)]
    known_ids = set(local_ids)
    skipped_person_ids: set[str] = set()
    for index, item in enumerate(items):
        if str(getattr(item, "entity_type", "") or "").lower() != "person":
            continue
        recover_person_identifiers_from_evidence(item)
        if not is_publishable_person_item(item):
            skipped_person_ids.add(_local_id(item, index))
    findings_by_id: dict[str, list[str]] = {local_id: [] for local_id in local_ids}
    execution_findings: list[str] = []

    for finding in wikidata_export_quality_findings(items):
        target_ids = (
            finding.identity_group.entity_ids
            if finding.identity_group is not None
            else finding.entity_ids
        )
        if target_ids:
            target_ids = tuple(
                target for target in target_ids
                if target not in skipped_person_ids
            )
            if not target_ids:
                continue
        if finding.severity is FindingSeverity.INFORMATIONAL:
            logger.warning(
                "wikidata upload quality [informational] %s",
                finding.render(),
            )
            continue
        if not target_ids or any(target not in known_ids for target in target_ids):
            execution_findings.append(finding.render())
            continue
        rendered = finding.render()
        for target in target_ids:
            findings_by_id[target].append(rendered)

    decisions = {
        local_id: UploadQualityDecision(
            eligible=not entity_findings,
            findings=tuple(entity_findings),
        )
        for local_id, entity_findings in findings_by_id.items()
    }
    return UploadCorpusQualityPreflight(
        decisions=decisions,
        execution_findings=tuple(execution_findings),
    )


PASS2_CREATE_STATUSES = frozenset({"created", "would_create", "success"})
PASS2_OWN_UPDATE_STATUSES = frozenset({
    "updated", "would_update", "adopted", "would_adopt", "exists",
})


def pass2_may_update_source(
    outcome: UploadOutcome | None, target_qid: str | None, *,
    accept: ForeignAccept | None = None, dry_run: bool = False,
) -> bool:
    """Allow pass 2 only for the target established by the first pass."""
    if outcome is None or not target_qid:
        return False
    if not outcome.qid:
        return bool(
            dry_run
            and outcome.status in PASS2_CREATE_STATUSES
            and outcome.ownership in {"", "absent"}
            and outcome.local_id
            and target_qid == f"dry:{outcome.local_id}"
        )
    if outcome.qid != target_qid or not re.fullmatch(r"Q[1-9][0-9]*", target_qid):
        return False
    if outcome.status in PASS2_CREATE_STATUSES and outcome.ownership in {"", "absent"}:
        return True
    if (
        outcome.status in PASS2_OWN_UPDATE_STATUSES
        and (
            outcome.ownership == "own"
            or (
                outcome.ownership == "foreign"
                and outcome.accepted_foreign_qid == target_qid
                and accept is not None
                and accept.accept_foreign_modify
                and str(accept.accepted_foreign_qid or "").strip() == target_qid
            )
        )
    ):
        return True
    return False


def _classify_upload_ownership(ownership_checker: Any, qid: str) -> str:
    from app.pipeline.wikidata_existence import classify_ownership_with_uploader  # noqa: PLC0415

    if qid in (getattr(ownership_checker, "_foreign_accept_qids", None) or ()):
        return "foreign"
    ownership = classify_ownership_with_uploader(ownership_checker, qid)
    if qid in (getattr(ownership_checker, "_foreign_accept_qids", None) or ()):
        return "foreign"
    return ownership


def _skip_if_person_unpublishable(prepared: PreparedItem) -> PreparedItem:
    """After a clash strip, omit identifierless persons instead of CREATE (W-154)."""
    if str(prepared.entity_type or "").lower() != "person":
        return prepared
    from app.pipeline.wikidata_canonical_enrichment import (  # noqa: PLC0415
        _UPLOAD_SKIP_MESSAGE,
    )
    from app.pipeline.wikidata_live_native_hygiene import (  # noqa: PLC0415
        person_has_publishable_identity,
    )

    if person_has_publishable_identity(prepared.item):
        return prepared
    prepared.blocked = False
    prepared.block_status = "skipped"
    prepared.block_message = _UPLOAD_SKIP_MESSAGE
    prepared.method = f"{prepared.method}+skipped_unpublishable"
    return prepared


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
        ownership = _classify_upload_ownership(ownership_checker, qid)
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
    from app.pipeline.wikidata_live_native_hygiene import strip_identity_pids

    strip_identity_pids(prepared.item)
    prepared = _clear_qid_for_create(prepared, suffix="cleared_identity_clash")
    return _skip_if_person_unpublishable(prepared)


def _item_has_identity_pid(item: Any) -> bool:
    from app.pipeline.wikidata_live_native_hygiene import item_has_identity_pid  # noqa: PLC0415

    return item_has_identity_pid(item)


def _match_needs_duplicate_confirm(prepared: PreparedItem) -> bool:
    if not prepared.existing_qid:
        return False
    et = str(prepared.entity_type or "").lower()
    if et not in {"person", "work"}:
        return False
    method = str(prepared.method or "").lower()
    if "identifier" in method:
        return False
    return not _item_has_identity_pid(prepared.item)


def _skip_uncertain_duplicate(prepared: PreparedItem, *, reason: str) -> PreparedItem:
    prepared.blocked = False
    prepared.block_status = "skipped"
    prepared.block_message = (
        f"Uncertain live duplicate ({reason}); refusing CREATE/UPDATE "
        "(Rule W-195)."
    )
    prepared.method = f"{prepared.method}+skipped_uncertain_duplicate"
    prepared.existing_qid = None
    try:
        prepared.item.existing_qid = None
    except Exception:  # noqa: BLE001
        pass
    prepared.ownership = "absent"
    prepared.adopt_candidate = False
    return prepared


def _skip_work_link_only(
    prepared: PreparedItem, *, qid: str, reason: str,
) -> PreparedItem:
    """Skip CREATE/UPDATE but keep the live QID for manuscript P1574 (W-196)."""
    clean = str(qid or "").strip()
    prepared.link_qid = clean or None
    prepared.blocked = False
    prepared.block_status = "skipped"
    prepared.block_message = (
        f"Catalog work already exists as {clean} ({reason}); "
        "refusing CREATE/UPDATE (Rule W-196). Manuscripts may P1574 that QID."
    )
    prepared.method = f"{prepared.method}+link_only"
    prepared.existing_qid = None
    try:
        prepared.item.existing_qid = None
    except Exception:  # noqa: BLE001
        pass
    prepared.ownership = "absent"
    prepared.adopt_candidate = False
    return prepared


def _apply_uncertain_duplicate_confirm(
    prepared: PreparedItem, *, is_test: bool,
) -> PreparedItem:
    from app.pipeline.wikidata_duplicate_confirm import (  # noqa: PLC0415
        DIFFERENT_ITEM,
        SAME_ITEM,
        confirm_uncertain_duplicate,
    )
    from app.pipeline.wikidata_existence import (  # noqa: PLC0415
        fetch_entity_labels,
        fetch_entity_p31,
    )

    qid = str(prepared.existing_qid or "").strip()
    labels = fetch_entity_labels([qid], is_test=is_test).get(qid) or {}
    is_work = str(prepared.entity_type or "").lower() == "work"
    try:
        if is_work and not is_test:
            p31 = fetch_entity_p31([qid], is_test=False, require_complete=True).get(qid)
        else:
            p31 = fetch_entity_p31([qid], is_test=is_test).get(qid)
    except Exception as exc:  # noqa: BLE001 — live identity must fail closed
        logger.warning("Could not read P31 for %s: %s", qid, exc)
        p31 = None
    if p31 is None and not is_test:
        prepared.blocked = True
        prepared.block_status = "blocked"
        prepared.block_message = (
            f"Item {qid} has unverified P31 metadata; refusing adoption, "
            "UPDATE or CREATE until the entity can be checked."
        )
        prepared.method = "work_identity_review" if is_work else "identity_review"
        return prepared
    p31 = p31 or []
    has_id = _item_has_identity_pid(prepared.item)
    item_p2888 = str(_find_statement_value(prepared.item, "P2888") or "").strip()
    catalog_author = str(
        _find_statement_value(prepared.item, "P2093")
        or _find_statement_value(prepared.item, "P50")
        or ""
    ).strip()
    raw_descs = getattr(prepared.item, "descriptions", None) or {}
    catalog_desc = (
        str(raw_descs.get("en") or raw_descs.get("he") or "").strip()
        if isinstance(raw_descs, dict) else ""
    )
    live_desc_en = ""
    live_desc_he = ""
    live_authors: list[str] = []
    if item_p2888 or catalog_author or catalog_desc:
        from app.pipeline.wikidata_existence import (  # noqa: PLC0415
            fetch_entity_duplicate_metadata,
        )

        meta = fetch_entity_duplicate_metadata([qid], is_test=is_test).get(qid) or {}
        live_p2888 = set(meta.get("p2888") or [])
        if item_p2888 and item_p2888 in live_p2888:
            prepared.method = f"{prepared.method}+p2888_identifier"
            return prepared
        live_descs = meta.get("descriptions") or {}
        if isinstance(live_descs, dict):
            live_desc_en = str(live_descs.get("en") or "")
            live_desc_he = str(live_descs.get("he") or "")
        live_authors = list(meta.get("authors") or [])
    verdict = confirm_uncertain_duplicate(
        local_id=prepared.local_id,
        entity_type=prepared.entity_type,
        heading=prepared.label,
        candidate_qid=qid,
        method=prepared.method,
        has_trusted_identifier=has_id,
        live_en=str(labels.get("en") or ""),
        live_he=str(labels.get("he") or ""),
        live_p31=p31,
        catalog_description=catalog_desc,
        catalog_author=catalog_author,
        live_description_en=live_desc_en,
        live_description_he=live_desc_he,
        live_authors=live_authors,
    )
    if verdict == SAME_ITEM:
        if not has_id and "identifier" not in str(prepared.method or "").lower():
            if str(prepared.entity_type or "").lower() == "work":
                from app.pipeline.wikidata_live_native_hygiene import (  # noqa: PLC0415
                    work_p31_allows_link_only,
                )

                if work_p31_allows_link_only(p31):
                    return _skip_work_link_only(
                        prepared,
                        qid=qid,
                        reason="same_item without shared identifier",
                    )
            return _skip_uncertain_duplicate(
                prepared, reason="same_item without shared identifier",
            )
        prepared.method = f"{prepared.method}+confirmed_same"
        return prepared
    if verdict == DIFFERENT_ITEM:
        if is_work and not is_test:
            from converter.wikidata.work_identity import work_identity_for_item  # noqa: PLC0415

            if work_identity_for_item(prepared.item).source_scoped:
                prepared.blocked = True
                prepared.block_status = "blocked"
                prepared.block_message = (
                    "Source-scoped work has no conclusive duplicate absence check; "
                    "refusing CREATE until source review."
                )
                prepared.method = "work_identity_review"
                return prepared
        prepared = _clear_qid_for_create(prepared, suffix="confirmed_different")
        return _skip_if_person_unpublishable(prepared)
    return _skip_uncertain_duplicate(prepared, reason=f"verdict={verdict}")


def rewrite_author_link_to_name_string(stmt: Any, name: str) -> Any:
    """Turn a deferred P50 `__LOCAL:` author into a P2093 name string."""
    from converter.wikidata.item_models import WikidataStatement  # noqa: PLC0415
    from converter.wikidata.property_mapping import P_AUTHOR_NAME_STRING  # noqa: PLC0415

    if isinstance(stmt, WikidataStatement):
        return replace(
            stmt,
            property_id=P_AUTHOR_NAME_STRING,
            value=name,
            value_type="string",
        )
    try:
        return replace(
            stmt,
            property_id=P_AUTHOR_NAME_STRING,
            value=name,
            value_type="string",
        )
    except TypeError:
        return WikidataStatement(
            property_id=P_AUTHOR_NAME_STRING,
            value=name,
            value_type="string",
        )


def _rollup_skipped_person_refs(prepared: list[PreparedItem]) -> None:
    skipped = {
        p.local_id: (p.label or "").strip()
        for p in prepared
        if str(p.entity_type or "").lower() == "person"
        and p.block_status == "skipped"
        and not p.blocked
        and p.local_id
    }
    if not skipped:
        return
    for p in prepared:
        if p.local_id in skipped:
            continue
        item = p.item
        stmts = list(getattr(item, "statements", None) or [])
        if not stmts:
            continue
        kept: list[Any] = []
        changed = False
        for stmt in stmts:
            pid = str(
                getattr(stmt, "property_id", "")
                or getattr(stmt, "property", "")
                or ""
            )
            value = str(getattr(stmt, "value", "") or "")
            if pid != "P50" or not value.startswith("__LOCAL:"):
                kept.append(stmt)
                continue
            if (getattr(stmt, "qualifiers", None) or getattr(stmt, "references", None)
                    or getattr(stmt, "reference_blocks", None)):
                kept.append(stmt)
                continue
            local_id = value.removeprefix("__LOCAL:")
            name = skipped.get(local_id, "").strip()
            if not name:
                kept.append(stmt)
                continue
            kept.append(rewrite_author_link_to_name_string(stmt, name))
            changed = True
        if changed:
            item.statements = kept


def _apply_work_identity_gate(
    prepared: PreparedItem, *, qid: str, is_test: bool = False,
) -> PreparedItem:
    """Check work identity before adoption or link-only handling (W-196)."""
    from app.pipeline.wikidata_existence import fetch_entity_p31  # noqa: PLC0415
    from app.pipeline.wikidata_live_native_hygiene import (  # noqa: PLC0415
        work_qid_refuses_update,
    )
    from converter.wikidata.work_identity import (  # noqa: PLC0415
        compare_work_identity,
        work_identity_for_item,
        work_identity_from_metadata,
    )

    try:
        if is_test:
            p31 = fetch_entity_p31([qid], is_test=True).get(qid)
        else:
            p31 = fetch_entity_p31([qid], is_test=False, require_complete=True).get(qid)
    except Exception as exc:  # noqa: BLE001 — live identity must fail closed
        logger.warning("Could not read work P31 for %s: %s", qid, exc)
        p31 = None
    if p31 is None:
        if not is_test:
            prepared.blocked = True
            prepared.block_status = "blocked"
            prepared.block_message = (
                f"Work {qid} has unverified P31 metadata; refusing adoption, "
                "UPDATE or CREATE until the entity can be checked."
            )
            prepared.method = "work_identity_review"
            return prepared
        p31 = []
    proposed = work_identity_for_item(prepared.item)
    strong = prepared.method in {"prebuilt", "ledger"} or "identifier" in prepared.method
    has_author_evidence = bool(
        proposed.author_qids or proposed.author_names or proposed.unresolved_authors
    )
    if not is_test and (has_author_evidence or proposed.source_scoped):
        from app.pipeline.wikidata_existence import fetch_entity_duplicate_metadata  # noqa: PLC0415

        compatible = False
        try:
            metadata = fetch_entity_duplicate_metadata([qid], is_test=False).get(qid)
            if metadata:
                existing = work_identity_from_metadata(metadata)
                compatible = compare_work_identity(
                    proposed, existing, strong_identifier=strong,
                ) == "match"
            else:
                logger.warning("Work identity metadata unavailable for %s", qid)
        except Exception as exc:  # noqa: BLE001 — identity must fail closed
            logger.warning("Could not read work identity metadata for %s: %s", qid, exc)
        if not compatible:
            prepared.blocked = True
            prepared.block_status = "blocked"
            prepared.block_message = (
                f"Work {qid} has conflicting or unverified author/source evidence; "
                "refusing adoption, UPDATE or CREATE until source review."
            )
            prepared.method = "work_identity_review"
            return prepared
    if not work_qid_refuses_update(qid, p31):
        return prepared
    return _skip_work_link_only(
        prepared,
        qid=qid,
        reason="ritual/concept or forbidden work QID",
    )


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
    if str(prepared.entity_type or "").lower() == "work":
        prepared = _apply_work_identity_gate(
            prepared, qid=clean_qid, is_test=is_test,
        )
        if prepared.blocked or not prepared.existing_qid:
            return prepared

    if _match_needs_duplicate_confirm(prepared):
        if (
            str(prepared.method or "").lower() == "ledger"
            and ownership_checker is not None
            and _classify_upload_ownership(ownership_checker, clean_qid) == "own"
        ):
            prepared.ownership = "own"
            prepared.method = "ledger+owned_identifier"
            return prepared
        prepared = _apply_uncertain_duplicate_confirm(prepared, is_test=is_test)
        if (
            prepared.blocked
            or prepared.block_status == "skipped"
            or not prepared.existing_qid
        ):
            return prepared

    ownership = (
        _classify_upload_ownership(ownership_checker, clean_qid)
        if ownership_checker is not None else "unknown"
    )
    if ownership == "own":
        prepared.ownership = "own"
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

    prepared.ownership = ownership

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


_RECONCILER: Any = None


def _make_reconciler() -> Any:
    """Shared reconciler (Rule W-280): one instance per process so the
    pre-warmed identifier cache survives the per-item upload calls."""
    global _RECONCILER
    if _RECONCILER is None:
        from converter.wikidata.reconciler import WikidataReconciler  # noqa: PLC0415

        _RECONCILER = WikidataReconciler()
    return _RECONCILER


def _reconcile_for_upload(
    reconciler: Any, item: Any, entity_type: str,
    *, check_work_identity: bool = True,
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
        from converter.wikidata.reconciler import WorkIdentityUncertainError  # noqa: PLC0415
        from converter.wikidata.work_identity import work_identity_for_item  # noqa: PLC0415

        identity = work_identity_for_item(item)
        check_candidate = getattr(reconciler, "work_candidate_matches", None)
        p2888 = _find_statement_value(item, "P2888")
        reconcile_p2888 = getattr(reconciler, "reconcile_by_exact_match_uri", None)
        if p2888 and callable(reconcile_p2888):
            qid_p2888 = reconcile_p2888(str(p2888))
            if qid_p2888:
                if check_work_identity and callable(check_candidate) and not check_candidate(
                    qid_p2888, item, strong_identifier=True,
                ):
                    raise WorkIdentityUncertainError(
                        f"P2888 candidate {qid_p2888} conflicts with source work evidence."
                    )
                return (qid_p2888, "p2888_identifier")
        if identity.source_scoped:
            return (None, "source_scoped_work")
        labels = getattr(item, "labels", {}) or {}
        title = labels.get("he") or labels.get("en") or ""
        p50 = _find_statement_value(item, "P50")
        p2093 = _find_statement_value(item, "P2093")
        if title:
            has_hebrew = any("\u0590" <= c <= "\u05ff" for c in str(title))
            reconcile_item = getattr(reconciler, "reconcile_work_item", None)
            if callable(reconcile_item):
                qid = reconcile_item(item)
            elif p2093:
                qid = reconciler.reconcile_work_by_label_and_author(
                    str(title), lang="he" if has_hebrew else "en",
                    author_qid=str(p50) if p50 else None, author_name=str(p2093),
                )
            else:
                qid = reconciler.reconcile_work_by_label_and_author(
                    str(title),
                    lang="he" if has_hebrew else "en",
                    author_qid=str(p50) if p50 else None,
                )
            if qid:
                return (qid, "label+author")
            if identity.author_qids or identity.author_names or identity.unresolved_authors:
                return (None, "none")
            from app.pipeline.wikidata_duplicate_probe import (  # noqa: PLC0415
                probe_work_title_allowlisted,
            )

            probed = probe_work_title_allowlisted(str(title))
            if probed:
                return (probed, "title+p31")
            return (None, "none")
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
    marc_records: list[dict[str, Any]] | None = None,
    quality_preflight: UploadCorpusQualityPreflight | None = None,
) -> list[PreparedItem]:
    from app.pipeline.wikidata_live_native_hygiene import (  # noqa: PLC0415
        coerce_monolingual_text_statements,
        omit_implausible_codex_dimensions,
    )
    from converter.wikidata.reconciler import (  # noqa: PLC0415
        ReconciliationUnavailableError,
        WorkIdentityUncertainError,
    )

    coerce_monolingual_text_statements(items)
    omit_implausible_codex_dimensions(items)

    skipped_person_indexes: set[int] = set()
    for index, item in enumerate(items):
        if str(getattr(item, "entity_type", "") or "").lower() != "person":
            continue
        from app.pipeline.wikidata_canonical_enrichment import (  # noqa: PLC0415
            is_publishable_person_item,
            recover_person_identifiers_from_evidence,
        )

        recover_person_identifiers_from_evidence(item)
        if not is_publishable_person_item(item):
            skipped_person_indexes.add(index)

    quality_items = [
        item for index, item in enumerate(items)
        if index not in skipped_person_indexes
    ]
    from app.pipeline.wikidata_export_quality_gate import (  # noqa: PLC0415
        FindingSeverity,
        wikidata_export_quality_findings,
    )

    collision_findings = [] if quality_preflight is not None else [
        finding.render()
        for finding in wikidata_export_quality_findings(quality_items)
        if finding.severity is FindingSeverity.HARD
        and finding.code == "STRONG_EXTERNAL_ID_COLLISION"
    ]
    if collision_findings:
        from app.pipeline.wikidata_canonical_enrichment import (  # noqa: PLC0415
            _UPLOAD_SKIP_MESSAGE,
        )

        sample = "; ".join(collision_findings[:3])
        suffix = (
            f" (+{len(collision_findings) - 3} more)"
            if len(collision_findings) > 3 else ""
        )
        rows: list[PreparedItem] = []
        for index, item in enumerate(items):
            local_id = _local_id(item, index)
            entity_type = getattr(item, "entity_type", "") or "other"
            existing_qid = getattr(item, "existing_qid", None)
            from app.pipeline.wikidata_person_identity_choice import reference_choice  # noqa: PLC0415

            if is_test and reference_choice(item):
                existing_qid = None
            if index in skipped_person_indexes:
                rows.append(PreparedItem(
                    item=item,
                    local_id=local_id,
                    label=_label(item),
                    entity_type=entity_type,
                    existing_qid=existing_qid,
                    method="prebuilt" if existing_qid else "none",
                    blocked=False,
                    block_status="skipped",
                    block_message=_UPLOAD_SKIP_MESSAGE,
                    had_builder_qid=bool(existing_qid),
                ))
                continue
            rows.append(PreparedItem(
                item=item,
                local_id=local_id,
                label=_label(item),
                entity_type=entity_type,
                existing_qid=existing_qid,
                method="quality_gate",
                blocked=True,
                block_status="blocked",
                block_message=(
                    "Blocked by the full Wikidata quality gate before "
                    f"reconciliation: {sample}{suffix}"
                ),
                had_builder_qid=bool(existing_qid),
            ))
        return rows

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

        from app.pipeline.wikidata_person_identity_choice import reference_choice  # noqa: PLC0415

        if reference_choice(item):
            prepared.append(PreparedItem(
                item=item, local_id=local_id, label=label, entity_type=et,
                existing_qid=None if is_test else existing, method="source_identity_choice",
                blocked=False, block_status="skipped",
                block_message="Source-scoped person identity is reference only; no person write.",
                had_builder_qid=had_builder_qid,
            ))
            continue

        if quality_preflight is not None:
            decision = quality_preflight.decisions.get(local_id)
            preflight_findings = quality_preflight.execution_findings
            if decision is None and not preflight_findings:
                preflight_findings = (
                    f"CORPUS_PREFLIGHT_MISSING {local_id}: the item was not "
                    "present in the checked corpus",
                )
            elif decision is not None and not decision.eligible:
                preflight_findings = decision.findings
            if preflight_findings:
                codes = ", ".join(sorted({
                    finding.split(" ", 1)[0]
                    for finding in preflight_findings
                }))
                prepared.append(PreparedItem(
                    item=item,
                    local_id=local_id,
                    label=label,
                    entity_type=et,
                    existing_qid=existing,
                    method="corpus_quality_preflight",
                    blocked=True,
                    block_status="blocked",
                    block_message=(
                        f"Blocked by the corpus quality preflight (ERROR: {codes}). "
                        + "; ".join(preflight_findings[:3])
                    ),
                    had_builder_qid=had_builder_qid,
                ))
                continue

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
                existing, method = _reconcile_for_upload(
                    reconciler, item, et, check_work_identity=not enforce_ownership,
                )
            except WorkIdentityUncertainError as exc:
                prepared.append(PreparedItem(
                    item=item, local_id=local_id, label=label, entity_type=et,
                    existing_qid=None, method="work_identity_review", blocked=True,
                    block_status="blocked", block_message=str(exc),
                    had_builder_qid=had_builder_qid,
                ))
                continue
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

        if et == "work" and not existing and not is_test:
            from converter.wikidata.work_identity import work_identity_for_item  # noqa: PLC0415

            if work_identity_for_item(item).source_scoped:
                prepared.append(PreparedItem(
                    item=item, local_id=local_id, label=label, entity_type=et,
                    existing_qid=None, method="work_identity_review", blocked=True,
                    block_status="blocked",
                    block_message=(
                        "Source-scoped work has no conclusive duplicate absence check; "
                        "refusing CREATE until source review."
                    ),
                    had_builder_qid=had_builder_qid,
                ))
                continue

        row = PreparedItem(
            item=item, local_id=local_id, label=label, entity_type=et,
            existing_qid=existing, method=method, blocked=False,
            block_status="", block_message="",
            had_builder_qid=had_builder_qid,
            adopt_candidate=adopt_candidate,
            ownership="absent" if not existing else "unknown",
        )
        check_candidate = getattr(reconciler, "work_candidate_matches", None)
        if et == "work" and existing and not is_test and not enforce_ownership and callable(check_candidate):
            try:
                compatible = check_candidate(
                    existing, item,
                    strong_identifier=method in {"prebuilt", "ledger"} or "identifier" in method,
                )
            except ReconciliationUnavailableError as exc:
                compatible = False
                row.block_message = str(exc)
            if not compatible:
                row.blocked = True
                row.block_status = "blocked"
                row.block_message = row.block_message or (
                    f"Work {existing} has conflicting or unverified author/source evidence."
                )
                row.method = "work_identity_review"
        prepared.append(row)
    _rollup_skipped_person_refs(prepared)

    if quality_preflight is None:
        active_items = [
            row.item
            for row in prepared
            if row.block_status != "skipped"
        ]
        findings = wikidata_export_quality_findings(
            active_items,
            marc_records=marc_records,
        )
        hard_findings = [
            finding
            for finding in findings
            if finding.severity is FindingSeverity.HARD
        ]
        for finding in findings:
            if finding.severity is FindingSeverity.INFORMATIONAL:
                logger.warning(
                    "wikidata upload quality [informational] %s",
                    finding.render(),
                )

        for row in prepared:
            if row.block_status == "skipped":
                continue
            row_findings = [
                finding
                for finding in hard_findings
                if not finding.entity_ids or row.local_id in finding.entity_ids
            ]
            if row_findings and not row.blocked:
                codes = ", ".join(sorted({finding.code for finding in row_findings}))
                rendered = [finding.render() for finding in row_findings]
                row.blocked = True
                row.block_status = "blocked"
                row.block_message = (
                    f"Blocked by the full quality gate (ERROR: {codes}). Fix via item "
                    "overrides and rebuild before uploading — error-severity "
                    "items are never created or updated. "
                    + "; ".join(rendered[:3])
                )

    for index, row in enumerate(prepared):
        if row.block_status == "skipped":
            continue
        if enforce_ownership:
            prepared[index] = _apply_existence_and_ownership(
                row,
                accept=accepts.get(row.local_id),
                ownership_checker=ownership_checker,
                is_test=is_test,
                existence_cache=existence_cache,
            )
    return prepared


def prepare_items_for_upload(
    items: list[Any],
    reconciler: Any,
    **options: Any,
) -> list[PreparedItem]:
    """Apply all upload preflight gates through one public seam."""
    return _prepare_for_upload(items, reconciler, **options)


async def reconcile_items(items: list[Any]) -> list[ReconcileOutcome]:
    return await run_in_threadpool(_reconcile_sync, items)


def _reconcile_sync(items: list[Any]) -> list[ReconcileOutcome]:
    from converter.wikidata.reconciler import (  # noqa: PLC0415
        ReconciliationUnavailableError,
    )

    reconciler = _make_reconciler()
    from converter.wikidata.reconciler import prewarm_reconciler_for_items  # noqa: PLC0415

    prewarm_reconciler_for_items(reconciler, items)
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
                    else "No conclusive duplicate check for source-scoped work"
                    if method == "source_scoped_work"
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
    from app.pipeline.wikidata_person_identity_choice import reference_choice  # noqa: PLC0415

    if record_ledger and outcome.existing_qid and not reference_choice(item):
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
        from app.pipeline.wikidata_person_identity_choice import reference_choice  # noqa: PLC0415

        if reference_choice(item):
            out.append(ReconcileOutcome(
                local_id=local_id, label=label, entity_type=et,
                existing_qid=None if ns == "wikidata-test" else item.existing_qid, method="source_identity_choice",
                message="The source-scoped person identity is reference only.",
            ))
            continue
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
                    else "No conclusive duplicate check for source-scoped work"
                    if method == "source_scoped_work"
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


async def _persisted_foreign_accept(run_id: uuid.UUID, local_id: str, qid: str) -> bool:
    from sqlalchemy import select  # noqa: PLC0415

    from app.db import session_scope  # noqa: PLC0415
    from app.models.item_override import WikidataItemOverride  # noqa: PLC0415

    async with session_scope() as db:
        result = await db.execute(
            select(WikidataItemOverride.id).where(
                WikidataItemOverride.run_id == run_id,
                WikidataItemOverride.local_id == local_id,
                WikidataItemOverride.accept_foreign_modify.is_(True),
                WikidataItemOverride.accepted_foreign_qid == qid,
            )
        )
        return result.scalar_one_or_none() is not None



async def persist_upload_outcomes(
    db: AsyncSession,
    items: list[Any],
    outcomes: list[UploadOutcome],
    *,
    audit_ctx: WikibaseAuditContext | None,
    is_test: bool = False,
    record_ledger: bool = True,
    strict_audit: bool = False,
) -> None:
    """Persist audit and ledger rows after network writes complete (Rule W-40)."""
    if audit_ctx is None:
        return
    ns = ledger_namespace(is_test=is_test)
    items_by_id = {_local_id(it, i): it for i, it in enumerate(items)}
    for outcome in outcomes:
        if strict_audit:
            await _record_outcome_audit(db, audit_ctx, outcome, strict=True)
        else:
            await _record_outcome_audit(db, audit_ctx, outcome)
        if (
            record_ledger
            and outcome.qid
            and str(outcome.qid).strip().startswith("Q")
            and outcome.status in ("created", "adopted", "updated", "exists", "skipped")
        ):
            item = items_by_id.get(outcome.local_id)
            from app.pipeline.wikidata_person_identity_choice import reference_choice  # noqa: PLC0415

            if item is not None and not reference_choice(item):
                await record_ledger_mapping(
                    db,
                    ledger_key_for_item(item, ns),
                    outcome.qid,
                    local_key=outcome.local_id,
                    label=outcome.label,
                )


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
    quality_preflight: UploadCorpusQualityPreflight | None = None,
    owner_targets: Mapping[str, str] | None = None,
) -> list[UploadOutcome]:
    resolved = mode or resolve_upload_mode(upload_target, dry_run=dry_run)
    if ledger is None and db is not None:
        ledger = await load_ledger_for_prepare(db, is_test=resolved.is_test)
    if accept_by_local_id is None and db is not None and run_id is not None:
        accept_by_local_id = await load_foreign_accept_map(db, run_id)
    approval_run_id = run_id or (audit_ctx.run_id if audit_ctx is not None else None)
    loop = asyncio.get_running_loop()

    def check_foreign_accept(local_id: str, qid: str) -> bool:
        if approval_run_id is None:
            return False
        future = asyncio.run_coroutine_threadsafe(
            _persisted_foreign_accept(approval_run_id, local_id, qid), loop,
        )
        try:
            return future.result(timeout=15)
        except Exception as exc:  # noqa: BLE001 — no approval on DB failure
            future.cancel()
            logger.warning("Could not verify foreign approval for %s (%s): %s", local_id, qid, exc)
            return False

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
        quality_preflight,
        check_foreign_accept,
        owner_targets,
    )
    if db is not None and audit_ctx is not None and not resolved.dry_run:
        try:
            await persist_upload_outcomes(
                db, items, outcomes, audit_ctx=audit_ctx, is_test=resolved.is_test,
                record_ledger=owner_targets is None, strict_audit=owner_targets is not None,
            )
        except Exception:  # noqa: BLE001 — retain the actual remote outcome
            if owner_targets is None:
                raise
            logger.exception("Owner-only audit persistence failed")
            for outcome in outcomes:
                outcome.audit_status = "failed"
                outcome.message += " Audit persistence failed; verify the remote outcome before retry."
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
    quality_preflight: UploadCorpusQualityPreflight | None = None,
    check_foreign_accept: Callable[[str, str], bool] | None = None,
    owner_targets: Mapping[str, str] | None = None,
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

    if owner_targets is not None:
        return _owner_only_upload_sync(
            items, owner_targets, ownership_checker, dry_run, is_test,
            session_qids, quality_preflight,
        )

    if dry_run:
        return _dry_run(
            items, ledger, ledger_ns,
            accept_by_local_id=accepts,
            ownership_checker=ownership_checker,
            is_test=is_test,
            existence_cache=existence_cache,
            quality_preflight=quality_preflight,
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
        quality_preflight=quality_preflight,
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
                qid=p.link_qid or p.existing_qid, status="skipped",
                message=p.block_message, added_properties=[],
                ownership=p.ownership,
            ))
            continue
        if p.blocked:
            out.append(UploadOutcome(
                local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                qid=p.existing_qid, status=p.block_status or "blocked",
                message=p.block_message, added_properties=[],
                ownership=p.ownership,
            ))
            continue
        if str(getattr(p.item, "existing_qid", "") or "").strip() != str(
            p.existing_qid or ""
        ).strip():
            out.append(UploadOutcome(
                local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                qid=p.existing_qid, status="blocked",
                message="Refusing write: the target QID changed after authorization.",
                added_properties=[], ownership=p.ownership,
            ))
            continue
        try:
            if p.allow_foreign_modify and p.existing_qid and not is_test:
                accept = accepts.get(p.local_id) or ForeignAccept()
                if (
                    not accept.accept_foreign_modify
                    or str(accept.accepted_foreign_qid or "").strip() != p.existing_qid
                    or str(getattr(p.item, "existing_qid", "") or "").strip() != p.existing_qid
                    or check_foreign_accept is None
                    or not check_foreign_accept(p.local_id, p.existing_qid)
                ):
                    out.append(UploadOutcome(
                        local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                        qid=p.existing_qid, status="blocked",
                        message=(
                            "Foreign modification requires current approval "
                            "for the exact target QID."
                        ),
                        added_properties=[], ownership="foreign",
                    ))
                    continue
                write_uploader.register_foreign_accept(p.existing_qid)
            result = write_uploader.upload_item(
                p.item, created_qids=session_qids,
            )
            status = result.status
            if not p.existing_qid and result.qid and status in {"updated", "exists"}:
                p.ownership = _classify_upload_ownership(write_uploader, str(result.qid))
                if p.ownership == "own" and status == "updated":
                    status = "adopted"
            if p.adopt_candidate and status == "updated":
                status = "adopted"
            elif status == "success":
                status = "updated" if p.existing_qid else "created"
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
                    ownership=p.ownership,
                    accepted_foreign_qid=(
                        p.existing_qid
                        if p.allow_foreign_modify and result.qid == p.existing_qid
                        else None
                    ),
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
                    ownership="foreign",
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("upload failed for %r", p.label)
            out.append(
                UploadOutcome(
                    local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                    qid=p.existing_qid,
                    status="failed", message=str(exc), added_properties=[],
                    ownership=p.ownership,
                ),
            )
            if _is_auth_failure_message(str(exc)):
                # Stop the batch immediately — further logins will rate-limit
                # (Rule W-179). Callers that share an uploader should abort the job.
                break
    return out


def _owner_only_upload_sync(
    items: list[Any], targets: Mapping[str, str], uploader: Any, dry_run: bool,
    is_test: bool, session_qids: dict[str, str],
    quality_preflight: UploadCorpusQualityPreflight | None,
) -> list[UploadOutcome]:
    out: list[UploadOutcome] = []
    from converter.wikidata.uploader import (  # noqa: PLC0415
        ClaimBuildError, owner_only_control_number, owner_only_has_generic_text,
        owner_only_has_local_references, owner_only_validate_quantity_units,
    )

    for index, item in enumerate(items):
        local_id = _local_id(item, index)
        qid = targets.get(local_id)
        original_qid = getattr(item, "existing_qid", None)
        def blocked(message: str) -> UploadOutcome:
            return UploadOutcome(local_id=local_id, label=_label(item),
                                 entity_type=getattr(item, "entity_type", "") or "other",
                                 qid=qid, status="blocked", message=message, added_properties=[])
        try:
            owner_only_validate_quantity_units(item)
        except ClaimBuildError as exc:
            out.append(blocked(str(exc)))
            continue
        if owner_only_has_local_references(item):
            out.append(blocked("Owner-only: local references require a complete independently verified item."))
            continue
        if owner_only_has_generic_text(item):
            out.append(blocked("Owner-only: generic text links may hide unresolved source evidence."))
            continue
        control_number = owner_only_control_number(item)
        if control_number is None:
            out.append(blocked("Owner-only: local P3959 does not match exactly one source record."))
            continue
        if (is_test or not qid or not re.fullmatch(r"Q[1-9][0-9]*", qid)
                or (original_qid and original_qid != qid) or uploader is None):
            out.append(blocked("Owner-only: no consistent selected target or live credential."))
            continue
        if not uploader.owner_only_target_valid(qid, control_number):
            out.append(blocked("Owner-only: target, P3959, or creator could not be verified."))
            continue
        candidate = replace(item, existing_qid=qid)
        uploader._foreign_accept_qids.clear()
        rows = _prepare_for_upload(
            [candidate], _make_reconciler(), ledger={}, accept_by_local_id={},
            ownership_checker=uploader, is_test=False, enforce_ownership=True,
            quality_preflight=quality_preflight,
        )
        row = rows[0]
        if (row.blocked or row.block_status == "skipped" or row.ownership != "own"
                or row.existing_qid != qid or getattr(row.item, "existing_qid", None) != qid
                or owner_only_control_number(row.item) != control_number):
            out.append(blocked(row.block_message or "Owner-only: target changed or ownership is unverified."))
            continue
        if dry_run:
            try:
                preview, _, _ = uploader._build_wbi_item(row.item, owner_only=True)
                revision = uploader._owner_only_revision(qid, control_number)
                if (getattr(preview, "id", None) != qid or type(revision) is not int
                        or revision <= 0 or getattr(preview, "lastrevid", None) != revision
                        or owner_only_control_number(row.item) != control_number):
                    out.append(blocked("Owner-only: target, identity, or revision changed during preview."))
                    continue
            except Exception as exc:  # noqa: BLE001 — preview must never predict an unsafe write
                logger.warning("Owner-only preview blocked for %s: %s", local_id, exc)
                out.append(blocked(f"Owner-only: write preparation failed: {exc}"))
                continue
            out.append(UploadOutcome(local_id, row.label, row.entity_type, qid,
                                     "would_update", f"Dry-run: eligible to attempt update of {qid}.",
                                     [], ownership="own"))
            continue
        result = uploader.upload_item(row.item, created_qids=session_qids, owner_only_qid=qid)
        status = result.status
        if status in {"success", "created"}:
            out.append(blocked("Owner-only: unexpected create outcome; verify the remote state."))
            continue
        if result.qid != qid and status in {"success", "created", "updated", "exists", "adopted"}:
            out.append(blocked("Owner-only: uploader returned a different target."))
            continue
        out.append(UploadOutcome(local_id, row.label, row.entity_type, result.qid, status,
                                 result.message, list(result.added_properties or []), ownership="own"))
    return out


_SESSION_QID_STATUSES = frozenset({
    "created", "updated", "adopted", "exists", "success",
    "would_create", "would_update", "would_adopt",
})


def remember_created_qid(
    session_qids: dict[str, str],
    local_id: str,
    qid: str | None,
    status: str,
    *,
    dry_run: bool = False,
    entity_type: str = "",
) -> None:
    """Record a written (or dry-run) QID so later ``__LOCAL:`` snaks can resolve."""
    if not local_id:
        return
    if (
        status == "skipped"
        and qid
        and str(entity_type or "").lower() == "work"
    ):
        session_qids[local_id] = str(qid)
        return
    if status not in _SESSION_QID_STATUSES:
        return
    if qid:
        session_qids[local_id] = str(qid)
    elif dry_run:
        session_qids[local_id] = f"dry:{local_id}"


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
    quality_preflight: UploadCorpusQualityPreflight | None = None,
) -> list[UploadOutcome]:
    reconciler = _make_reconciler()
    prepared = _prepare_for_upload(
        items, reconciler, ledger=ledger, ledger_ns=ledger_ns,
        accept_by_local_id=accept_by_local_id or {},
        ownership_checker=ownership_checker,
        is_test=is_test,
        enforce_ownership=True,
        existence_cache=existence_cache,
        quality_preflight=quality_preflight,
    )

    out: list[UploadOutcome] = []
    for p in prepared:
        stmts = getattr(p.item, "statements", []) or []
        if p.block_status == "skipped" and not p.blocked:
            out.append(UploadOutcome(
                local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                qid=p.link_qid or p.existing_qid, status="skipped",
                message=p.block_message, added_properties=[],
                ownership=p.ownership,
            ))
            continue
        if p.blocked:
            out.append(UploadOutcome(
                local_id=p.local_id, label=p.label, entity_type=p.entity_type,
                qid=p.existing_qid, status=p.block_status or "blocked",
                message=f"Dry-run: would be {p.block_status or 'BLOCKED'} — {p.block_message}",
                added_properties=[],
                ownership=p.ownership,
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
                ownership=p.ownership,
                accepted_foreign_qid=p.existing_qid if p.allow_foreign_modify else None,
            ),
        )
    return out


async def _record_outcome_audit(
    db: AsyncSession,
    ctx: WikibaseAuditContext,
    outcome: UploadOutcome,
    *, strict: bool = False,
) -> None:
    operation = _audit_operation_for_status(outcome.status)
    if operation is None:
        return
    options = {"raise_on_error": True} if strict else {}
    await record_wikibase_write(
        db, ctx,
        operation=operation,
        target_kind=TARGET_ITEM,
        target_key=outcome.local_id,
        wikibase_id=outcome.qid,
        outcome_message=outcome.message or "ok",
        **options,
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
