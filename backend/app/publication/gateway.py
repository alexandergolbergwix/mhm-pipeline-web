"""The only seam between publication logic and public Wikidata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from app.publication.types import JsonValue, PublicationEntity, TargetRef


@dataclass(frozen=True, slots=True)
class TargetObservation:
    entity_key: str
    status: Literal["absent", "present_owned", "present_foreign", "unknown"]
    qid: str | None = None
    fingerprint: str | None = None
    remote_revision: int | None = None
    detail: str | None = None

    @classmethod
    def absent(cls, entity_key: str) -> TargetObservation:
        return cls(entity_key=entity_key, status="absent")

    @classmethod
    def present_owned(
        cls,
        entity_key: str,
        *,
        qid: str,
        fingerprint: str | None = None,
        remote_revision: int | None = None,
    ) -> TargetObservation:
        return cls(
            entity_key=entity_key,
            status="present_owned",
            qid=qid,
            fingerprint=fingerprint,
            remote_revision=remote_revision,
        )

    @classmethod
    def present_foreign(
        cls,
        entity_key: str,
        *,
        qid: str,
        fingerprint: str | None = None,
        remote_revision: int | None = None,
    ) -> TargetObservation:
        return cls(
            entity_key=entity_key,
            status="present_foreign",
            qid=qid,
            fingerprint=fingerprint,
            remote_revision=remote_revision,
        )

    @classmethod
    def unknown(cls, entity_key: str, detail: str) -> TargetObservation:
        return cls(entity_key=entity_key, status="unknown", detail=detail)


@dataclass(frozen=True, slots=True)
class GatewayMutation:
    entity_key: str
    entity_type: str
    action: Literal["create", "update"]
    target_qid: str | None
    payload_digest: str
    entity_digest: str
    document: JsonValue
    identity_assertions: tuple[str, ...]
    expected_revision: int | None = None
    allow_foreign_update: bool = False


@dataclass(frozen=True, slots=True)
class GatewayWriteRequest:
    intent_id: str
    request_key: str
    mutation: GatewayMutation


@dataclass(frozen=True, slots=True)
class GatewayRecoveryRequest:
    intent_id: str
    request_key: str
    mutation: GatewayMutation


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    status: Literal[
        "succeeded",
        "pre_send_retryable",
        "outcome_unknown",
        "blocked",
        "confirmed_not_applied",
    ]
    qid: str | None = None
    fingerprint: str | None = None
    detail: str | None = None

    @classmethod
    def succeeded(cls, *, qid: str, fingerprint: str) -> WriteOutcome:
        return cls(status="succeeded", qid=qid, fingerprint=fingerprint)

    @classmethod
    def pre_send_retryable(cls, detail: str) -> WriteOutcome:
        return cls(status="pre_send_retryable", detail=detail)

    @classmethod
    def outcome_unknown(cls, detail: str) -> WriteOutcome:
        return cls(status="outcome_unknown", detail=detail)

    @classmethod
    def blocked(cls, detail: str) -> WriteOutcome:
        return cls(status="blocked", detail=detail)

    @classmethod
    def confirmed_not_applied(cls, detail: str) -> WriteOutcome:
        return cls(status="confirmed_not_applied", detail=detail)


class WikidataGatewaySession(Protocol):
    async def reconcile_batch(
        self,
        entities: tuple[PublicationEntity, ...],
    ) -> tuple[TargetObservation, ...]: ...

    async def compile_mutation(
        self,
        entity: PublicationEntity,
        *,
        action: Literal["create", "update"],
        target_qid: str | None,
        expected_revision: int | None = None,
        allow_foreign_update: bool = False,
    ) -> GatewayMutation: ...

    async def write(self, request: GatewayWriteRequest) -> WriteOutcome: ...

    async def recover_ambiguous(
        self,
        request: GatewayRecoveryRequest,
    ) -> WriteOutcome: ...


class WikidataGateway(Protocol):
    async def open(
        self,
        credential_ref: str,
        target: TargetRef,
    ) -> WikidataGatewaySession: ...


class FakeWikidataGateway:
    """Provide deterministic observations without any network or writes."""

    def __init__(
        self,
        *,
        observations: dict[str, TargetObservation] | None = None,
        write_outcomes: dict[str, tuple[WriteOutcome, ...]] | None = None,
        recovery_outcomes: dict[str, WriteOutcome] | None = None,
    ) -> None:
        self._observations = observations or {}
        self._write_outcomes = {
            key: list(outcomes) for key, outcomes in (write_outcomes or {}).items()
        }
        self._recovery_outcomes = recovery_outcomes or {}
        self.open_count = 0
        self._write_calls: list[GatewayWriteRequest] = []
        self._recovery_calls: list[GatewayRecoveryRequest] = []

    @property
    def write_calls(self) -> tuple[GatewayWriteRequest, ...]:
        return tuple(self._write_calls)

    @property
    def recovery_calls(self) -> tuple[GatewayRecoveryRequest, ...]:
        return tuple(self._recovery_calls)

    async def open(
        self,
        credential_ref: str,
        target: TargetRef | None = None,
    ) -> FakeWikidataGateway:
        if not credential_ref:
            raise ValueError("A credential reference is required")
        del target
        self.open_count += 1
        return self

    async def reconcile_batch(
        self,
        entities: tuple[PublicationEntity, ...],
    ) -> tuple[TargetObservation, ...]:
        return tuple(
            self._observations.get(
                entity.entity_key,
                TargetObservation.unknown(
                    entity.entity_key,
                    "The fake gateway has no observation",
                ),
            )
            for entity in entities
        )

    async def compile_mutation(
        self,
        entity: PublicationEntity,
        *,
        action: Literal["create", "update"],
        target_qid: str | None,
        expected_revision: int | None = None,
        allow_foreign_update: bool = False,
    ) -> GatewayMutation:
        from app.publication.digests import canonical_digest

        return GatewayMutation(
            entity_key=entity.entity_key,
            entity_type=entity.entity_type,
            action=action,
            target_qid=target_qid,
            payload_digest=canonical_digest(
                {
                    "entity_digest": entity.entity_digest,
                    "action": action,
                    "target_qid": target_qid,
                }
            ),
            entity_digest=entity.entity_digest,
            document=entity.document,
            identity_assertions=entity.identity_assertions,
            expected_revision=expected_revision,
            allow_foreign_update=allow_foreign_update,
        )

    async def write(self, request: GatewayWriteRequest) -> WriteOutcome:
        self._write_calls.append(request)
        outcomes = self._write_outcomes.get(request.mutation.entity_key)
        if outcomes:
            return outcomes.pop(0)
        return WriteOutcome.succeeded(
            qid=request.mutation.target_qid or "QFAKE",
            fingerprint="fake-target-v1",
        )

    async def recover_ambiguous(
        self,
        request: GatewayRecoveryRequest,
    ) -> WriteOutcome:
        self._recovery_calls.append(request)
        return self._recovery_outcomes.get(
            request.mutation.entity_key,
            WriteOutcome.outcome_unknown("The fake recovery result is unknown"),
        )
