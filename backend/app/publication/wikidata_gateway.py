"""Production adapter for the Wikidata publication gateway seam."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Protocol, cast

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

logger = logging.getLogger(__name__)

# A wedged execution worker is always a call that never returned: WBI's
# synchronous HTTP has no timeout of its own, so one hung write parked a
# worker forever while its heartbeat kept the job "running" (2026-10-17,
# execution 9d18c5c3: claimable rows sat unclaimed behind 13 stale
# leases). Every boundary call gets an outer time budget; a breach is an
# outcome the durable claim queue can retry, never a silent stall.
_RECONCILE_TIMEOUT_S = 300.0
_FETCH_TIMEOUT_S = 120.0
_WRITE_TIMEOUT_S = 600.0
_CONFIRM_TIMEOUT_S = 300.0


class _WritableItem(Protocol):
    def write(self, **kwargs: object) -> object: ...


class _CurrentUploader(Protocol):
    _mark_as_bot: bool
    _is_test: bool
    _foreign_accept_qids: set[str]
    _is_our_item_cache: dict[str, bool]

    def ensure_authenticated(self) -> None: ...

    def _is_our_item(self, qid: str, *, our_p3959: str | None = None) -> bool: ...

    def _get_authenticated_user(self) -> str | None: ...

    def _get_first_revision_author(self, qid: str) -> str | None: ...

    def _wbgetentities(self, ids: list[str], *, props: str) -> dict[str, dict]: ...

    def register_foreign_accept(self, qid: str) -> None: ...

    def register_test_ledger_target(self, item: object, ledger: Mapping[str, str]) -> str: ...

    def register_test_local_target(self, local_id: str, qid: str) -> str: ...

    def _check_moratorium_for_live(self) -> None: ...

    def _build_wbi_item(
        self,
        item: object,
        *,
        read_only: bool = False,
    ) -> tuple[_WritableItem, int, list[str]]: ...

    def _assert_modifiable(
        self,
        qid: str,
        *,
        stage: str,
        our_p3959: str | None = None,
    ) -> None: ...

    def _refresh_edit_token(self) -> None: ...


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


class WikidataCsrfError(WikidataPreSendRetryableError):
    """The edit token expired before MediaWiki accepted the request."""


class WikidataOutcomeUnknownError(WikidataGatewayError):
    """The connection failed after the write might have reached Wikidata."""


class WikidataRevisionConflictError(WikidataGatewayError):
    """The remote entity changed after the Plan was sealed."""


class WikidataAuthenticationError(WikidataGatewayError):
    """The target rejected the configured credential."""


class WikidataPermanentError(WikidataGatewayError):
    """The API rejected the write before applying it — retrying cannot help."""


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
    target_site: str | None = None


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
            observations.extend(await asyncio.wait_for(
                self._boundary.reconcile_batch(entities[start : start + 50]),
                timeout=_RECONCILE_TIMEOUT_S,
            ))
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
        from app.publication.gateway import validate_publication_references  # noqa: PLC0415

        validate_publication_references(thaw_json(entity.document))
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
        if request.authorize_send is None:
            return WriteOutcome.blocked("The Execution authorization check is missing")
        try:
            authorized = await request.authorize_send()
        except Exception:  # noqa: BLE001
            logger.exception("The Execution authorization check failed")
            return WriteOutcome.pre_send_retryable("The Execution authorization check failed")
        if not authorized:
            return WriteOutcome.blocked("The Execution approval or Plan changed before the write")
        try:
            qid = await asyncio.wait_for(
                self._boundary.write_once(request),
                timeout=_WRITE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return WriteOutcome.outcome_unknown(
                f"The Wikidata write exceeded its {int(_WRITE_TIMEOUT_S)} s time budget"
            )
        except WikidataPreSendRetryableError as exc:
            return WriteOutcome.pre_send_retryable(str(exc))
        except WikidataRevisionConflictError as exc:
            return WriteOutcome.blocked(str(exc))
        except WikidataAuthenticationError as exc:
            return WriteOutcome.blocked(str(exc))
        except WikidataPermanentError as exc:
            return WriteOutcome.blocked(str(exc))
        except WikidataOutcomeUnknownError as exc:
            return WriteOutcome.outcome_unknown(str(exc))
        except Exception as exc:  # noqa: BLE001
            return WriteOutcome.outcome_unknown(str(exc))
        try:
            confirmation = await asyncio.wait_for(
                self._boundary.confirm_mutation(mutation, qid=qid),
                timeout=_CONFIRM_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return WriteOutcome.outcome_unknown(
                "The write read-back exceeded its time budget"
            )
        return self._confirmation_outcome(confirmation, after_write=True)

    async def recover_ambiguous(
        self,
        request: GatewayRecoveryRequest,
    ) -> WriteOutcome:
        confirmation = await asyncio.wait_for(
            self._boundary.confirm_mutation(
                request.mutation,
                qid=request.mutation.target_qid,
            ),
            timeout=_CONFIRM_TIMEOUT_S,
        )
        return self._confirmation_outcome(confirmation, after_write=False)

    async def _preflight(
        self,
        mutation: GatewayMutation,
    ) -> WriteOutcome | None:
        if mutation.action == "create":
            entity = _publication_entity_from_mutation(mutation)
            try:
                observations = await asyncio.wait_for(
                    getattr(self._boundary, "reconcile_fresh_batch", self._boundary.reconcile_batch)((entity,)),
                    timeout=_RECONCILE_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                return WriteOutcome.pre_send_retryable(
                    "The final CREATE identity check exceeded its time budget"
                )
            except WikidataPreSendRetryableError as exc:
                return WriteOutcome.pre_send_retryable(str(exc))
            if len(observations) != 1 or observations[0].status == "unknown":
                return WriteOutcome.pre_send_retryable(
                    "The final CREATE identity check is inconclusive"
                )
            observation = observations[0]
            if observation.status == "absent":
                return None
            return WriteOutcome.blocked(
                f"The final CREATE identity check found {observation.qid or 'a target'}; create a fresh Plan"
            )
        if mutation.target_qid is None or mutation.expected_revision is None:
            return WriteOutcome.blocked(
                "An UPDATE requires a target QID and remote revision"
            )
        try:
            snapshot = await asyncio.wait_for(
                self._boundary.fetch_entity(mutation.target_qid),
                timeout=_FETCH_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return WriteOutcome.pre_send_retryable(
                "The UPDATE target read exceeded its time budget"
            )
        except WikidataPreSendRetryableError as exc:
            return WriteOutcome.pre_send_retryable(str(exc))
        if snapshot is None:
            return WriteOutcome.blocked("The planned UPDATE target no longer exists")
        if snapshot.revision != mutation.expected_revision:
            return WriteOutcome.blocked(
                f"The remote revision changed from {mutation.expected_revision} "
                f"to {snapshot.revision}"
            )
        if mutation.entity_type == "work":
            reason = _work_target_identity_error(_publication_entity_from_mutation(mutation), snapshot)
            if reason:
                return WriteOutcome.blocked(reason)
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
            return WriteOutcome.outcome_unknown(
                "The mutation is not visible, but the original request may still be active"
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

    def _creator_owned(self, qid: str) -> bool:
        user = self._uploader._get_authenticated_user()
        return bool(
            user
            and self._uploader._get_first_revision_author(qid) == user
            and self._uploader._is_our_item(qid)
        )

    def _register_test_target(self, item: object, mutation: GatewayMutation) -> None:
        """Bind sealed test-ledger QIDs, the target and its local references, before any build."""
        if not self._uploader._is_test:
            return
        document = thaw_json(mutation.document)
        if mutation.action == "update":
            self._register_test_update_target(item, mutation, document)
        self._register_test_local_targets(document)

    def _register_test_update_target(
        self, item: object, mutation: GatewayMutation, document: object,
    ) -> None:
        target_qid = mutation.target_qid
        marker = document.get("publication_target_qid") if isinstance(document, dict) else None
        if (not target_qid or not isinstance(marker, Mapping)
                or marker.get("site") != "test.wikidata.org"
                or marker.get("qid") != target_qid):
            raise WikidataPermanentError("The test UPDATE target lacks sealed test-ledger proof")
        from app.pipeline.wikidata_qid_ledger import ledger_key_for_item  # noqa: PLC0415

        try:
            self._uploader.register_test_ledger_target(
                item, {ledger_key_for_item(item, "wikidata-test"): target_qid},
            )
        except ValueError as exc:
            raise WikidataPermanentError(str(exc)) from exc

    def _register_test_local_targets(self, document: object) -> None:
        if not isinstance(document, dict):
            return
        targets = document.get("publication_local_targets")
        if targets is None:
            return
        if not isinstance(targets, Mapping):
            raise WikidataPermanentError("The test local target proof is invalid")
        for local_id, marker in targets.items():
            if not isinstance(marker, Mapping):
                raise WikidataPermanentError("The test local target lacks sealed test-ledger proof")
            qid = marker.get("qid")
            if marker.get("site") != "test.wikidata.org" or not isinstance(qid, str):
                raise WikidataPermanentError("The test local target lacks sealed test-ledger proof")
            try:
                self._uploader.register_test_local_target(local_id, qid)
            except ValueError as exc:
                raise WikidataPermanentError(str(exc)) from exc

    def _assert_publication_modifiable(self, mutation: GatewayMutation) -> None:
        qid = mutation.target_qid
        if not qid:
            return
        if not mutation.allow_foreign_update and not self._creator_owned(qid):
            from converter.wikidata.uploader import UnauthorisedModificationError  # noqa: PLC0415

            raise UnauthorisedModificationError(qid=qid, stage="publication_pre_write")
        self._uploader._assert_modifiable(qid, stage="publication_pre_write")

    async def reconcile_batch(
        self,
        entities: tuple[PublicationEntity, ...],
    ) -> tuple[TargetObservation, ...]:
        if len(entities) > 50:
            raise ValueError("A Wikidata reconciliation batch cannot exceed 50 entities")
        return await asyncio.to_thread(self._reconcile_sync, entities)

    async def reconcile_fresh_batch(
        self, entities: tuple[PublicationEntity, ...],
    ) -> tuple[TargetObservation, ...]:
        return await asyncio.to_thread(self._reconcile_sync, entities, fresh=True)

    async def fetch_entity(self, qid: str) -> RemoteEntitySnapshot | None:
        return await asyncio.to_thread(self._fetch_entity_sync, qid)

    async def write_once(self, request: GatewayWriteRequest) -> str:
        if request.authorize_send is None:
            raise WikidataPermanentError("The Execution authorization check is missing")
        loop = asyncio.get_running_loop()
        active = threading.Event()
        active.set()

        def authorize() -> None:
            if not active.is_set():
                raise WikidataPermanentError("The write request is no longer active")
            future = asyncio.run_coroutine_threadsafe(request.authorize_send(), loop)
            try:
                allowed = future.result(timeout=60)
            except FutureTimeoutError as exc:
                future.cancel()
                raise WikidataPreSendRetryableError("The Execution authorization check timed out") from exc
            except Exception as exc:
                raise WikidataPreSendRetryableError("The Execution authorization check failed") from exc
            if not allowed or not active.is_set():
                raise WikidataPermanentError("The Execution approval or Plan changed before the write")

        try:
            return await asyncio.to_thread(self._write_once_sync, request, authorize)
        finally:
            active.clear()

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
        *, fresh: bool = False,
    ) -> tuple[TargetObservation, ...]:
        from app.pipeline.wikidata_upload import _reconcile_sync  # noqa: PLC0415

        observations: list[TargetObservation | None] = [None] * len(entities)
        generic_entities: list[tuple[int, PublicationEntity]] = []
        for index, entity in enumerate(entities):
            if entity.entity_type == "work":
                try:
                    self._native_item(entity)
                except ValueError as exc:
                    observations[index] = TargetObservation.unknown(entity.entity_key, str(exc))
                    continue
            reference = self._reference_only_target(entity)
            if reference is None:
                document = entity.document
                raw_qid = document.get("existing_qid") if isinstance(document, Mapping) else None
                assertions = {value.removeprefix("wikidata:") for value in entity.identity_assertions
                    if value.startswith("wikidata:")}
                if raw_qid is not None and raw_qid != "":
                    assertions.add(str(raw_qid))
                if not assertions:
                    if self._uploader._is_test:
                        observations[index] = TargetObservation.unknown(
                            entity.entity_key,
                            "Target-specific test Wikidata reconciliation is unavailable",
                        )
                        continue
                    generic_entities.append((index, entity))
                    continue
                if len(assertions) != 1 or not re.fullmatch(r"Q[1-9][0-9]*", next(iter(assertions))):
                    observations[index] = TargetObservation.unknown(entity.entity_key, "The sealed QID is invalid or inconsistent")
                    continue
                qid = next(iter(assertions))
                marker = document.get("publication_target_qid") if isinstance(document, Mapping) else None
                if self._uploader._is_test and not (
                    isinstance(marker, Mapping)
                    and marker.get("site") == "test.wikidata.org"
                    and marker.get("qid") == qid
                ):
                    observations[index] = TargetObservation.unknown(entity.entity_key, "A production QID cannot identify a test-wiki item")
                    continue
                snapshot = self._fetch_entity_sync(qid)
                if snapshot is None:
                    observations[index] = TargetObservation.unknown(entity.entity_key, f"The sealed target {qid} is absent")
                    continue
                reason = _work_target_identity_error(entity, snapshot)
                if reason:
                    observations[index] = TargetObservation.unknown(entity.entity_key, reason)
                    continue
                constructor = TargetObservation.present_owned if self._creator_owned(qid) else TargetObservation.present_foreign
                observations[index] = constructor(entity.entity_key, qid=qid, fingerprint=snapshot.fingerprint,
                    remote_revision=snapshot.revision, method="sealed-qid")
                continue
            if isinstance(reference, str):
                observations[index] = TargetObservation.unknown(entity.entity_key, reference)
                continue
            qid, expected_revision = reference
            snapshot = self._fetch_entity_sync(qid)
            if snapshot is None:
                observations[index] = TargetObservation.unknown(
                    entity.entity_key,
                    f"The reference-only target {qid} is absent",
                )
                continue
            if snapshot.revision != expected_revision:
                observations[index] = TargetObservation.unknown(
                    entity.entity_key,
                    "The reference-only target revision changed. Review its identity again.",
                )
                continue
            reason = _work_target_identity_error(entity, snapshot)
            if reason:
                observations[index] = TargetObservation.unknown(entity.entity_key, reason)
                continue
            constructor = (
                TargetObservation.present_owned
                if self._creator_owned(snapshot.qid)
                else TargetObservation.present_foreign
            )
            observations[index] = constructor(
                entity.entity_key,
                qid=snapshot.qid,
                fingerprint=snapshot.fingerprint,
                remote_revision=snapshot.revision,
                method="reference-only",
            )

        items = [self._native_item(entity) for _, entity in generic_entities]
        if fresh and items:
            from app.pipeline.wikidata_upload import ReconcileOutcome, _reconcile_for_upload  # noqa: PLC0415
            from converter.wikidata.reconciler import WikidataReconciler  # noqa: PLC0415

            reconciler = WikidataReconciler()
            outcomes = []
            for item in items:
                try:
                    properties = {statement.property_id for statement in item.statements if statement.value}
                    probeable = (
                        item.entity_type == "person" and bool(properties & {"P214", "P8189", "P244", "P227", "P213"})
                        or item.entity_type == "manuscript" and bool(properties & {"P3959", "P217"})
                        or item.entity_type == "work" and (bool(item.labels.get("he") or item.labels.get("en")) or "P2888" in properties)
                    )
                    if not probeable:
                        raise ValueError("No supported duplicate check exists for this CREATE")
                    qid, method = _reconcile_for_upload(reconciler, item, item.entity_type)
                    if not qid and method == "source_scoped_work":
                        raise ValueError("The source-scoped work did not receive a duplicate check")
                    if not qid and item.entity_type == "work" and not properties.intersection({"P50", "P2093"}):
                        raise ValueError("A work without author evidence cannot prove duplicate absence")
                    outcomes.append(ReconcileOutcome(item.local_id, "", item.entity_type, qid, method, ""))
                except Exception as exc:  # noqa: BLE001
                    outcomes.append(ReconcileOutcome(item.local_id, "", item.entity_type, None, "error", str(exc)))
        else:
            outcomes = _reconcile_sync(items) if items else []
        for (index, entity), outcome in zip(
            generic_entities, outcomes, strict=True,
        ):
            if outcome.method == "error":
                observations[index] = TargetObservation.unknown(entity.entity_key, outcome.message)
                continue
            if not outcome.existing_qid:
                if outcome.method == "source_scoped_work":
                    observations[index] = TargetObservation.unknown(
                        entity.entity_key, "The source-scoped work did not receive a duplicate check",
                    )
                    continue
                observations[index] = TargetObservation.absent(
                    entity.entity_key,
                    method=outcome.method,
                )
                continue
            snapshot = self._fetch_entity_sync(outcome.existing_qid)
            if snapshot is None:
                if entity.entity_type == "work" and any(
                    isinstance(row, Mapping) and row.get("identity_scope") == "record"
                    for row in entity.document.get("work_candidate_evidence", ())
                ):
                    observations[index] = TargetObservation.unknown(
                        entity.entity_key, "The source-scoped target is unavailable for identity review",
                    )
                    continue
                observations[index] = TargetObservation.unknown(
                    entity.entity_key,
                    f"The reconciled target {outcome.existing_qid} is absent",
                )
                continue
            reason = _work_target_identity_error(entity, snapshot)
            if reason:
                observations[index] = TargetObservation.unknown(entity.entity_key, reason)
                continue
            is_ours = self._creator_owned(snapshot.qid)
            constructor = (
                TargetObservation.present_owned
                if is_ours
                else TargetObservation.present_foreign
            )
            observations[index] = constructor(
                entity.entity_key,
                qid=snapshot.qid,
                fingerprint=snapshot.fingerprint,
                remote_revision=snapshot.revision,
                method=outcome.method,
            )
        if any(observation is None for observation in observations):
            raise RuntimeError("The Wikidata reconciliation did not return every observation")
        return tuple(observation for observation in observations if observation is not None)

    @staticmethod
    def _reference_only_target(
        entity: PublicationEntity,
    ) -> tuple[str, int] | str | None:
        document = entity.document
        if not isinstance(document, Mapping):
            return "The reference-only document is invalid"
        reference = document.get("publication_reference_only")
        if reference is None:
            return None
        if not isinstance(reference, Mapping):
            return "The reference-only target is invalid"
        qid = reference.get("qid")
        revision = reference.get("remote_revision")
        if not isinstance(qid, str) or not qid:
            return "The reference-only target has no QID"
        if not isinstance(revision, int):
            return "The reference-only target has no remote revision"
        return qid, revision

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
            target_site="test.wikidata.org" if self._uploader._is_test else "www.wikidata.org",
        )

    def _write_once_sync(self, request: GatewayWriteRequest, authorize: Callable[[], None]) -> str:
        mutation = request.mutation
        try:
            authorize()
            if mutation.allow_foreign_update and (
                mutation.action != "update"
                or not mutation.target_qid
                or mutation.expected_revision is None
            ):
                raise WikidataPermanentError("Foreign consent requires an UPDATE target and revision")
            with self._foreign_accept(mutation):
                return self._write_authorized_sync(request, authorize)
        except WikidataGatewayError:
            raise
        except Exception as exc:  # noqa: BLE001
            from converter.wikidata.uploader import ClaimBuildError  # noqa: PLC0415

            if isinstance(exc, ClaimBuildError):
                raise WikidataPermanentError(str(exc)) from exc
            raise _classify_write_exception(exc) from exc

    @contextmanager
    def _foreign_accept(self, mutation: GatewayMutation) -> Iterator[None]:
        if not mutation.allow_foreign_update or not mutation.target_qid:
            yield
            return
        qid = mutation.target_qid
        self._uploader.register_foreign_accept(qid)
        try:
            yield
        finally:
            self._uploader._foreign_accept_qids.discard(qid)
            self._uploader._is_our_item_cache.pop(qid, None)

    def _write_authorized_sync(self, request: GatewayWriteRequest, authorize: Callable[[], None]) -> str:
        mutation = request.mutation
        # The uploader's internal build guard can use P3959. Check the
        # creator or this mutation's consent before that guard runs.
        self._assert_publication_modifiable(mutation)
        item = self._native_item_from_mutation(mutation)
        self._register_test_target(item, mutation)
        # Test Wikidata requires remapped claims and refuses incompatible snaks.
        if self._uploader._is_test:
            self._uploader._ensure_test_maps_for_item(item)
            item, adapt = self._uploader._adapt_item_for_test_wiki(item)
            if adapt.skipped:
                raise WikidataPermanentError(
                    f"{len(adapt.skipped)} incompatible snaks for "
                    f"test.wikidata.org: {'; '.join(adapt.skipped[:6])}"
                )
        self._uploader._check_moratorium_for_live()
        wbi_item, _, _ = self._uploader._build_wbi_item(item)
        kwargs: dict[str, object] = {
            "summary": (
                f"MHM Pipeline publication; local_id={mutation.entity_key}; "
                f"request={request.request_key}"
            )[:500],
            "is_bot": self._uploader._mark_as_bot,
            # WBI counts total attempts, so one prevents unguarded resends.
            "max_retries": 1,
            "allow_redirects": False,
            "retry_after": 15,
        }
        if mutation.expected_revision is not None:
            kwargs["baserevid"] = mutation.expected_revision
        result: object | None = None
        for write_attempt in range(2):
            authorize()
            try:
                result = wbi_item.write(**kwargs)
                break
            except Exception as exc:  # noqa: BLE001
                classified = _classify_write_exception(exc)
                if isinstance(classified, WikidataCsrfError) and write_attempt == 0:
                    self._uploader._refresh_edit_token()
                    continue
                if (
                    self._uploader._is_test
                    and mutation.action == "create"
                    and mutation.target_qid is None
                    and isinstance(classified, WikidataPermanentError)
                    and "already has label" in str(exc).lower()
                ):
                    raise WikidataPermanentError("The CREATE label already exists; create a fresh Plan") from exc
                raise classified from exc
        qid = str(getattr(result, "id", "") or mutation.target_qid or "")
        if not qid and mutation.action == "create":
            qid = str(getattr(item, "existing_qid", "") or "")
        if not qid:
            raise WikidataOutcomeUnknownError(
                "Wikidata returned no QID after the write request"
            )
        return qid

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
            reason = _work_target_identity_error(self._entity_from_mutation(mutation), snapshot)
            if reason:
                return MutationConfirmation.unknown(reason)
            item = self._native_item_from_mutation(mutation, target_qid=resolved_qid)
            self._register_test_target(item, mutation)
            # The read-back comparison must see the SAME claim shapes the
            # write applied: on test.wikidata.org that is the W-182 remap
            # (production P-numbers do not exist there with the same
            # datatypes). Comparing unadapted claims made every successful
            # write confirm as 'not applied' and churn forever
            # (2026-10-15: 868 outcome_unknown with 'lacks claims').
            if self._uploader._is_test:
                self._uploader._ensure_test_maps_for_item(item)
                item, _ = self._uploader._adapt_item_for_test_wiki(item)
            # CREATE confirmation only reads the item that the preceding
            # write returned. The creator checks can lag behind the write,
            # especially while Wikidata updates its SPARQL index. Keep the
            # guard on every mutation path, but do not require it for this
            # read-only claim comparison.
            if mutation.action == "create":
                _, new_claims, _ = self._uploader._build_wbi_item(item, read_only=True)
            else:
                if resolved_qid != mutation.target_qid:
                    return MutationConfirmation.unknown("The read-back QID does not match the mutation")
                with self._foreign_accept(mutation):
                    self._assert_publication_modifiable(mutation)
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
        from app.publication.gateway import validate_publication_references  # noqa: PLC0415

        validate_publication_references(thaw_json(mutation.document))
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
        from converter.wikidata.quickstatements import statement_reference_kwargs  # noqa: PLC0415

        document = thaw_json(entity.document)
        if not isinstance(document, dict):
            raise ValueError("A publication entity document must be an object")
        records = document.get("records", [])
        evidence = document.get("work_candidate_evidence", [])
        if entity.entity_type == "work":
            if "records" not in document or not isinstance(records, list) or any(
                not isinstance(record, str) or not record.strip() for record in records
            ):
                raise ValueError("The sealed work source records are missing or invalid")
            if "work_candidate_evidence" not in document or not isinstance(evidence, list) or any(
                not isinstance(row, dict)
                or ("identity_scope" in row and row["identity_scope"] != "record")
                for row in evidence
            ):
                raise ValueError("The sealed work source evidence is missing or invalid")
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
                    **statement_reference_kwargs(raw, WikidataStatement),
                    precision=int(raw.get("precision") or 9),
                    language=str(raw.get("language") or "he"),
                    unit=str(raw.get("unit") or ""),
                    rank=str(raw.get("rank") or "normal"),
                )
            )
        statements = _repair_legacy_statement_shapes(statements)
        return WikidataItem(
            labels=_string_map(document.get("labels")),
            descriptions=_string_map(document.get("descriptions")),
            aliases=_aliases(document.get("aliases")),
            statements=statements,
            existing_qid=target_qid,
            entity_type=entity.entity_type,
            local_id=entity.entity_key,
            records=records if isinstance(records, list) else [],
            work_candidate_evidence=evidence if isinstance(evidence, list) else [],
        )


def _work_target_identity_error(
    entity: PublicationEntity, snapshot: RemoteEntitySnapshot,
) -> str | None:
    if entity.entity_type != "work":
        return None
    from app.pipeline.wikidata_live_native_hygiene import WRITTEN_WORK_P31  # noqa: PLC0415
    from converter.wikidata.work_identity import (  # noqa: PLC0415
        compare_work_identity,
        work_identity_for_item,
        work_identity_from_entity,
    )

    try:
        if snapshot.target_site not in {None, "www.wikidata.org"}:
            return "The target work P31 cannot be verified on test Wikidata"
        if not isinstance(snapshot.document, Mapping) or not isinstance(
            snapshot.document.get("claims"), Mapping,
        ):
            raise ValueError("The target claims are unavailable")
        rows = snapshot.document["claims"].get("P31")
        if not isinstance(rows, list) or not rows:
            return "The target work P31 is missing or invalid"
        types = set()
        for row in rows:
            if not isinstance(row, Mapping):
                return "The target work P31 is invalid"
            rank = row.get("rank", "normal")
            if rank == "deprecated":
                continue
            if rank not in {"normal", "preferred"}:
                return "The target work P31 is invalid"
            snak = row.get("mainsnak")
            if (not isinstance(snak, Mapping) or snak.get("snaktype", "value") != "value"
                or snak.get("property", "P31") != "P31"
                or snak.get("datatype", "wikibase-item") != "wikibase-item"):
                return "The target work P31 is invalid"
            datavalue = snak.get("datavalue")
            value = datavalue.get("value") if isinstance(datavalue, Mapping) else None
            qid = value.get("id") if isinstance(value, Mapping) else None
            if (not isinstance(datavalue, Mapping)
                or datavalue.get("type", "wikibase-entityid") != "wikibase-entityid"
                or not isinstance(value, Mapping)
                or value.get("entity-type", "item") != "item"
                or not isinstance(qid, str) or not re.fullmatch(r"Q[1-9][0-9]*", qid)):
                return "The target work P31 is invalid"
            numeric_id = value.get("numeric-id")
            if numeric_id is not None and (
                type(numeric_id) is not int or f"Q{numeric_id}" != qid
            ):
                return "The target work P31 is invalid"
            types.add(qid)
        if not types or not types <= WRITTEN_WORK_P31:
            return "The target work P31 is not a verified written-work type"
        proposed = work_identity_for_item(CurrentWikidataBoundary._native_item(entity))
        existing = work_identity_from_entity(snapshot.document)
        if compare_work_identity(proposed, existing, strong_identifier=True) != "match":
            return "The target work has conflicting or unverified author/source evidence"
    except (ValueError, TypeError, AttributeError) as exc:
        return f"The work identity evidence is invalid: {exc}"
    return None


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


_QUANTITY_PROPERTY_IDS = frozenset({"P1104", "P2048", "P2049", "P2635"})
_STRUCTURED_VALUE_TYPES = frozenset(
    {"item", "quantity", "time", "url", "monolingualtext", "external-id"},
)


def _repair_legacy_statement_shapes(
    statements: list[Any],
) -> list[Any]:
    """Drop legacy string-typed twins and coerce numeric strings to quantities.

    Older builds carried a property twice: once with a plain string value
    and once with the properly typed statement (2026-10-16 live release:
    P1104 "2" string beside P1104 113 quantity on the same manuscript —
    the string twin failed every update with 'Bad value type string,
    expected quantity'). The structured statement is canonical; the string
    twin is dropped. A lone numeric string on a known quantity property is
    coerced so a stale shape still writes correctly.
    """
    by_property: dict[str, list[Any]] = {}
    for statement in statements:
        by_property.setdefault(statement.property_id, []).append(statement)

    repaired: list[Any] = []
    for statement in statements:
        group = by_property.get(statement.property_id) or []
        if (
            statement.value_type == "string"
            and any(
                other.value_type in _STRUCTURED_VALUE_TYPES
                for other in group
                if other is not statement
            )
        ):
            continue
        if (
            statement.value_type == "string"
            and statement.property_id in _QUANTITY_PROPERTY_IDS
            and str(statement.value).strip().replace(".", "", 1).isdigit()
        ):
            statement.value = str(statement.value).strip()
            statement.value_type = "quantity"
        repaired.append(statement)
    return repaired


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
    from requests.exceptions import HTTPError  # noqa: PLC0415
    from wikibaseintegrator.wbi_exceptions import MWApiError  # noqa: PLC0415

    message = str(exc)
    code = str(exc.code or "").lower() if isinstance(exc, MWApiError) else ""
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    headers = getattr(response, "headers", {}) or {}
    retry_after = _retry_after_seconds(headers.get("Retry-After"), default=5.0)
    if code == "maxlag":
        return WikidataMaxlagError(retry_after_seconds=retry_after)
    if (isinstance(exc, HTTPError) and status_code == 429) or code in {"ratelimited", "actionthrottledtext"}:
        return WikidataRateLimitError(retry_after_seconds=retry_after)
    if code == "editconflict":
        return WikidataRevisionConflictError(message)
    if code in {"badtoken", "invalidcsrf", "invalid_csrf"}:
        return WikidataCsrfError(message)
    if code in {"badtoken", "notloggedin", "assertuserfailed", "permissiondenied"}:
        return WikidataAuthenticationError(message)
    # Deterministic rejections: the API refused the request BEFORE applying
    # anything, so the outcome is known — the item can never be written as
    # shaped. Treating these as outcome_unknown made the execution retry the
    # same doomed write forever (2026-10-14: 1842/1847 churned on
    # 'Bad value type' + 'Label and description ... same value').
    if code in {"modification-failed", "failed-save", "invalidvalue"}:
        return WikidataPermanentError(message)
    from converter.wikidata.uploader import UnauthorisedModificationError  # noqa: PLC0415

    if isinstance(exc, UnauthorisedModificationError):
        # A creator-check refusal is a permanent ownership verdict — the
        # item is not ours (or the creator evidence is unavailable).
        # Churning it as unknown retried forever (2026-10-15: adoption
        # false-negatives from SPARQL hiccups looped the execution).
        return WikidataPermanentError(message)
    return WikidataOutcomeUnknownError(message)


def _retry_after_seconds(value: object, *, default: float) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default
