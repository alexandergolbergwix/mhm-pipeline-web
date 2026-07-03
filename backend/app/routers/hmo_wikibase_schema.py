"""Global HMO Wikibase schema bootstrap router.

Unlike ``hmo_studio.py`` (per-run manifest build/upload, prefixed under
``/runs/{run_id}/hmo-studio/``), the ontology schema is global — one
ontology file, one Wikibase Cloud instance — so this router has no
``run_id`` in its path:

    GET  /api/hmo-wikibase-schema/status     →  ontology counts vs. mapped
    POST /api/hmo-wikibase-schema/bootstrap  →  create missing classes/properties

Gated on ``current_auth`` only (any signed-in user), matching the
``/me/api-keys`` router's pattern for account-scoped rather than
project-scoped operations — this endpoint has no project to scope to.
"""

from __future__ import annotations

import logging

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.crypto import secrets as secrets_mod
from app.db import get_session
from app.models.api_key import ApiKey
from app.pipeline import hmo_schema_bootstrap as pipeline

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/hmo-wikibase-schema", tags=["hmo-wikibase-schema"])

# Mirrors hmo_studio.py's default bot name — operators create the bot
# password at https://mhm-hmo.wikibase.cloud/wiki/Special:BotPasswords.
_DEFAULT_BOT_NAME = "mhm-pipeline"


class SchemaStatusResponse(BaseModel):
    total_classes: int
    total_properties: int
    mapped_classes: int
    mapped_properties: int
    missing_sample: list[str]
    bot_username_set: bool
    bot_password_set: bool


class SchemaBootstrapEntryDto(BaseModel):
    ontology_uri: str
    entity_kind: str
    label: str
    wikibase_id: str | None
    status: str
    message: str = ""


class SchemaBootstrapResponse(BaseModel):
    dry_run: bool
    created: int
    skipped: int
    failed: int
    entries: list[SchemaBootstrapEntryDto]


class SchemaBootstrapRequest(BaseModel):
    dry_run: bool = Field(
        default=True,
        description="Default True — reports what would be created without "
                    "writing. Set False for live; live also requires the "
                    "user to have both wikibase_cloud_bot_username and "
                    "wikibase_cloud_bot_password stored in Settings.",
    )


@router.get("/status", response_model=SchemaStatusResponse)
async def get_schema_status(
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> SchemaStatusResponse:
    """Ontology class/property counts vs. how many already have a live mapping."""
    result = await pipeline.schema_status(db)
    return SchemaStatusResponse(
        total_classes=result.total_classes,
        total_properties=result.total_properties,
        mapped_classes=result.mapped_classes,
        mapped_properties=result.mapped_properties,
        missing_sample=result.missing_sample,
        bot_username_set=await _has_user_secret(db, auth, "wikibase_cloud_bot_username"),
        bot_password_set=await _has_user_secret(db, auth, "wikibase_cloud_bot_password"),
    )


@router.post("/bootstrap", response_model=SchemaBootstrapResponse)
async def bootstrap_schema(
    payload: SchemaBootstrapRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> SchemaBootstrapResponse:
    """Create every missing HMO ontology class/property on the Wikibase Cloud.

    Dry-run (the default) requires no credentials. A live run requires
    both bot username + password in the user's encrypted-secret store.
    """
    writer = None
    if not payload.dry_run:
        bot_username = await _unwrap_user_secret(db, auth, "wikibase_cloud_bot_username")
        bot_password = await _unwrap_user_secret(db, auth, "wikibase_cloud_bot_password")
        missing = [
            name for name, val in (
                ("wikibase_cloud_bot_username", bot_username),
                ("wikibase_cloud_bot_password", bot_password),
            )
            if not val
        ]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Add Wikibase bot credentials in Settings → "
                    "Credentials, then retry. Missing: " + ", ".join(missing)
                ),
            )
        from converter.wikibase.cloud_client import (  # noqa: PLC0415
            WikibaseBotCredentials,
            WikibaseCloudClient,
            WikibaseCloudWriter,
        )

        writer = WikibaseCloudWriter(
            WikibaseCloudClient.config_for_mhm_hmo_cloud(),
            WikibaseBotCredentials(
                username=bot_username or "",
                bot_name=_DEFAULT_BOT_NAME,
                password=bot_password or "",
            ),
        )

    result = await pipeline.bootstrap_schema(db, writer=writer, dry_run=payload.dry_run)
    # Snapshot to disk so the eval-agent's hmo_wikibase_schema evaluator
    # can judge this pass (`eval-agent run --pipeline-output
    # backend/state/hmo_wikibase_schema --evaluators hmo_wikibase_schema`).
    pipeline.cache_schema_bootstrap_report(result)
    return SchemaBootstrapResponse(
        dry_run=result.dry_run,
        created=result.created,
        skipped=result.skipped,
        failed=result.failed,
        entries=[SchemaBootstrapEntryDto(**entry.__dict__) for entry in result.entries],
    )


# ── Helpers (mirrors hmo_studio.py's credential-unwrap pattern) ─────────


async def _has_user_secret(db: AsyncSession, auth: AuthContext, key_name: str) -> bool:
    row = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.user_id == auth.user.id, ApiKey.key_name == key_name,
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def _unwrap_user_secret(
    db: AsyncSession, auth: AuthContext, key_name: str,
) -> str | None:
    row = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.user_id == auth.user.id, ApiKey.key_name == key_name,
            )
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
        logger.warning("Failed to unwrap %s for user %s", key_name, auth.user.id)
        return None
