"""Resolve saved credentials and protect execution-scoped worker grants."""

from __future__ import annotations

import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.publication.gateway import WikidataGateway
from app.publication.types import TargetRef
from app.publication.wikidata_gateway import (
    CredentialMaterial,
    CurrentWikidataBoundaryFactory,
    WikidataGatewayAdapter,
)
from app.settings import get_settings


class ServerPublicationCredentialResolver:
    """Map a target-bound reference to one server-held bot credential."""

    async def resolve(self, credential_ref: str) -> CredentialMaterial:
        parts = credential_ref.split(":", 2)
        if len(parts) != 3 or parts[0] != "wikidata":
            raise ValueError("The Publication credential reference is invalid")
        environment = parts[1]
        target = (
            TargetRef(site="www.wikidata.org", environment="production")
            if environment == "production"
            else TargetRef(site="test.wikidata.org", environment="test")
            if environment == "test"
            else None
        )
        if target is None:
            raise ValueError("The Publication credential target is invalid")
        settings = get_settings()
        secret = (
            settings.wikidata_publication_live_token
            if environment == "production"
            else settings.wikidata_publication_test_token
        ).strip()
        if not secret:
            raise ValueError("The Publication bot credential is not configured")
        return CredentialMaterial(
            credential_id=credential_ref,
            target=target,
            secret=secret,
        )


def configured_publication_gateway_factory(
    *,
    target: TargetRef,
    actor_id: str,
) -> WikidataGateway:
    """Build the external gateway without exposing its server-held secret."""
    del target, actor_id
    return WikidataGatewayAdapter(
        credential_resolver=ServerPublicationCredentialResolver(),
        boundary_factory=CurrentWikidataBoundaryFactory(),
    )


class SavedPublicationCredentialResolver:
    """Open only the authenticated user's credential for the requested wiki."""

    def __init__(self, session: AsyncSession, user_id: uuid.UUID, kek: bytes) -> None:
        self._session = session
        self._user_id = user_id
        self._kek = kek

    async def resolve(self, credential_ref: str) -> CredentialMaterial:
        from cryptography.exceptions import InvalidTag
        from app.crypto.secrets import WrappedSecret, unwrap_secret
        from app.models.api_key import ApiKey

        parts = credential_ref.split(":")
        if len(parts) != 3 or parts[0] != "wikidata" or parts[2] != str(self._user_id):
            raise ValueError("The Publication credential belongs to another account")
        if parts[1] not in {"production", "test"}:
            raise ValueError("The Publication credential target is invalid")
        name = "wikidata" if parts[1] == "production" else "wikidata_test"
        row = await self._session.get(ApiKey, (self._user_id, name))
        if row is None:
            return await ServerPublicationCredentialResolver().resolve(credential_ref)
        try:
            secret = unwrap_secret(WrappedSecret(
                ciphertext=row.ciphertext, ciphertext_nonce=row.ciphertext_nonce,
                dek_wrapped=row.dek_wrapped, dek_wrap_nonce=row.dek_wrap_nonce,
            ), kek=self._kek)
        except InvalidTag as exc:
            raise ValueError("Unlock or replace the saved Wikidata credential in Settings") from exc
        return CredentialMaterial(
            credential_id=credential_ref,
            target=TargetRef(site="www.wikidata.org" if parts[1] == "production" else "test.wikidata.org",
                             environment=parts[1]),
            secret=secret,
        )


def seal_execution_credential(
    material: CredentialMaterial, *, publication_id: str, execution_id: str,
    expires_at: float | None = None,
) -> str:
    """Issue an encrypted 24-hour credential for one authorized execution."""
    import base64
    import json
    import time
    from app.crypto.pii import encrypt_pii

    payload = json.dumps({
        "credential_ref": material.credential_id,
        "site": material.target.site, "environment": material.target.environment,
        "secret": material.secret, "publication_id": publication_id,
        "execution_id": execution_id,
        "expires_at": expires_at if expires_at is not None else time.time() + 24 * 60 * 60,
    })
    return base64.b64encode(encrypt_pii(payload)).decode("ascii")


class ExecutionCredentialResolver:
    """Reject a worker credential outside its account, wiki, execution, or lifetime."""

    def __init__(self, envelope: str, *, publication_id: str, execution_id: str, actor_id: str) -> None:
        self._envelope = envelope
        self._publication_id = publication_id
        self._execution_id = execution_id
        self._actor_id = actor_id

    async def resolve(self, credential_ref: str) -> CredentialMaterial:
        import base64
        import binascii
        import json
        import time
        from cryptography.exceptions import InvalidTag
        from app.crypto.pii import decrypt_pii

        try:
            payload = json.loads(decrypt_pii(base64.b64decode(self._envelope, validate=True)))
        except (ValueError, InvalidTag, binascii.Error, UnicodeError) as exc:
            raise ValueError("The Publication execution credential is invalid; resume from your signed-in session") from exc
        if not isinstance(payload, dict):
            raise ValueError("The Publication execution credential is invalid")
        environment = payload.get("environment")
        site = {"production": "www.wikidata.org", "test": "test.wikidata.org"}.get(environment)
        if (
            site is None or payload.get("site") != site
            or payload.get("publication_id") != self._publication_id
            or payload.get("execution_id") != self._execution_id
            or payload.get("credential_ref") != credential_ref
            or credential_ref != f"wikidata:{environment}:{self._actor_id}"
        ):
            raise ValueError("The Publication execution credential does not match this execution")
        expiry = payload.get("expires_at")
        if not isinstance(expiry, (float, int)) or expiry <= time.time():
            raise ValueError("The Publication execution credential expired; resume from your signed-in session")
        secret = payload.get("secret")
        if not isinstance(secret, str) or not secret:
            raise ValueError("The Publication execution credential is empty")
        return CredentialMaterial(credential_id=credential_ref,
            target=TargetRef(site=site, environment=environment), secret=secret)
