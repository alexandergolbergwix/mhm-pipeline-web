"""Validate and enrich job params with server-side secrets at start time."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext
from app.models.run_job import (
    JOB_KIND_AUTHORITY_RE_ENRICH,
    JOB_KIND_AUTHORITY_VERIFY,
    JOB_KIND_EXTRACTION,
    JOB_KIND_HMO_ITEM_UPLOAD,
    JOB_KIND_HMO_ITEM_VERIFY,
    JOB_KIND_HMO_SCHEMA_BOOTSTRAP,
    JOB_KIND_NER_VERIFY,
    JOB_KIND_RDF_BUILD,
    JOB_KIND_WIKIDATA_STUDIO_BUILD,
    JOB_KIND_WIKIDATA_UPLOAD,
    JOB_KIND_WIKIDATA_VERIFY,
    SUPPORTED_JOB_KINDS,
)
from app.pipeline.agent_runner import new_session_id

logger = logging.getLogger(__name__)


async def prepare_job_params(
    db: AsyncSession,
    auth: AuthContext,
    *,
    run_id: uuid.UUID,
    kind: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    if kind not in SUPPORTED_JOB_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported job kind {kind!r}",
        )

    merged = dict(params)

    if kind in (JOB_KIND_AUTHORITY_RE_ENRICH, JOB_KIND_AUTHORITY_VERIFY):
        from app.settings import get_settings  # noqa: PLC0415
        if not get_settings().legacy_authority_mutations_enabled:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail=(
                    "Standalone Authority jobs are retired; "
                    "rebuild or verify canonical HMO entities instead."
                ),
            )

    if kind == JOB_KIND_EXTRACTION:
        from app.routers.extraction import _unwrap_user_huggingface_key  # noqa: PLC0415

        hf_token = await _unwrap_user_huggingface_key(
            db, user_id=auth.user.id, kek=auth.kek,
        )
        if not hf_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "No HuggingFace access token configured. Open Settings → "
                    "Credentials and add a token with 'read' access."
                ),
            )
        merged["_hf_token"] = hf_token
        return merged

    if kind in (
        JOB_KIND_NER_VERIFY, JOB_KIND_AUTHORITY_VERIFY, JOB_KIND_WIKIDATA_VERIFY,
        JOB_KIND_HMO_ITEM_VERIFY,
    ):
        from app.pipeline.judge_models import (  # noqa: PLC0415
            UnknownTier1ModelError,
            ensure_tier1_credentials,
            resolve_tier1_model,
        )

        tier_raw = merged.get("tier_model")
        try:
            spec = resolve_tier1_model(str(tier_raw) if tier_raw else None)
        except UnknownTier1ModelError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        gemini_key = await _resolve_gemini_key(db, auth)
        try:
            ensure_tier1_credentials(spec, gemini_key=gemini_key)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        merged["tier_model"] = spec.id
        merged["_api_key"] = gemini_key or ""
        if not merged.get("session_id"):
            merged["session_id"] = new_session_id()
        await _validate_verify_params(db, run_id, kind, merged, auth)
        return merged

    if kind == JOB_KIND_WIKIDATA_UPLOAD:
        from app.pipeline.wikidata_upload import (  # noqa: PLC0415
            VALID_UPLOAD_TARGETS,
            resolve_upload_mode,
        )
        from app.routers.wikidata_studio import _unwrap_user_secret  # noqa: PLC0415

        raw_target = merged.get("upload_target")
        if raw_target is not None and str(raw_target).strip():
            target = str(raw_target).strip().lower()
            if target not in VALID_UPLOAD_TARGETS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "upload_target must be one of: dry_run, test, live"
                    ),
                )
            merged["upload_target"] = target
        else:
            # Legacy dry_run bool → canonical target; default dry_run.
            mode_legacy = resolve_upload_mode(
                None, dry_run=merged.get("dry_run", True),
            )
            merged["upload_target"] = mode_legacy.target

        mode = resolve_upload_mode(
            merged.get("upload_target"),
            dry_run=merged.get("dry_run"),
        )
        merged["dry_run"] = mode.dry_run
        merged["upload_target"] = mode.target

        token = await _unwrap_user_secret(db, auth, "wikidata")
        if not mode.dry_run and not token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Live upload requires a Wikidata token in Settings.",
            )
        # Dry-run also carries the token when present so ownership classify
        # (own vs foreign) matches live policy (Rule W-99).
        if token:
            merged["_wikidata_token"] = token

    if kind == JOB_KIND_HMO_ITEM_UPLOAD or (
        kind == JOB_KIND_HMO_SCHEMA_BOOTSTRAP and not merged.get("dry_run", True)
    ):
        from app.settings import get_settings  # noqa: PLC0415

        if not get_settings().wikibase_cloud_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Wikibase Cloud is not configured on this server. "
                    "Contact the administrator."
                ),
            )

    if kind == JOB_KIND_RDF_BUILD:
        merged.setdefault("add_epistemological_status", True)
        merged.setdefault("add_cataloging_view", True)
        merged.setdefault("add_philological_overlay", True)

    if kind == JOB_KIND_WIKIDATA_STUDIO_BUILD:
        merged.setdefault("approved_only", True)
        merged.setdefault("force_rebuild", False)

    if kind == JOB_KIND_AUTHORITY_RE_ENRICH:
        from app.settings import get_settings  # noqa: PLC0415
        if not get_settings().legacy_authority_mutations_enabled:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail=(
                    "Standalone Authority re-enrichment is retired; "
                    "rebuild HMO canonical entities instead."
                ),
            )
        merged.setdefault("skip_cache", False)

    return merged


async def _validate_verify_params(
    db: AsyncSession,
    run_id: uuid.UUID,
    kind: str,
    params: dict[str, Any],
    auth: AuthContext,
) -> None:
    action_id = str(params.get("action_id") or "").strip()
    if not action_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="action_id is required for verify jobs",
        )

    if kind == JOB_KIND_AUTHORITY_VERIFY:
        from app.pipeline import agent_actions  # noqa: PLC0415
        from app.routers.ai_verify import _fetch_matches  # noqa: PLC0415

        action = agent_actions.get_action(action_id)
        if action is None:
            raise HTTPException(status_code=400, detail=f"unknown action_id {action_id!r}")
        raw_ids = params.get("match_ids")
        match_ids = None
        if raw_ids:
            match_ids = [uuid.UUID(str(x)) for x in raw_ids]
        matches = await _fetch_matches(db, run_id, match_ids)
        if not matches:
            raise HTTPException(status_code=400, detail="no authority matches in scope")
        if len(matches) < action.min_candidates:
            raise HTTPException(
                status_code=400,
                detail=f"action requires at least {action.min_candidates} candidates",
            )
        return

    if kind == JOB_KIND_NER_VERIFY:
        from app.pipeline import extraction_actions  # noqa: PLC0415
        from app.routers.extraction_verify import _fetch_entities  # noqa: PLC0415

        action = extraction_actions.get_action(action_id)
        if action is None:
            raise HTTPException(status_code=400, detail=f"unknown action_id {action_id!r}")
        entities = await _fetch_entities(db, run_id, params.get("entity_ids"))
        if not entities:
            raise HTTPException(status_code=400, detail="no extracted entities in scope")
        if len(entities) < action.min_candidates:
            raise HTTPException(
                status_code=400,
                detail=f"action requires at least {action.min_candidates} candidates",
            )
        return

    if kind == JOB_KIND_WIKIDATA_VERIFY:
        from app.pipeline import wikidata_actions  # noqa: PLC0415

        action = wikidata_actions.get_action(action_id)
        if action is None:
            raise HTTPException(status_code=400, detail=f"unknown action_id {action_id!r}")
        # Building the Studio scope can exceed Heroku’s 30-second request limit.
        # The claimed worker validates the scope after this request commits the job.
        return

    if kind == JOB_KIND_HMO_ITEM_VERIFY:
        from app.pipeline import hmo_item_actions  # noqa: PLC0415
        from app.routers.hmo_studio_items import (  # noqa: PLC0415
            _fetch_verify_items,
            _prepare_verify_scope,
        )

        action = hmo_item_actions.get_action(action_id)
        if action is None:
            raise HTTPException(status_code=400, detail=f"unknown action_id {action_id!r}")
        items = await _fetch_verify_items(db, run_id, item_ids=params.get("item_ids"))
        items = await _prepare_verify_scope(action, items)
        if not items:
            raise HTTPException(status_code=400, detail="no HMO items in scope")
        if len(items) < action.min_candidates:
            raise HTTPException(
                status_code=400,
                detail=f"action requires at least {action.min_candidates} candidates",
            )


async def _resolve_gemini_key(db: AsyncSession, auth: AuthContext) -> str | None:
    import os

    try:
        from app.pipeline.ai_verifier import unwrap_user_gemini_key  # noqa: PLC0415

        key = await unwrap_user_gemini_key(db, user_id=auth.user.id, kek=auth.kek)
        if key:
            return key
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not unwrap stored Gemini key: %s", exc)
    return os.environ.get("GEMINI_API_KEY")
