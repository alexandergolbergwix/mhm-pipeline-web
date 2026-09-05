"""Resolve Publication credentials without storing a secret in a job row."""

from __future__ import annotations

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
