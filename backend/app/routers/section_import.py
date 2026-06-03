"""Section-level import endpoints.

One POST per pipeline section.  Each accepts a ``multipart/form-data``
file upload (JSON or CSV for tabular sections, TTL for RDF) and
returns a JSON summary with counts and per-row errors.

URL surface::

    POST /runs/{run_id}/extraction/import   (JSON | CSV)
    POST /runs/{run_id}/authority/import    (JSON | CSV)
    POST /runs/{run_id}/rdf/import          (TTL — replaces built graph on disk)
    POST /runs/{run_id}/wikibase/import     (JSON)
    POST /runs/{run_id}/wikidata-studio/import  (JSON | CSV)

All endpoints require editor role.  Format detection is by
``file.content_type`` with a file-extension fallback.

Import pipeline (tabular sections):

1. Parse bytes → list[dict] (csv.DictReader or json.loads).
2. Validate each row against a Pydantic ``ImportRow`` model; invalid
   rows accumulate in ``errors`` and are skipped.
3. Upsert via ``apply_event`` (Rule W-21):
   - Look up the existing row by natural key.
   - If exists and data differs → ``OP_PATCH`` + update read-model.
   - If new → ``OP_CREATE`` + insert read-model.
4. Invalidate ``WikidataStudioCache`` after extraction / authority
   imports (fingerprint changes automatically on next rebuild request).
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.event import (
    ENTITY_TYPE_AUTHORITY_MATCH,
    ENTITY_TYPE_EXTRACTION_ENTITY,
    ENTITY_TYPE_WIKIDATA_OVERRIDE,
    ENTITY_TYPE_WIKIBASE_ITEM,
    OP_CREATE,
    OP_PATCH,
)
from app.models.extraction_approval import ExtractionApproval
from app.models.item_override import WikidataItemOverride
from app.models.run import AuthorityMatch, Run
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.pipeline.rdf_build import rdf_output_path_for_run
from app.routers.runs import _lookup_run_with_access
from app.versioning import apply_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["section-import"])

_MAX_IMPORT_BYTES = 50 * 1024 * 1024  # 50 MB


# ── Response schema ───────────────────────────────────────────────────


class ImportRowError(BaseModel):
    row: int
    message: str


class ImportResult(BaseModel):
    imported: int
    skipped: int
    errors: list[ImportRowError]


# ── Format detection ──────────────────────────────────────────────────


def _detect_format(file: UploadFile) -> str:
    """Return 'json', 'csv', or 'ttl' based on content_type + filename."""
    ct = (file.content_type or "").lower()
    if "json" in ct:
        return "json"
    if "csv" in ct or "comma" in ct:
        return "csv"
    if "turtle" in ct or "ttl" in ct:
        return "ttl"
    # Fall back to file extension
    name = (file.filename or "").lower()
    if name.endswith(".json"):
        return "json"
    if name.endswith(".csv"):
        return "csv"
    if name.endswith(".ttl"):
        return "ttl"
    return "json"  # default


def _parse_bytes(raw: bytes, fmt: str) -> list[dict[str, Any]]:
    """Parse raw upload bytes into a list of row dicts."""
    if fmt == "csv":
        text = raw.decode("utf-8-sig")  # strip BOM if present
        reader = csv.DictReader(io.StringIO(text))
        return [dict(row) for row in reader]
    if fmt == "json":
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, list):
            return data
        # Accept keyed envelopes like {"entities": [...]} or {"matches": [...]}
        for key in ("entities", "matches", "items", "overrides"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return [data]
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"Unsupported format: {fmt!r}",
    )


def _coerce_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("1", "true", "yes", "y")


def _coerce_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _coerce_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── Extraction import ─────────────────────────────────────────────────


class ExtractionImportRow(BaseModel):
    control_number: str
    source: str
    text: str
    start: int = 0
    end: int = 0
    type: str | None = None
    role: str | None = None
    confidence: float | None = None
    model_confidence: float | None = None
    override_text: str | None = None
    override_type: str | None = None
    override_role: str | None = None
    approved: bool = False

    @field_validator("control_number", "source", "text", mode="before")
    @classmethod
    def nonempty(cls, v: Any) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("must not be empty")
        return s

    @field_validator("start", "end", mode="before")
    @classmethod
    def coerce_int(cls, v: Any) -> int:
        return _coerce_int(v)

    @field_validator("confidence", "model_confidence", mode="before")
    @classmethod
    def coerce_float(cls, v: Any) -> float | None:
        if v in (None, "", "None"):
            return None
        return _coerce_float(v)

    @field_validator("approved", mode="before")
    @classmethod
    def coerce_bool(cls, v: Any) -> bool:
        return _coerce_bool(v)


@router.post("/{run_id}/extraction/import", response_model=ImportResult)
async def import_extraction(
    run_id: uuid.UUID,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ImportResult:
    """Upload extraction entities (JSON or CSV) and upsert into the run."""
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    raw = await file.read(_MAX_IMPORT_BYTES)
    fmt = _detect_format(file)
    rows_raw = _parse_bytes(raw, fmt)

    imported = 0
    skipped = 0
    errors: list[ImportRowError] = []

    for idx, row_raw in enumerate(rows_raw):
        try:
            row = ExtractionImportRow.model_validate(row_raw)
        except ValidationError as exc:
            errors.append(ImportRowError(row=idx, message=str(exc.errors()[0]["msg"])))
            skipped += 1
            continue

        existing = (
            await db.execute(
                select(ExtractionApproval).where(
                    ExtractionApproval.run_id == run_id,
                    ExtractionApproval.control_number == row.control_number,
                    ExtractionApproval.source == row.source,
                    ExtractionApproval.text == row.text,
                    ExtractionApproval.start == row.start,
                    ExtractionApproval.end == row.end,
                )
            )
        ).scalar_one_or_none()

        new_state: dict[str, Any] = {
            "control_number": row.control_number,
            "source": row.source,
            "text": row.text,
            "start": row.start,
            "end": row.end,
            "type": row.type,
            "role": row.role,
            "confidence": row.confidence,
            "model_confidence": row.model_confidence,
            "override_text": row.override_text,
            "override_type": row.override_type,
            "override_role": row.override_role,
            "approved": row.approved,
        }

        if existing is not None:
            # Only upsert if anything changed
            current_state = {
                "control_number": existing.control_number,
                "source": existing.source,
                "text": existing.text,
                "start": existing.start,
                "end": existing.end,
                "type": existing.type,
                "role": existing.role,
                "confidence": existing.confidence,
                "model_confidence": existing.model_confidence,
                "override_text": existing.override_text,
                "override_type": existing.override_type,
                "override_role": existing.override_role,
                "approved": existing.approved,
            }
            if current_state == new_state:
                skipped += 1
                continue
            try:
                await apply_event(
                    db,
                    project_id=run.project_id,
                    entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
                    entity_id=str(existing.id),
                    op=OP_PATCH,
                    new_state=new_state,
                    actor_id=auth.user.id,
                    message="import",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("apply_event PATCH extraction %s: %s", existing.id, exc)
            existing.type = row.type
            existing.role = row.role
            existing.confidence = row.confidence
            existing.model_confidence = row.model_confidence
            existing.override_text = row.override_text
            existing.override_type = row.override_type
            existing.override_role = row.override_role
            existing.approved = row.approved
        else:
            entity = ExtractionApproval(
                run_id=run_id,
                control_number=row.control_number,
                source=row.source,
                text=row.text,
                start=row.start,
                end=row.end,
                type=row.type,
                role=row.role,
                confidence=row.confidence,
                model_confidence=row.model_confidence,
                override_text=row.override_text,
                override_type=row.override_type,
                override_role=row.override_role,
                approved=row.approved,
                approved_by=auth.user.id if row.approved else None,
                approved_at=datetime.now(timezone.utc) if row.approved else None,
            )
            db.add(entity)
            await db.flush()
            try:
                await apply_event(
                    db,
                    project_id=run.project_id,
                    entity_type=ENTITY_TYPE_EXTRACTION_ENTITY,
                    entity_id=str(entity.id),
                    op=OP_CREATE,
                    new_state=new_state,
                    actor_id=auth.user.id,
                    message="import",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("apply_event CREATE extraction: %s", exc)

        imported += 1

    # Invalidate wikidata studio cache — extraction feeds the builder
    await db.execute(
        delete(WikidataStudioCache).where(WikidataStudioCache.run_id == run_id)
    )
    await db.commit()
    return ImportResult(imported=imported, skipped=skipped, errors=errors)


# ── Authority import ──────────────────────────────────────────────────


class AuthorityImportRow(BaseModel):
    control_number: str
    entity_text: str
    entity_kind: str = "person"
    role: str = ""
    matched_name: str = ""
    mazal_id: str = ""
    viaf_id: str = ""
    wikidata_qid: str = ""
    confidence: str = "low"
    source: str = ""
    approved: bool = False

    @field_validator("control_number", "entity_text", mode="before")
    @classmethod
    def nonempty(cls, v: Any) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("must not be empty")
        return s

    @field_validator("approved", mode="before")
    @classmethod
    def coerce_bool(cls, v: Any) -> bool:
        return _coerce_bool(v)

    @field_validator(
        "matched_name", "mazal_id", "viaf_id", "wikidata_qid",
        "confidence", "source", "entity_kind", "role",
        mode="before",
    )
    @classmethod
    def to_str(cls, v: Any) -> str:
        return "" if v is None else str(v)


@router.post("/{run_id}/authority/import", response_model=ImportResult)
async def import_authority(
    run_id: uuid.UUID,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ImportResult:
    """Upload authority matches (JSON or CSV) and upsert into the run."""
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    raw = await file.read(_MAX_IMPORT_BYTES)
    fmt = _detect_format(file)
    rows_raw = _parse_bytes(raw, fmt)

    imported = 0
    skipped = 0
    errors: list[ImportRowError] = []

    for idx, row_raw in enumerate(rows_raw):
        try:
            row = AuthorityImportRow.model_validate(row_raw)
        except ValidationError as exc:
            errors.append(ImportRowError(row=idx, message=str(exc.errors()[0]["msg"])))
            skipped += 1
            continue

        existing = (
            await db.execute(
                select(AuthorityMatch).where(
                    AuthorityMatch.run_id == run_id,
                    AuthorityMatch.control_number == row.control_number,
                    AuthorityMatch.entity_text == row.entity_text,
                    AuthorityMatch.entity_kind == row.entity_kind,
                    AuthorityMatch.role == row.role,
                    AuthorityMatch.source == row.source,
                )
            )
        ).scalar_one_or_none()

        new_state: dict[str, Any] = {
            "control_number": row.control_number,
            "entity_text": row.entity_text,
            "entity_kind": row.entity_kind,
            "role": row.role,
            "matched_name": row.matched_name,
            "mazal_id": row.mazal_id,
            "viaf_id": row.viaf_id,
            "wikidata_qid": row.wikidata_qid,
            "confidence": row.confidence,
            "source": row.source,
            "approved": row.approved,
        }

        if existing is not None:
            current_state = {
                "control_number": existing.control_number,
                "entity_text": existing.entity_text,
                "entity_kind": existing.entity_kind,
                "role": existing.role,
                "matched_name": existing.matched_name,
                "mazal_id": existing.mazal_id,
                "viaf_id": existing.viaf_id,
                "wikidata_qid": existing.wikidata_qid,
                "confidence": existing.confidence,
                "source": existing.source,
                "approved": existing.approved,
            }
            if current_state == new_state:
                skipped += 1
                continue
            try:
                await apply_event(
                    db,
                    project_id=run.project_id,
                    entity_type=ENTITY_TYPE_AUTHORITY_MATCH,
                    entity_id=str(existing.id),
                    op=OP_PATCH,
                    new_state=new_state,
                    actor_id=auth.user.id,
                    message="import",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("apply_event PATCH authority %s: %s", existing.id, exc)
            existing.matched_name = row.matched_name
            existing.mazal_id = row.mazal_id
            existing.viaf_id = row.viaf_id
            existing.wikidata_qid = row.wikidata_qid
            existing.confidence = row.confidence
            existing.approved = row.approved
            if row.approved and not existing.approved_by:
                existing.approved_by = auth.user.id
                existing.approved_at = datetime.now(timezone.utc)
        else:
            match = AuthorityMatch(
                run_id=run_id,
                control_number=row.control_number,
                entity_text=row.entity_text,
                entity_kind=row.entity_kind,
                role=row.role,
                matched_name=row.matched_name,
                mazal_id=row.mazal_id,
                viaf_id=row.viaf_id,
                wikidata_qid=row.wikidata_qid,
                confidence=row.confidence,
                source=row.source,
                approved=row.approved,
                approved_by=auth.user.id if row.approved else None,
                approved_at=datetime.now(timezone.utc) if row.approved else None,
                payload={},
            )
            db.add(match)
            await db.flush()
            try:
                await apply_event(
                    db,
                    project_id=run.project_id,
                    entity_type=ENTITY_TYPE_AUTHORITY_MATCH,
                    entity_id=str(match.id),
                    op=OP_CREATE,
                    new_state=new_state,
                    actor_id=auth.user.id,
                    message="import",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("apply_event CREATE authority: %s", exc)

        imported += 1

    # Authority matches feed the Wikidata Studio builder
    await db.execute(
        delete(WikidataStudioCache).where(WikidataStudioCache.run_id == run_id)
    )
    await db.commit()
    return ImportResult(imported=imported, skipped=skipped, errors=errors)


# ── RDF import ────────────────────────────────────────────────────────


@router.post("/{run_id}/rdf/import", response_model=ImportResult)
async def import_rdf(
    run_id: uuid.UUID,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ImportResult:
    """Upload a Turtle file to replace the built RDF graph on disk.

    The TTL is sanity-checked with rdflib.parse before overwriting the
    existing graph.  No versioning event is emitted — RDF is a derived
    artefact (rebuilt from MARC + approvals); this import is an
    explicit override of that derived output.
    """
    await _lookup_run_with_access(db, run_id, auth, write=True)
    raw = await file.read(_MAX_IMPORT_BYTES)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is empty.",
        )

    # Validate that it parses as Turtle before writing
    try:
        await asyncio.to_thread(_validate_ttl, raw)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid Turtle: {exc}",
        ) from exc

    ttl_path = rdf_output_path_for_run(str(run_id))
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(ttl_path.write_bytes, raw)

    return ImportResult(imported=1, skipped=0, errors=[])


def _validate_ttl(raw: bytes) -> None:
    import rdflib  # noqa: PLC0415
    g = rdflib.Graph()
    g.parse(data=raw.decode("utf-8"), format="turtle")


# ── Wikibase import ───────────────────────────────────────────────────


class WikibaseImportRow(BaseModel):
    entity_id: str
    state: dict[str, Any] | None = None

    @field_validator("entity_id", mode="before")
    @classmethod
    def nonempty(cls, v: Any) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("must not be empty")
        return s


@router.post("/{run_id}/wikibase/import", response_model=ImportResult)
async def import_wikibase(
    run_id: uuid.UUID,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ImportResult:
    """Upload Wikibase items (JSON) and persist as versioned events."""
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    raw = await file.read(_MAX_IMPORT_BYTES)
    rows_raw = _parse_bytes(raw, "json")

    imported = 0
    skipped = 0
    errors: list[ImportRowError] = []

    for idx, row_raw in enumerate(rows_raw):
        try:
            row = WikibaseImportRow.model_validate(row_raw)
        except ValidationError as exc:
            errors.append(ImportRowError(row=idx, message=str(exc.errors()[0]["msg"])))
            skipped += 1
            continue

        try:
            await apply_event(
                db,
                project_id=run.project_id,
                entity_type=ENTITY_TYPE_WIKIBASE_ITEM,
                entity_id=row.entity_id,
                op=OP_CREATE,
                new_state=row.state or {},
                actor_id=auth.user.id,
                message="import",
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(ImportRowError(row=idx, message=str(exc)))
            skipped += 1
            continue

        imported += 1

    await db.commit()
    return ImportResult(imported=imported, skipped=skipped, errors=errors)


# ── Wikidata Studio import ────────────────────────────────────────────


class WikidataOverrideImportRow(BaseModel):
    local_id: str
    labels: dict[str, Any] = {}
    descriptions: dict[str, Any] = {}
    aliases: dict[str, Any] = {}
    add_statements: list[dict[str, Any]] = []
    remove_statements: list[int] = []
    statement_edits: dict[str, Any] = {}

    @field_validator("local_id", mode="before")
    @classmethod
    def nonempty(cls, v: Any) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("must not be empty")
        return s

    @field_validator(
        "labels", "descriptions", "aliases", "statement_edits",
        mode="before",
    )
    @classmethod
    def coerce_dict(cls, v: Any) -> dict[str, Any]:
        if v is None or v == "":
            return {}
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return v if isinstance(v, dict) else {}

    @field_validator("add_statements", mode="before")
    @classmethod
    def coerce_list(cls, v: Any) -> list[dict[str, Any]]:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                return []
        return v if isinstance(v, list) else []

    @field_validator("remove_statements", mode="before")
    @classmethod
    def coerce_int_list(cls, v: Any) -> list[int]:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return [int(x) for x in parsed] if isinstance(parsed, list) else []
            except (json.JSONDecodeError, ValueError):
                return []
        return [int(x) for x in v] if isinstance(v, list) else []


@router.post("/{run_id}/wikidata-studio/import", response_model=ImportResult)
async def import_wikidata_studio(
    run_id: uuid.UUID,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ImportResult:
    """Upload Wikidata Studio overrides (JSON or CSV) and upsert."""
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    raw = await file.read(_MAX_IMPORT_BYTES)
    fmt = _detect_format(file)
    rows_raw = _parse_bytes(raw, fmt)

    imported = 0
    skipped = 0
    errors: list[ImportRowError] = []

    for idx, row_raw in enumerate(rows_raw):
        try:
            row = WikidataOverrideImportRow.model_validate(row_raw)
        except ValidationError as exc:
            errors.append(ImportRowError(row=idx, message=str(exc.errors()[0]["msg"])))
            skipped += 1
            continue

        existing = (
            await db.execute(
                select(WikidataItemOverride).where(
                    WikidataItemOverride.run_id == run_id,
                    WikidataItemOverride.local_id == row.local_id,
                )
            )
        ).scalar_one_or_none()

        new_state: dict[str, Any] = {
            "local_id": row.local_id,
            "labels": row.labels,
            "descriptions": row.descriptions,
            "aliases": row.aliases,
            "add_statements": row.add_statements,
            "remove_statements": row.remove_statements,
            "statement_edits": row.statement_edits,
        }

        if existing is not None:
            try:
                await apply_event(
                    db,
                    project_id=run.project_id,
                    entity_type=ENTITY_TYPE_WIKIDATA_OVERRIDE,
                    entity_id=str(existing.id),
                    op=OP_PATCH,
                    new_state=new_state,
                    actor_id=auth.user.id,
                    message="import",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("apply_event PATCH wikidata override %s: %s", existing.id, exc)
            existing.labels = row.labels
            existing.descriptions = row.descriptions
            existing.aliases = row.aliases
            existing.add_statements = row.add_statements
            existing.remove_statements = row.remove_statements
            existing.statement_edits = row.statement_edits
            existing.updated_by = auth.user.id
        else:
            override = WikidataItemOverride(
                run_id=run_id,
                local_id=row.local_id,
                labels=row.labels,
                descriptions=row.descriptions,
                aliases=row.aliases,
                add_statements=row.add_statements,
                remove_statements=row.remove_statements,
                statement_edits=row.statement_edits,
                updated_by=auth.user.id,
            )
            db.add(override)
            await db.flush()
            try:
                await apply_event(
                    db,
                    project_id=run.project_id,
                    entity_type=ENTITY_TYPE_WIKIDATA_OVERRIDE,
                    entity_id=str(override.id),
                    op=OP_CREATE,
                    new_state=new_state,
                    actor_id=auth.user.id,
                    message="import",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("apply_event CREATE wikidata override: %s", exc)

        imported += 1

    # Overrides feed the Wikidata Studio builder
    await db.execute(
        delete(WikidataStudioCache).where(WikidataStudioCache.run_id == run_id)
    )
    await db.commit()
    return ImportResult(imported=imported, skipped=skipped, errors=errors)
