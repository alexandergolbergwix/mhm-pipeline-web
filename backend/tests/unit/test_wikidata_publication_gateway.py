"""Tests for the production gateway around the true Wikidata boundary."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.publication.gateway import GatewayMutation, GatewayWriteRequest, TargetObservation
from app.publication.types import PublicationEntity, TargetRef
from app.publication.wikidata_gateway import (
    _SafeWikidataGatewaySession,
    CredentialMaterial,
    CredentialTargetMismatchError,
    CurrentWikidataBoundary,
    MutationConfirmation,
    RemoteEntitySnapshot,
    WikidataGatewayAdapter,
    WikidataMaxlagError,
)


class _ReadbackUploader:
    _mark_as_bot = True
    _is_test = False

    def __init__(self) -> None:
        self.read_only_arguments: list[bool] = []

    def _is_our_item(self, qid: str) -> bool:
        del qid
        return False

    def _wbgetentities(self, ids: list[str], *, props: str) -> dict[str, dict]:
        del props
        return {
            qid: {
                "id": qid,
                "lastrevid": 18,
                "labels": {"en": {"language": "en", "value": "Manuscript"}},
                "claims": {},
            }
            for qid in ids
        }

    def _build_wbi_item(
        self,
        item: object,
        *,
        read_only: bool = False,
    ) -> tuple[object, int, list[str]]:
        del item
        self.read_only_arguments.append(read_only)
        if not read_only:
            raise RuntimeError("mutation validation must not run during read-back")
        return object(), 0, []


class _CsrfRetryUploader:
    _mark_as_bot = True
    _is_test = False

    def __init__(self) -> None:
        self.refresh_count = 0
        self.item = _CsrfRetryItem()

    def _build_wbi_item(
        self,
        item: object,
        *,
        read_only: bool = False,
    ) -> tuple[object, int, list[str]]:
        del item, read_only
        return self.item, 0, []

    def _assert_modifiable(self, qid: str, *, stage: str) -> None:
        del qid, stage

    def _check_moratorium_for_live(self) -> None:
        return

    def _refresh_edit_token(self) -> None:
        self.refresh_count += 1


class _CsrfRetryItem:
    def __init__(self) -> None:
        self.write_count = 0

    def write(self, **kwargs: object) -> object:
        del kwargs
        self.write_count += 1
        if self.write_count == 1:
            raise RuntimeError("Invalid CSRF token.")
        return SimpleNamespace(id="Q9001")


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
async def test_create_readback_does_not_require_creator_guard() -> None:
    uploader = _ReadbackUploader()
    boundary = CurrentWikidataBoundary(uploader=uploader)
    mutation = GatewayMutation(
        entity_key="manuscript:1",
        entity_type="manuscript",
        action="create",
        target_qid=None,
        payload_digest="payload",
        entity_digest="1" * 64,
        document={"labels": {"en": "Manuscript"}, "statements": []},
        identity_assertions=("P217:shelf-1",),
    )

    confirmation = await boundary.confirm_mutation(mutation, qid="Q9001")

    assert confirmation.status == "applied"
    assert uploader.read_only_arguments == [True]


@pytest.mark.asyncio
async def test_update_readback_keeps_creator_guard() -> None:
    uploader = _ReadbackUploader()
    boundary = CurrentWikidataBoundary(uploader=uploader)
    mutation = GatewayMutation(
        entity_key="manuscript:1",
        entity_type="manuscript",
        action="update",
        target_qid="Q9001",
        payload_digest="payload",
        entity_digest="1" * 64,
        document={"labels": {"en": "Manuscript"}, "statements": []},
        identity_assertions=("P217:shelf-1",),
        expected_revision=17,
    )

    confirmation = await boundary.confirm_mutation(mutation, qid="Q9001")

    assert confirmation.status == "unknown"
    assert uploader.read_only_arguments == [False]


@pytest.mark.asyncio
async def test_reference_only_reuses_its_sealed_qid_without_generic_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.pipeline.wikidata_upload as wikidata_upload

    def fail_generic_reconciliation(_: list[object]) -> list[object]:
        raise AssertionError("reference-only entities must not use generic reconciliation")

    monkeypatch.setattr(wikidata_upload, "_reconcile_sync", fail_generic_reconciliation)
    boundary = CurrentWikidataBoundary(uploader=_ReadbackUploader())
    entity = PublicationEntity(
        release_id="release-1",
        entity_key="person:1",
        entity_type="person",
        entity_digest="1" * 64,
        document={
            "labels": {"en": "Known person"},
            "statements": [],
            "publication_reference_only": {
                "qid": "Q9001",
                "remote_revision": 18,
            },
        },
        evidence_refs=(),
        identity_assertions=("P8189:authority-1",),
        local_references=(),
    )

    observations = await boundary.reconcile_batch((entity,))

    assert len(observations) == 1
    observation = observations[0]
    assert observation.status == "present_foreign"
    assert observation.qid == "Q9001"
    assert observation.remote_revision == 18


@pytest.mark.asyncio
async def test_reference_only_blocks_when_its_sealed_revision_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.pipeline.wikidata_upload as wikidata_upload

    monkeypatch.setattr(
        wikidata_upload,
        "_reconcile_sync",
        lambda _: (_ for _ in ()).throw(AssertionError("generic reconciliation ran")),
    )
    boundary = CurrentWikidataBoundary(uploader=_ReadbackUploader())
    entity = PublicationEntity(
        release_id="release-1",
        entity_key="person:1",
        entity_type="person",
        entity_digest="1" * 64,
        document={
            "labels": {"en": "Known person"},
            "statements": [],
            "publication_reference_only": {
                "qid": "Q9001",
                "remote_revision": 17,
            },
        },
        evidence_refs=(),
        identity_assertions=("P8189:authority-1",),
        local_references=(),
    )

    observations = await boundary.reconcile_batch((entity,))

    assert observations[0].status == "unknown"
    assert observations[0].detail == (
        "The reference-only target revision changed. Review its identity again."
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
async def test_write_refreshes_expired_csrf_token_before_retry() -> None:
    uploader = _CsrfRetryUploader()
    boundary = CurrentWikidataBoundary(uploader=uploader)
    mutation = GatewayMutation(
        entity_key="manuscript:1",
        entity_type="manuscript",
        action="update",
        target_qid="Q9001",
        payload_digest="payload",
        entity_digest="1" * 64,
        document={"labels": {"en": "Manuscript"}, "statements": []},
        identity_assertions=("P217:shelf-1",),
        expected_revision=17,
    )

    qid = await boundary.write_once(
        GatewayWriteRequest(
            intent_id="intent-1",
            request_key="execution-1:manuscript-1:1",
            mutation=mutation,
        )
    )

    assert qid == "Q9001"
    assert uploader.refresh_count == 1
    assert uploader.item.write_count == 2


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


class _MissingTargetUploader:
    _mark_as_bot = True
    _is_test = False

    def __init__(self, *, is_test: bool) -> None:
        self._is_test = is_test

    def _is_our_item(self, qid: str) -> bool:
        del qid
        return False

    def _wbgetentities(self, ids: list[str], *, props: str) -> dict[str, dict]:
        del props
        return {qid: {"missing": "-"} for qid in ids}


@pytest.mark.asyncio
@pytest.mark.parametrize("is_test,expected_status", [(True, "absent"), (False, "unknown")])
async def test_reconciled_target_absent_on_the_target_wiki(
    monkeypatch: pytest.MonkeyPatch,
    *,
    is_test: bool,
    expected_status: str,
) -> None:
    import app.pipeline.wikidata_upload as wikidata_upload

    def reconcile_to_production_qid(items: list[object]) -> list[object]:
        assert len(items) == 1
        return [
            SimpleNamespace(
                local_id="ms:1",
                label="Ms",
                entity_type="manuscript",
                existing_qid="Q141175735",
                method="identifier",
                message="Found Q141175735 via identifier",
            )
        ]

    monkeypatch.setattr(wikidata_upload, "_reconcile_sync", reconcile_to_production_qid)
    boundary = CurrentWikidataBoundary(
        uploader=_MissingTargetUploader(is_test=is_test),
    )
    entity = PublicationEntity(
        release_id="release-1",
        entity_key="ms:1",
        entity_type="manuscript",
        entity_digest="1" * 64,
        document={"labels": {"en": "Ms"}, "statements": []},
        evidence_refs=(),
        identity_assertions=("P8189:authority-1",),
        local_references=(),
    )

    observations = await boundary.reconcile_batch((entity,))

    assert len(observations) == 1
    observation = observations[0]
    assert observation.status == expected_status
    if expected_status == "unknown":
        assert "Q141175735" in (observation.detail or "")


class _TestWikiUploader(_MissingTargetUploader):
    def __init__(self, *, skipped: list[str]) -> None:
        super().__init__(is_test=True)
        self._skipped = skipped
        self.adapted = 0

    def _ensure_test_maps_for_item(self, item: object) -> object:
        del item
        return None

    def _adapt_item_for_test_wiki(self, item: object) -> tuple[object, object]:
        self.adapted += 1
        return item, SimpleNamespace(stats=None, skipped=self._skipped)


@pytest.mark.asyncio
async def test_test_target_write_refuses_incompatible_leftovers() -> None:
    uploader = _TestWikiUploader(skipped=["P854 url not representable"])
    boundary = CurrentWikidataBoundary(uploader=uploader)
    entity = PublicationEntity(
        release_id="release-1",
        entity_key="ms:1",
        entity_type="manuscript",
        entity_digest="1" * 64,
        document={"labels": {"en": "Ms"}, "statements": []},
        evidence_refs=(),
        identity_assertions=(),
        local_references=(),
    )
    mutation = GatewayMutation(
        entity_key=entity.entity_key,
        entity_type=entity.entity_type,
        action="create",
        target_qid=None,
        payload_digest="1" * 64,
        entity_digest=entity.entity_digest,
        document=entity.document,
        identity_assertions=entity.identity_assertions,
    )
    class _Factory:
        async def open(self, credential: CredentialMaterial) -> CurrentWikidataBoundary:
            return boundary

    material = CredentialMaterial(
        credential_id="wikidata:test:actor",
        target=TargetRef(site="test.wikidata.org", environment="test"),
        secret="token",
    )

    class _FixedResolver:
        async def resolve(self, credential_ref: str) -> CredentialMaterial:
            del credential_ref
            return material

    adapter = WikidataGatewayAdapter(
        credential_resolver=_FixedResolver(), boundary_factory=_Factory(),
    )
    session = await adapter.open(
        "wikidata:test:actor",
        TargetRef(site="test.wikidata.org", environment="test"),
    )
    outcome = await session.write(
        GatewayWriteRequest(
            intent_id="intent-1",
            request_key="execution-1:ms:1:1",
            mutation=mutation,
        ),
    )
    assert uploader.adapted == 1
    assert outcome.status == "blocked"
    assert "P854" in (outcome.detail or "")


def test_native_item_drops_legacy_string_typed_twin() -> None:
    entity = PublicationEntity(
        release_id="release-1",
        entity_key="QDraft_MS_1",
        entity_type="manuscript",
        entity_digest="1" * 64,
        document={
            "labels": {"en": "Ms"},
            "statements": [
                {"property_id": "P1104", "value": "2", "value_type": "string"},
                {
                    "property_id": "P1104",
                    "value": 113,
                    "value_type": "quantity",
                    "unit": "Q107256474",
                },
                {"property_id": "P3959", "value": "9900001", "value_type": "external-id"},
            ],
        },
        evidence_refs=(),
        identity_assertions=(),
        local_references=(),
    )

    item = CurrentWikidataBoundary._native_item(  # type: ignore[attr-defined]  # noqa: SLF001
        entity=entity,
    )

    p1104 = [s for s in item.statements if s.property_id == "P1104"]
    assert len(p1104) == 1
    assert p1104[0].value_type == "quantity"
    assert p1104[0].value == 113
    assert [s.value for s in item.statements if s.property_id == "P3959"] == [
        "9900001",
    ]


def test_native_item_coerces_lone_numeric_string_on_quantity_property() -> None:
    entity = PublicationEntity(
        release_id="release-1",
        entity_key="QDraft_MS_2",
        entity_type="manuscript",
        entity_digest="2" * 64,
        document={
            "labels": {"en": "Ms"},
            "statements": [
                {"property_id": "P1104", "value": "74", "value_type": "string"},
            ],
        },
        evidence_refs=(),
        identity_assertions=(),
        local_references=(),
    )

    item = CurrentWikidataBoundary._native_item(  # type: ignore[attr-defined]  # noqa: SLF001
        entity=entity,
    )

    assert len(item.statements) == 1
    assert item.statements[0].value_type == "quantity"
