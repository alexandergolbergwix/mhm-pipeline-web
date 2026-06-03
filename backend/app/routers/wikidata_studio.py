"""Wikidata Studio router — builds items + QuickStatements for a run.

The endpoint reads the run's MARC records and approved authority
matches, drives the *real* desktop ``WikidataItemBuilder`` +
``QuickStatementsExporter`` in a threadpool, and returns the structured
items (every label / description / claim / qualifier / reference) plus
the QuickStatements TSV blob ready for download.

Only the **approved** matches feed the builder — this is the curator
workflow's unit of truth (see Rule 54 in the desktop CLAUDE.md).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.event import (
    ENTITY_TYPE_WIKIDATA_OVERRIDE,
    OP_CREATE,
    OP_PATCH,
    ProjectEvent,
)
from app.models.extraction_approval import ExtractionApproval
from app.models.item_override import WikidataItemOverride
from app.models.run import AuthorityMatch, Run, RunRecord
from app.pipeline import wikidata_studio, wikidata_upload
from app.routers.runs import _lookup_run_with_access  # noqa: PLF401 — module-internal
from app.versioning import apply_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["wikidata-studio"])


class StudioSummary(BaseModel):
    total_items: int
    manuscripts: int
    persons: int
    works: int
    statements: int


class StudioBuildResponse(BaseModel):
    items: list[dict[str, Any]]
    quickstatements: str
    summary: StudioSummary
    approved_match_count: int     # how many of the feed are approved
    pending_match_count: int      # how many are still pending review
    used_match_count: int         # what we actually fed the builder
    approved_only: bool           # which mode was used
    record_count: int


@router.get("/{run_id}/wikidata-studio", response_model=StudioBuildResponse)
async def build_studio(
    run_id: uuid.UUID,
    approved_only: bool = Query(
        default=True,
        description="When true (default), only approved authority matches "
                    "and NER entities feed the item builder, matching the "
                    "'ship this in the final output' semantics of the "
                    "approval stores. Pass false to preview all candidates.",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StudioBuildResponse:
    await _lookup_run_with_access(db, run_id, auth)

    # Pull every record + every match (filtered to approved on request).
    records = (
        await db.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )
    ).scalars().all()
    all_matches = (
        await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )
    ).scalars().all()
    approved_count = sum(1 for m in all_matches if m.approved)
    pending_count = len(all_matches) - approved_count
    matches = [m for m in all_matches if m.approved] if approved_only else all_matches

    marc_records = [dict(r.marc) for r in records]
    approved_matches = [
        {
            "id": str(m.id),
            "control_number": m.control_number,
            "entity_text": m.entity_text,
            "role": m.role,
            "matched_name": m.matched_name,
            "mazal_id": m.mazal_id,
            "viaf_id": m.viaf_id,
            "wikidata_qid": m.wikidata_qid,
            "confidence": m.confidence,
            "source": m.source,
            "payload": m.payload or {},
        }
        for m in matches
    ]

    overrides = await _load_overrides(db, run_id)
    entities_by_cn = await _load_entities_by_cn(db, run_id, approved_only)
    result = await wikidata_studio.build_items_for_run(
        marc_records=marc_records, approved_matches=approved_matches,
        entities_by_cn=entities_by_cn,
        overrides=overrides, return_native=True,
    )
    # Stamp local_id onto each serialised item so the frontend can key
    # edits against persisted overrides across rebuilds.
    if result.get("native_items"):
        for it_dict, it_native in zip(
            result["items"], result["native_items"], strict=True,
        ):
            it_dict["local_id"] = wikidata_studio.local_id_for_item(it_native)

    return StudioBuildResponse(
        items=result["items"],
        quickstatements=result["quickstatements"],
        summary=StudioSummary(**result["summary"]),
        approved_match_count=approved_count,
        pending_match_count=pending_count,
        used_match_count=len(approved_matches),
        approved_only=approved_only,
        record_count=len(marc_records),
    )


@router.get("/{run_id}/wikidata-studio/quickstatements.txt", response_class=PlainTextResponse)
async def download_quickstatements(
    run_id: uuid.UUID,
    approved_only: bool = Query(default=True),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """Plain-text QuickStatements TSV — paste into
    https://quickstatements.toolforge.org."""
    studio = await build_studio(run_id, approved_only, auth, db)  # type: ignore[arg-type]
    suffix = "approved" if approved_only else "all"
    return PlainTextResponse(
        studio.quickstatements,
        headers={
            "Content-Disposition": (
                f'attachment; filename="run-{run_id}-{suffix}-quickstatements.txt"'
            ),
        },
    )


