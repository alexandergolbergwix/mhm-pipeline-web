"""Build login / me responses with Wikibase access fields."""

from __future__ import annotations

from typing import Literal

from app.crypto import pii
from app.models.user import User
from app.schemas.auth import LoginResponse
from app.services.wikibase_user_access import WikibaseAccessSnapshot

WikiAccountStatus = Literal["none", "pending", "active", "skipped", "failed"]


def build_me_response(
    user: User,
    email: str,
    wikibase: WikibaseAccessSnapshot,
) -> LoginResponse:
    status = wikibase.wikibase_wiki_account_status
    if status not in ("none", "pending", "active", "skipped", "failed"):
        status = "none"
    return LoginResponse(
        id=user.id,
        email=email,
        name=pii.decrypt_pii(user.name_encrypted),
        role=user.role,  # type: ignore[arg-type]
        wikibase_configured=wikibase.wikibase_configured,
        wikibase_authorized=wikibase.wikibase_authorized,
        wikibase_base_url=wikibase.wikibase_base_url,
        wikibase_username=wikibase.wikibase_username,
        wikibase_wiki_account_status=status,
    )
