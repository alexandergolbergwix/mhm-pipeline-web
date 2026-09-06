"""Production adapter for the Wikidata publication gateway seam."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast

from app.publication.digests import canonical_digest, thaw_json
from app.publication.gateway import (
    GatewayMutation,
    GatewayRecoveryRequest,
    GatewayWriteRequest,
    TargetObservation,
    WikidataGatewaySession,
    WriteOutcome,
)
from app.publication.types import PublicationEntity, TargetRef


class _WritableItem(Protocol):
    def write(self, **kwargs: object) -> object: ...


class _CurrentUploader(Protocol):
    _mark_as_bot: bool

    def ensure_authenticated(self) -> None: ...

    def _is_our_item(self, qid: str) -> bool: ...

    def _wbgetentities(self, ids: list[str], *, props: str) -> dict[str, dict]: ...

    def register_foreign_accept(self, qid: str) -> None: ...

    def _check_moratorium_for_live(self) -> None: ...

    def _build_wbi_item(self, item: object) -> tuple[_WritableItem, int, list[str]]: ...

    def _assert_modifiable(self, qid: str, *, stage: str) -> None: ...


class CredentialTargetMismatchError(ValueError):
    """The credential belongs to a different Wikidata target."""


class WikidataGatewayError(RuntimeError):
    """A Wikidata boundary operation failed."""


class WikidataPreSendRetryableError(WikidataGatewayError):
    """Wikidata rejected or failed the request before it could apply."""


class WikidataMaxlagError(WikidataPreSendRetryableError):
    def __init__(self, *, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Wikidata reported maxlag; retry after {retry_after_seconds:g} seconds")


class WikidataRateLimitError(WikidataPreSendRetryableError):
    def __init__(self, *, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Wikidata rejected the request for rate limits; retry after "
            f"{retry_after_seconds:g} seconds"
        )


class WikidataOutcomeUnknownError(WikidataGatewayError):
    """The connection failed after the write might have reached Wikidata."""


class WikidataRevisionConflictError(WikidataGatewayError):
    """The remote entity changed after the Plan was sealed."""


class WikidataAuthenticationError(WikidataGatewayError):
    """The target rejected the configured credential."""


@dataclass(frozen=True, slots=True)
class CredentialMaterial:
    credential_id: str
    target: TargetRef
    secret: str = field(repr=False)


class TargetCredentialResolver(Protocol):
    async def resolve(self, credential_ref: str) -> CredentialMaterial: ...


@dataclass(frozen=True, slots=True)
class RemoteEntitySnapshot:
    qid: str
    revision: int
    fingerprint: str
    document: Mapping[str, object] | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class MutationConfirmation:
    status: Literal["applied", "not_applied", "unknown"]
    qid: str | None = None
    revision: int | None = None
    fingerprint: str | None = None
    detail: str | None = None

    @classmethod
    def applied(
        cls,
        *,
        qid: str,
        revision: int,
        fingerprint: str,
    ) -> MutationConfirmation:
        return cls(
            status="applied",
            qid=qid,
            revision=revision,
            fingerprint=fingerprint,
        )

    @classmethod
    def not_applied(cls, detail: str) -> MutationConfirmation:
        return cls(status="not_applied", detail=detail)

    @classmethod
    def unknown(cls, detail: str) -> MutationConfirmation:
        return cls(status="unknown", detail=detail)


class WikidataBoundary(Protocol):
    """The true external seam. Tests replace only this protocol."""

    async def reconcile_batch(
        self,
        entities: tuple[PublicationEntity, ...],
    ) -> tuple[TargetObservation, ...]: ...

    async def fetch_entity(self, qid: str) -> RemoteEntitySnapshot | None: ...

    async def write_once(self, request: GatewayWriteRequest) -> str: ...

    async def confirm_mutation(
        self,
        mutation: GatewayMutation,
        *,
        qid: str | None,
    ) -> MutationConfirmation: ...


class WikidataBoundaryFactory(Protocol):
    async def open(self, credential: CredentialMaterial) -> WikidataBoundary: ...


class WikidataGatewayAdapter:
    """Bind credentials and enforce write safety around one boundary session."""

    def __init__(
        self,
        *,
        credential_resolver: TargetCredentialResolver,
        boundary_factory: WikidataBoundaryFactory,
    ) -> None:
        self._credential_resolver = credential_resolver
        self._boundary_factory = boundary_factory

    async def open(
        self,
        credential_ref: str,
        target: TargetRef,
    ) -> WikidataGatewaySession:
        credential = await self._credential_resolver.resolve(credential_ref)
        if credential.credential_id != credential_ref or credential.target != target:
            raise CredentialTargetMismatchError(
                "The credential reference does not match the Publication target"
            )
        boundary = await self._boundary_factory.open(credential)
        return _SafeWikidataGatewaySession(boundary)


class _SafeWikidataGatewaySession:
    def __init__(self, boundary: WikidataBoundary) -> None:
        self._boundary = boundary

    async def reconcile_batch(
        self,
        entities: tuple[PublicationEntity, ...],
    ) -> tuple[TargetObservation, ...]:
        observations: list[TargetObservation] = []
        for start in range(0, len(entities), 50):
            observations.extend(await self._boundary.reconcile_batch(entities[start : start + 50]))
        return tuple(observations)

    async def compile_mutation(
        self,
        entity: PublicationEntity,
        *,
        action: Literal["create", "update"],
        target_qid: str | None,
        expected_revision: int | None = None,
        allow_foreign_update: bool = False,
    ) -> GatewayMutation:
        payload_digest = canonical_digest(
            {
                "schema": "wikidata-publication-mutation-v1",
                "entity_digest": entity.entity_digest,
                "action": action,
                "target_qid": target_qid,
                "expected_revision": expected_revision,
                "allow_foreign_update": allow_foreign_update,
            }
        )
        return GatewayMutation(
            entity_key=entity.entity_key,
            entity_type=entity.entity_type,
            action=action,
            target_qid=target_qid,
            payload_digest=payload_digest,
            entity_digest=entity.entity_digest,
            document=entity.document,
            identity_assertions=entity.identity_assertions,
            expected_revision=expected_revision,
            allow_foreign_update=allow_foreign_update,
        )

    async def write(self, request: GatewayWriteRequest) -> WriteOutcome:
        mutation = request.mutation
        preflight = await self._preflight(mutation)
        if preflight is not None:
            return preflight
        try:
            qid = await self._boundary.write_once(request)
        except WikidataPreSendRetryableError as exc:
            return WriteOutcome.pre_send_retryable(str(exc))
        except WikidataRevisionConflictError as exc:
            return WriteOutcome.blocked(str(exc))
        except WikidataAuthenticationError as exc:
            return WriteOutcome.blocked(str(exc))
        except WikidataOutcomeUnknownError as exc:
            return WriteOutcome.outcome_unknown(str(exc))
        except Exception as exc:  # noqa: BLE001
            return WriteOutcome.outcome_unknown(str(exc))
        confirmation = await self._boundary.confirm_mutation(mutation, qid=qid)
        return self._confirmation_outcome(confirmation, after_write=True)

    async def recover_ambiguous(
        self,
        request: GatewayRecoveryRequest,
    ) -> WriteOutcome:
        confirmation = await self._boundary.confirm_mutation(
            request.mutation,
            qid=request.mutation.target_qid,
        )
        return self._confirmation_outcome(confirmation, after_write=False)

    async def _preflight(self, mutation: GatewayMutation) -> WriteOutcome | None:
        if mutation.action == "create":
            entity = _publication_entity_from_mutation(mutation)
            try:
                observations = await self._boundary.reconcile_batch((entity,))
            except WikidataPreSendRetryableError as exc:
                return WriteOutcome.pre_send_retryable(str(exc))
            if len(observations) != 1 or observations[0].status == "unknown":
                return WriteOutcome.pre_send_retryable(
                    "The final CREATE identity check is inconclusive"
                )
            if observations[0].status != "absent":
                return WriteOutcome.blocked(
                    f"The final CREATE identity check found {observations[0].qid or 'a target'}"
                )
            return None
        if mutation.target_qid is None or mutation.expected_revision is None:
            return WriteOutcome.blocked("An UPDATE requires a target QID and remote revision")
        try:
            snapshot = await self._boundary.fetch_entity(mutation.target_qid)
        except WikidataPreSendRetryableError as exc:
            return WriteOutcome.pre_send_retryable(str(exc))
        if snapshot is None:
            return WriteOutcome.blocked("The planned UPDATE target no longer exists")
        if snapshot.revision != mutation.expected_revision:
            return WriteOutcome.blocked(
                f"The remote revision changed from {mutation.expected_revision} "
                f"to {snapshot.revision}"
            )
        return None

    @staticmethod
    def _confirmation_outcome(
        confirmation: MutationConfirmation,
        *,
        after_write: bool,
    ) -> WriteOutcome:
        if (
            confirmation.status == "applied"
            and confirmation.qid is not None
            and confirmation.fingerprint is not None
        ):
            return WriteOutcome.succeeded(
                qid=confirmation.qid,
                fingerprint=confirmation.fingerprint,
            )
        if confirmation.status == "not_applied" and not after_write:
            return WriteOutcome.confirmed_not_applied(
                confirmation.detail or "The mutation is not present on Wikidata"
            )
        return WriteOutcome.outcome_unknown(
            confirmation.detail or "The post-write read-back could not confirm the mutation"
        )


class CurrentWikidataBoundaryFactory:
    """Create a boundary around the current reconciler and uploader."""

    async def open(self, credential: CredentialMaterial) -> WikidataBoundary:
        return await asyncio.to_thread(self._open_sync, credential)

    @staticmethod
    def _open_sync(credential: CredentialMaterial) -> CurrentWikidataBoundary:
        supported = {
            ("www.wikidata.org", "production"): False,
            ("test.wikidata.org", "test"): True,
        }
        key = (credential.target.site, credential.target.environment)
        if key not in supported:
            raise CredentialTargetMismatchError("The Wikidata target is not supported")
        from converter.wikidata.uploader import WikidataUploader  # noqa: PLC0415

        uploader = WikidataUploader(
            token=credential.secret,
            is_test=supported[key],
            batch_mode=True,
            allow_live=not supported[key],
        )
        uploader.ensure_authenticated()
        return CurrentWikidataBoundary(uploader=uploader)


class CurrentWikidataBoundary:
    """Use the current native reconciler and uploader for one target."""

    def __init__(self, *, uploader: _CurrentUploader) -> None:
        self._uploader = uploader

    async def reconcile_batch(
        self,
        entities: tuple[PublicationEntity, ...],
    ) -> tuple[TargetObservation, ...]:
        if len(entities) > 50:
            raise ValueError("A Wikidata reconciliation batch cannot exceed 50 entities")
        return await asyncio.to_thread(self._reconcile_sync, entities)

    async def fetch_entity(self, qid: str) -> RemoteEntitySnapshot | None:
        return await asyncio.to_thread(self._fetch_entity_sync, qid)

    async def write_once(self, request: GatewayWriteRequest) -> str:
        return await asyncio.to_thread(self._write_once_sync, request)

    async def confirm_mutation(
        self,
        mutation: GatewayMutation,
        *,
        qid: str | None,
    ) -> MutationConfirmation:
        return await asyncio.to_thread(self._confirm_mutation_sync, mutation, qid)

    def _reconcile_sync(
        self,
        entities: tuple[PublicationEntity, ...],
    ) -> tuple[TargetObservation, ...]:
        from app.pipeline.wikidata_upload import _reconcile_sync  # noqa: PLC0415

        items = [self._native_item(entity) for entity in entities]
        outcomes = _reconcile_sync(items)
        observations: list[TargetObservation] = []
        for entity, outcome in zip(entities, outcomes, strict=True):
            if outcome.method == "error":
                observations.append(TargetObservation.unknown(entity.entity_key, outcome.message))
                continue
            if not outcome.existing_qid:
                observations.append(TargetObservation.absent(entity.entity_key))
                continue
            snapshot = self._fetch_entity_sync(outcome.existing_qid)
            if snapshot is None:
                observations.append(
                    TargetObservation.unknown(
                        entity.entity_key,
                        f"The reconciled target {outcome.existing_qid} is absent",
                    )
                )
                continue
            is_ours = self._uploader._is_our_item(snapshot.qid)
            constructor = (
                TargetObservation.present_owned
                if is_ours
                else TargetObservation.present_foreign
            )
            observations.append(
                constructor(
                    entity.entity_key,
                    qid=snapshot.qid,
                    fingerprint=snapshot.fingerprint,
                    remote_revision=snapshot.revision,
                )
            )
        return tuple(observations)

    def _fetch_entity_sync(self, qid: str) -> RemoteEntitySnapshot | None:
        payload = self._uploader._wbgetentities(
            [qid],
            props="info|labels|descriptions|aliases|claims",
        )
        if qid not in payload:
            raise WikidataPreSendRetryableError(
                f"The Wikidata read for {qid} did not return a result"
            )
        entity = payload[qid]
        if not isinstance(entity, Mapping) or "missing" in entity:
            return None
        revision = entity.get("lastrevid")
        if not isinstance(revision, int):
            raise WikidataPreSendRetryableError(
                f"The Wikidata read for {qid} omitted the remote revision"
            )
        return RemoteEntitySnapshot(
            qid=qid,
            revision=revision,
            fingerprint=canonical_digest(entity),
            document=entity,
        )

    def _write_once_sync(self, request: GatewayWriteRequest) -> str:
        mutation = request.mutation
        try:
            item = self._native_item_from_mutation(mutation)
            if mutation.allow_foreign_update and mutation.target_qid:
                self._uploader.register_foreign_accept(mutation.target_qid)
            self._uploader._check_moratorium_for_live()
            wbi_item, _, _ = self._uploader._build_wbi_item(item)
            self._uploader._assert_modifiable(
                mutation.target_qid or "",
                stage="publication_pre_write",
            )
            kwargs: dict[str, object] = {
                "summary": (
                    f"MHM Pipeline publication; local_id={mutation.entity_key}; "
                    f"request={request.request_key}"
                )[:500],
                "is_bot": self._uploader._mark_as_bot,
            }
            if mutation.expected_revision is not None:
                kwargs["baserevid"] = mutation.expected_revision
            result = wbi_item.write(**kwargs)
            qid = str(getattr(result, "id", "") or mutation.target_qid or "")
            if not qid:
                raise WikidataOutcomeUnknownError(
                    "Wikidata returned no QID after the write request"
                )
            return qid
        except WikidataGatewayError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _classify_write_exception(exc) from exc

    def _confirm_mutation_sync(
        self,
        mutation: GatewayMutation,
        qid: str | None,
    ) -> MutationConfirmation:
        resolved_qid = qid
        if resolved_qid is None and mutation.action == "create":
            observations = self._reconcile_sync((self._entity_from_mutation(mutation),))
            observation = observations[0]
            if observation.status == "absent":
                return MutationConfirmation.not_applied(
                    "The strong identity lookup confirms that CREATE did not apply"
                )
            if observation.status == "unknown" or observation.qid is None:
                return MutationConfirmation.unknown(
                    observation.detail or "The CREATE recovery lookup is inconclusive"
                )
            resolved_qid = observation.qid
        if resolved_qid is None:
            return MutationConfirmation.unknown("The mutation has no target QID")
        try:
            snapshot = self._fetch_entity_sync(resolved_qid)
            if snapshot is None:
                return MutationConfirmation.not_applied("The target QID is absent")
            item = self._native_item_from_mutation(mutation, target_qid=resolved_qid)
            if mutation.allow_foreign_update:
                self._uploader.register_foreign_accept(resolved_qid)
            _, new_claims, _ = self._uploader._build_wbi_item(item)
            if new_claims:
                return MutationConfirmation.not_applied(
                    "The read-back still lacks claims from the planned mutation"
                )
            return MutationConfirmation.applied(
                qid=resolved_qid,
                revision=snapshot.revision,
                fingerprint=snapshot.fingerprint,
            )
        except WikidataPreSendRetryableError as exc:
            return MutationConfirmation.unknown(str(exc))
        except Exception as exc:  # noqa: BLE001
            return MutationConfirmation.unknown(str(exc))

    @staticmethod
    def _entity_from_mutation(mutation: GatewayMutation) -> PublicationEntity:
        return _publication_entity_from_mutation(mutation)

    def _native_item_from_mutation(
        self,
        mutation: GatewayMutation,
        *,
        target_qid: str | None = None,
    ) -> object:
        entity = self._entity_from_mutation(mutation)
        return self._native_item(entity, target_qid=target_qid or mutation.target_qid)

    @staticmethod
    def _native_item(
        entity: PublicationEntity,
        *,
        target_qid: str | None = None,
    ) -> object:
        from converter.wikidata.item_models import (  # noqa: PLC0415
            WikidataItem,
            WikidataStatement,
        )

        document = thaw_json(entity.document)
        if not isinstance(document, dict):
            raise ValueError("A publication entity document must be an object")
        raw_statements = document.get("statements") or document.get("claims") or []
        if not isinstance(raw_statements, Sequence) or isinstance(raw_statements, str):
            raise ValueError("The publication entity statements must be a list")
        statements = []
        for raw in raw_statements:
            if not isinstance(raw, dict):
                raise ValueError("A publication statement must be an object")
            property_id = str(raw.get("property_id") or raw.get("property") or "")
            if not property_id:
                raise ValueError("A publication statement requires a property ID")
            statements.append(
                WikidataStatement(
                    property_id=property_id,
                    value=cast("object", raw.get("value")),
                    value_type=str(raw.get("value_type") or raw.get("datatype") or "string"),
                    qualifiers=_dict_list(raw.get("qualifiers")),
                    references=cast("list[dict[str, str]]", _dict_list(raw.get("references"))),
                    precision=int(raw.get("precision") or 9),
                    language=str(raw.get("language") or "he"),
                    unit=str(raw.get("unit") or ""),
                    rank=str(raw.get("rank") or "normal"),
                )
            )
        return WikidataItem(
            labels=_string_map(document.get("labels")),
            descriptions=_string_map(document.get("descriptions")),
            aliases=_aliases(document.get("aliases")),
            statements=statements,
            existing_qid=target_qid,
            entity_type=entity.entity_type,
            local_id=entity.entity_key,
        )


def _publication_entity_from_mutation(mutation: GatewayMutation) -> PublicationEntity:
    return PublicationEntity(
        release_id="recovery",
        entity_key=mutation.entity_key,
        entity_type=mutation.entity_type,
        entity_digest=mutation.entity_digest,
        document=mutation.document,
        evidence_refs=(),
        identity_assertions=mutation.identity_assertions,
        local_references=(),
    )


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _string_map(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None}


def _aliases(value: object) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): [str(item) for item in items]
        for key, items in value.items()
        if isinstance(items, Sequence) and not isinstance(items, (str, bytes))
    }


def _classify_write_exception(exc: Exception) -> WikidataGatewayError:
    message = str(exc)
    lowered = message.lower()
    code = str(getattr(exc, "code", "") or "").lower()
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    headers = getattr(response, "headers", {}) or {}
    retry_after = _retry_after_seconds(headers.get("Retry-After"), default=5.0)
    if code == "maxlag" or "maxlag" in lowered:
        return WikidataMaxlagError(retry_after_seconds=retry_after)
    if status_code == 429 or code in {"ratelimited", "actionthrottledtext"}:
        return WikidataRateLimitError(retry_after_seconds=retry_after)
    if code == "editconflict" or "editconflict" in lowered:
        return WikidataRevisionConflictError(message)
    if code in {"badtoken", "notloggedin", "assertuserfailed", "permissiondenied"}:
        return WikidataAuthenticationError(message)
    return WikidataOutcomeUnknownError(message)


def _retry_after_seconds(value: object, *, default: float) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default