# ── Reconcile (SPARQL against Wikidata, no writes) ──────────────────────


class ReconcileOutcomeDto(BaseModel):
    local_id: str
    label: str
    entity_type: str
    existing_qid: str | None
    method: str
    message: str


class ReconcileResponse(BaseModel):
    reconciled: int
    matched: int
    outcomes: list[ReconcileOutcomeDto]


@router.post(
    "/{run_id}/wikidata-studio/reconcile", response_model=ReconcileResponse,
)
async def reconcile_against_wikidata(
    run_id: uuid.UUID,
    approved_only: bool = Query(default=True),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ReconcileResponse:
    """For every built item, SPARQL-query Wikidata to find an existing
    QID via VIAF / NLI / cluster IDs. Updates the in-memory items so the
    next download / upload knows to UPDATE instead of CREATE. **Never**
    writes to Wikidata."""
    native = await _build_native_items(db, run_id, auth, approved_only=approved_only)
    outcomes = await wikidata_upload.reconcile_items(native)
    return ReconcileResponse(
        reconciled=len(outcomes),
        matched=sum(1 for o in outcomes if o.existing_qid),
        outcomes=[ReconcileOutcomeDto(**o.__dict__) for o in outcomes],
    )


# ── Upload (dry-run or live, all 4 guards intact) ──────────────────────


class UploadOutcomeDto(BaseModel):
    local_id: str
    label: str
    entity_type: str
    qid: str | None
    status: str
    message: str
    added_properties: list[str]


class UploadResponse(BaseModel):
    dry_run: bool
    moratorium_lifted: bool
    test_mode: bool
    outcomes: list[UploadOutcomeDto]


@router.post("/{run_id}/wikidata-studio/upload", response_model=UploadResponse)
async def upload_to_wikidata(
    run_id: uuid.UUID,
    dry_run: bool = Query(
        default=True,
        description="Default True — describe what would happen without "
                    "writing. Set False for live; live also requires the "
                    "user to have a stored Wikidata token (Settings) AND "
                    "MORATORIUM_LIFTED=true in the env (or WIKIDATA_TEST_MODE=true).",
    ),
    approved_only: bool = Query(default=True),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> UploadResponse:
    """Live uploads ALWAYS use the user's own Wikidata token (stored
    encrypted via the Settings page) and route through the real
    ``WikidataUploader`` with all four modification guards intact."""
    import os  # noqa: PLC0415

    # Build the items first (with the latest approval state).
    native = await _build_native_items(db, run_id, auth, approved_only=approved_only)

    token: str | None = None
    if not dry_run:
        token = await _unwrap_user_secret(db, auth, "wikidata")

    if not dry_run and not token:
        raise_msg = (
            "Live upload requires a Wikidata token in Settings. "
            "Add one (User@Bot:hex or OAuth secret) and retry."
        )
        return UploadResponse(
            dry_run=False, moratorium_lifted=False, test_mode=False,
            outcomes=[UploadOutcomeDto(
                local_id="*", label="(token missing)", entity_type="",
                qid=None, status="failed", message=raise_msg, added_properties=[],
            )],
        )

    outcomes = await wikidata_upload.upload_items(
        native, token=token or "", dry_run=dry_run,
    )
    return UploadResponse(
        dry_run=dry_run,
        moratorium_lifted=os.environ.get("MORATORIUM_LIFTED", "").lower() == "true",
        test_mode=os.environ.get("WIKIDATA_TEST_MODE", "").lower() == "true",
        outcomes=[UploadOutcomeDto(**o.__dict__) for o in outcomes],
    )


# ── Per-item editing (curator overrides) ───────────────────────────────


class ItemOverridePayload(BaseModel):
    """Partial update — every field optional. The persisted row is
    merged with the existing override, so the UI can PATCH one tab
    at a time."""
    labels:            dict[str, str | None] | None = None
    descriptions:      dict[str, str | None] | None = None
    aliases:           dict[str, list[str] | None] | None = None
    add_statements:    list[dict[str, Any]] | None = None
    remove_statements: list[int] | None = None
    statement_edits:   dict[str, dict[str, Any]] | None = None


class ItemOverrideResponse(BaseModel):
    run_id: uuid.UUID
    local_id: str
    labels: dict[str, Any]
    descriptions: dict[str, Any]
    aliases: dict[str, Any]
    add_statements: list[dict[str, Any]]
    remove_statements: list[int]
    statement_edits: dict[str, Any]


@router.patch(
    "/{run_id}/wikidata-studio/items/{local_id:path}",
    response_model=ItemOverrideResponse,
)
async def patch_item_override(
    run_id: uuid.UUID, local_id: str,
    payload: ItemOverridePayload,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ItemOverrideResponse:
    """Persist a curator override for one Studio item.

    All fields optional. Sending ``labels: {"he": null}`` clears the
    Hebrew label override (reverts to whatever the builder produced).
    Statement edits use ``{"<index>": {"value": "Q5"}}`` — index is
    relative to the builder output AFTER removals are applied.
    """
    run = await _lookup_run_with_access(db, run_id, auth, write=True)

    row = (
        await db.execute(
            select(WikidataItemOverride).where(
                WikidataItemOverride.run_id == run_id,
                WikidataItemOverride.local_id == local_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = WikidataItemOverride(
            run_id=run_id, local_id=local_id, updated_by=auth.user.id,
        )
        db.add(row)
        # Flush so the python-side UUID default is materialised before
        # we use ``row.id`` as the versioning entity id.
        await db.flush()

    if payload.labels is not None:
        # Merge non-null, drop keys set to null. Force a new dict so
        # SQLAlchemy notices the change.
        new = dict(row.labels or {})
        for k, v in payload.labels.items():
            if v is None: new.pop(k, None)
            else:         new[k] = v
        row.labels = new
    if payload.descriptions is not None:
        new = dict(row.descriptions or {})
        for k, v in payload.descriptions.items():
            if v is None: new.pop(k, None)
            else:         new[k] = v
        row.descriptions = new
    if payload.aliases is not None:
        new = dict(row.aliases or {})
        for lang, vals in payload.aliases.items():
            if vals is None: new.pop(lang, None)
            else:            new[lang] = list(vals)
        row.aliases = new
    if payload.add_statements is not None:
        row.add_statements = list(payload.add_statements)
    if payload.remove_statements is not None:
        row.remove_statements = list(payload.remove_statements)
    if payload.statement_edits is not None:
        new_edits = dict(row.statement_edits or {})
        for k, v in payload.statement_edits.items():
            if v is None: new_edits.pop(k, None)
            else:         new_edits[k] = v
        row.statement_edits = new_edits

    row.updated_by = auth.user.id

    # Versioning event — audit the override edit on the same transaction
    # as the row write. Failure must NEVER 500 the request.
    entity_id_str = str(row.id)
    try:
        has_history = (
            await db.execute(
                select(ProjectEvent.id)
                .where(
                    ProjectEvent.entity_type == ENTITY_TYPE_WIKIDATA_OVERRIDE,
                    ProjectEvent.entity_id == entity_id_str,
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
        op_kind = OP_PATCH if has_history else OP_CREATE
        new_state = {
            "labels":            dict(row.labels or {}),
            "descriptions":      dict(row.descriptions or {}),
            "aliases":           dict(row.aliases or {}),
            "add_statements":    list(row.add_statements or []),
            "remove_statements": list(row.remove_statements or []),
            "statement_edits":   dict(row.statement_edits or {}),
        }
        await apply_event(
            db,
            project_id=run.project_id,
            entity_type=ENTITY_TYPE_WIKIDATA_OVERRIDE,
            entity_id=entity_id_str,
            op=op_kind,
            new_state=new_state,
            actor_id=auth.user.id,
            message=f"wikidata override edit ({local_id})",
        )
    except Exception as exc:  # noqa: BLE001 — versioning must never 500
        logger.warning(
            "apply_event failed for wikidata_override %s: %s", entity_id_str, exc,
        )

    await db.commit()
    return ItemOverrideResponse(
        run_id=run_id, local_id=local_id,
        labels=row.labels or {}, descriptions=row.descriptions or {},
        aliases=row.aliases or {}, add_statements=row.add_statements or [],
        remove_statements=row.remove_statements or [],
        statement_edits=row.statement_edits or {},
    )


@router.delete(
    "/{run_id}/wikidata-studio/items/{local_id:path}/overrides",
    status_code=204,
)
async def clear_item_override(
    run_id: uuid.UUID, local_id: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> None:
    """Drop every curator edit for this item — next rebuild returns to
    what the builder + matchers produced."""
    run = await _lookup_run_with_access(db, run_id, auth, write=True)

    # Fetch the row first so we can record the tombstone against its
    # stable UUID — the bulk DELETE below loses the id otherwise.
    row = (
        await db.execute(
            select(WikidataItemOverride).where(
                WikidataItemOverride.run_id == run_id,
                WikidataItemOverride.local_id == local_id,
            )
        )
    ).scalar_one_or_none()

    if row is not None:
        entity_id_str = str(row.id)
        try:
            has_history = (
                await db.execute(
                    select(ProjectEvent.id)
                    .where(
                        ProjectEvent.entity_type == ENTITY_TYPE_WIKIDATA_OVERRIDE,
                        ProjectEvent.entity_id == entity_id_str,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none() is not None
            op_kind = OP_PATCH if has_history else OP_CREATE
            await apply_event(
                db,
                project_id=run.project_id,
                entity_type=ENTITY_TYPE_WIKIDATA_OVERRIDE,
                entity_id=entity_id_str,
                op=op_kind,
                new_state={"_deleted": True},
                actor_id=auth.user.id,
                message=f"wikidata override cleared ({local_id})",
            )
        except Exception as exc:  # noqa: BLE001 — versioning must never 500
            logger.warning(
                "apply_event failed for wikidata_override tombstone %s: %s",
                entity_id_str, exc,
            )

    await db.execute(
        WikidataItemOverride.__table__.delete().where(
            (WikidataItemOverride.run_id == run_id)
            & (WikidataItemOverride.local_id == local_id)
        )
    )
    await db.commit()


# ── helpers ─────────────────────────────────────────────────────────────


async def _build_native_items(
    db: AsyncSession, run_id: uuid.UUID, auth: AuthContext,
    *, approved_only: bool,
) -> list[Any]:
    """Re-run the builder and return the *native* WikidataItem objects
    (not the JSON dicts) so reconcile/upload can mutate them in place."""
    await _lookup_run_with_access(db, run_id, auth)
    records = (
        await db.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )
    ).scalars().all()
    all_matches = (
        await db.execute(select(AuthorityMatch).where(AuthorityMatch.run_id == run_id))
    ).scalars().all()
    matches = [m for m in all_matches if m.approved] if approved_only else all_matches

    overrides = await _load_overrides(db, run_id)
    entities_by_cn = await _load_entities_by_cn(db, run_id, approved_only)
    result = await wikidata_studio.build_items_for_run(
        marc_records=[dict(r.marc) for r in records],
        approved_matches=[
            {
                "id": str(m.id),
                "control_number": m.control_number,
                "entity_text": m.entity_text,
                "role": m.role,
                "matched_name": m.matched_name,
                "mazal_id": m.mazal_id,
                "viaf_id": m.viaf_id,
                "wikidata_qid": m.wikidata_qid,
                "confidence": m.confidence,
                "source": m.source,
                "payload": m.payload or {},
            }
            for m in matches
        ],
        entities_by_cn=entities_by_cn,
        overrides=overrides,
        return_native=True,
    )
    return result.get("native_items") or []


async def _load_entities_by_cn(
    db: AsyncSession, run_id: uuid.UUID, approved_only: bool,
) -> dict[str, list[dict[str, Any]]]:
    """Load Stage-2 NER entities, grouped by control_number.

    Shape matches what the desktop ``WikidataItemBuilder`` expects on
    ``record["entities"]``: each dict carries ``source``, ``type``,
    ``text``, ``start``, ``end``, ``role``, ``confidence``,
    ``model_confidence``. Curator-supplied ``override_type`` /
    ``override_role`` (Rule 50 / desktop "edits flow downstream")
    take precedence over the model's prediction.

    When ``approved_only`` is True we filter out unapproved entities so
    the studio output mirrors ``approved_only`` semantics already
    applied to authority matches — matching the desktop pipeline's
    "ship this in the final output" filter (extraction_approval.py
    doctring).
    """
    stmt = select(ExtractionApproval).where(ExtractionApproval.run_id == run_id)
    if approved_only:
        stmt = stmt.where(ExtractionApproval.approved.is_(True))
    rows = (await db.execute(stmt)).scalars().all()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        grouped.setdefault(r.control_number, []).append({
            "text":             r.text,
            "type":             (r.override_type or r.type or "").upper(),
            "role":             (r.override_role or r.role or "").upper(),
            "source":           r.source,
            "start":            int(r.start or 0),
            "end":              int(r.end or 0),
            "confidence":       r.confidence,
            "model_confidence": r.model_confidence,
            "approved":         bool(r.approved),
        })
    return grouped


async def _load_overrides(
    db: AsyncSession, run_id: uuid.UUID,
) -> dict[str, dict[str, Any]]:
    rows = (
        await db.execute(
            select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
        )
    ).scalars().all()
    return {
        r.local_id: {
            "labels":            r.labels,
            "descriptions":      r.descriptions,
            "aliases":           r.aliases,
            "add_statements":    r.add_statements,
            "remove_statements": r.remove_statements,
            "statement_edits":   r.statement_edits,
        }
        for r in rows
    }


async def _unwrap_user_secret(db: AsyncSession, auth: AuthContext, key_name: str) -> str | None:
    """Unwrap the user's stored Wikidata token (or any named secret)
    using the request's KEK. Returns None when the user hasn't saved one."""
    from cryptography.exceptions import InvalidTag  # noqa: PLC0415

    from app.crypto import secrets as secrets_mod  # noqa: PLC0415
    from app.models.api_key import ApiKey  # noqa: PLC0415

    row = (
        await db.execute(
            select(ApiKey).where(ApiKey.user_id == auth.user.id, ApiKey.key_name == key_name)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    try:
        return secrets_mod.unwrap_secret(
            secrets_mod.WrappedSecret(
                ciphertext=row.ciphertext,
                ciphertext_nonce=row.ciphertext_nonce,
                dek_wrapped=row.dek_wrapped,
                dek_wrap_nonce=row.dek_wrap_nonce,
            ),
            kek=auth.kek,
        )
    except InvalidTag:
        return None
