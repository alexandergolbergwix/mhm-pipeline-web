"""Tests for the production gateway around the true Wikidata boundary."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.publication.gateway import GatewayWriteRequest, TargetObservation
from app.publication.types import PublicationEntity, TargetRef
from app.publication.wikidata_gateway import (
    CredentialMaterial,
    CredentialTargetMismatchError,
    MutationConfirmation,
    RemoteEntitySnapshot,
    WikidataGatewayAdapter,
    WikidataMaxlagError,
)


@dataclass
class _Resolver:
    material: CredentialMaterial

    async def resolve(self, credential_ref: str) -> CredentialMaterial:
        assert credential_ref == self.material.credential_id
        return self.material


class _Boundary:
    def __init__(self) -> None:
        self.reconcile_sizes: list[int] = []
        self.write_calls: list[GatewayWriteRequest] = []
        self.remote_revision = 17
        self.write_error: Exception | None = None

    async def reconcile_batch(
        self,
        entities: tuple[PublicationEntity, ...],
    ) -> tuple[TargetObservation, ...]:
        self.reconcile_sizes.append(len(entities))
        return tuple(TargetObservation.absent(entity.entity_key) for entity in entities)

    async def fetch_entity(self, qid: str) -> RemoteEntitySnapshot | None:
        return RemoteEntitySnapshot(
            qid=qid,
            revision=self.remote_revision,
            fingerprint=f"remote-{self.remote_revision}",
        )

    async def write_once(self, request: GatewayWriteRequest) -> str:
        self.write_calls.append(request)
        if self.write_error is not None:
            raise self.write_error
        return request.mutation.target_qid or "Q9001"

    async def confirm_mutation(
        self,
        mutation,
        *,
        qid: str | None,
    ) -> MutationConfirmation:
        return MutationConfirmation.applied(
            qid=qid or "Q9001",
            revision=self.remote_revision + 1,
            fingerprint="remote-after",
        )


@dataclass
class _BoundaryFactory:
    boundary: _Boundary
    opened: list[CredentialMaterial]

    async def open(self, credential: CredentialMaterial) -> _Boundary:
        self.opened.append(credential)
        return self.boundary


def _entity(index: int = 1) -> PublicationEntity:
    return PublicationEntity(
        release_id="release-1",
        entity_key=f"manuscript:{index}",
        entity_type="manuscript",
        entity_digest=str(index).zfill(64),
        document={
            "labels": {"en": f"Manuscript {index}"},
            "statements": [],
        },
        evidence_refs=(),
        identity_assertions=(f"P217:shelf-{index}",),
        local_references=(),
    )


def _gateway(boundary: _Boundary) -> WikidataGatewayAdapter:
    target = TargetRef(site="www.wikidata.org", environment="production")
    material = CredentialMaterial(
        credential_id="credential:curator-1:live",
        target=target,
        secret="test-only-secret",
    )
    return WikidataGatewayAdapter(
        credential_resolver=_Resolver(material),
        boundary_factory=_BoundaryFactory(boundary, []),
    )


@pytest.mark.asyncio
async def test_gateway_binds_credentials_and_reconciles_in_batches_of_50() -> None:
    boundary = _Boundary()
    gateway = _gateway(boundary)
    target = TargetRef(site="www.wikidata.org", environment="production")
    session = await gateway.open("credential:curator-1:live", target)

    observations = await session.reconcile_batch(tuple(_entity(i) for i in range(1, 52)))

    assert len(observations) == 51
    assert boundary.reconcile_sizes == [50, 1]
    with pytest.raises(CredentialTargetMismatchError):
        await gateway.open(
            "credential:curator-1:live",
            TargetRef(site="test.wikidata.org", environment="test"),
        )


@pytest.mark.asyncio
async def test_gateway_checks_remote_revision_and_reads_back_after_write() -> None:
    boundary = _Boundary()
    gateway = _gateway(boundary)
    session = await gateway.open(
        "credential:curator-1:live",
        TargetRef(site="www.wikidata.org", environment="production"),
    )
    mutation = await session.compile_mutation(
        _entity(),
        action="update",
        target_qid="Q700",
        expected_revision=17,
    )
    request = GatewayWriteRequest(
        intent_id="intent-1",
        request_key="execution-1:manuscript-1:1",
        mutation=mutation,
    )

    outcome = await session.write(request)

    assert outcome.status == "succeeded"
    assert outcome.qid == "Q700"
    assert outcome.fingerprint == "remote-after"
    assert len(boundary.write_calls) == 1

    boundary.remote_revision = 18
    stale = await session.write(request)
    assert stale.status == "blocked"
    assert "revision" in (stale.detail or "").lower()
    assert len(boundary.write_calls) == 1


@pytest.mark.asyncio
async def test_gateway_classifies_maxlag_as_pre_send_retryable() -> None:
    boundary = _Boundary()
    boundary.write_error = WikidataMaxlagError(retry_after_seconds=12)
    gateway = _gateway(boundary)
    session = await gateway.open(
        "credential:curator-1:live",
        TargetRef(site="www.wikidata.org", environment="production"),
    )
    mutation = await session.compile_mutation(
        _entity(),
        action="update",
        target_qid="Q700",
        expected_revision=17,
    )

    outcome = await session.write(
        GatewayWriteRequest(
            intent_id="intent-1",
            request_key="execution-1:manuscript-1:1",
            mutation=mutation,
        )
    )

    assert outcome.status == "pre_send_retryable"
    assert "12" in (outcome.detail or "")
