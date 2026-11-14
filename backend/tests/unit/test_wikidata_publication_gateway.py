"""Tests for the production gateway around the true Wikidata boundary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.publication.gateway import (
    GatewayMutation, GatewayRecoveryRequest, GatewayWriteRequest, TargetObservation,
)
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
from converter.wikidata.uploader import ClaimBuildError, WikidataUploader


async def _authorize_send() -> bool:
    return True


class _ReadbackUploader:
    _mark_as_bot = True
    _is_test = False

    def __init__(self) -> None:
        self.read_only_arguments: list[bool] = []

    def _is_our_item(self, qid: str, *, our_p3959: str | None = None) -> bool:
        del qid, our_p3959
        return False

    def _get_authenticated_user(self) -> str:
        return "User"

    def _get_first_revision_author(self, qid: str) -> str:
        return "ForeignCreator"

    def _our_p3959(self, item: object) -> str | None:
        del item
        return None

    def _assert_modifiable(self, qid: str, *, stage: str) -> None:
        del qid, stage
        raise RuntimeError("mutation validation must not run during read-back")

    def _p3959_identity_accepts(self, qid: str, our_p3959: str | None) -> bool:
        del qid, our_p3959
        return False

    def _wbgetentities(self, ids: list[str], *, props: str) -> dict[str, dict]:
        del props
        return {
            qid: {
                "id": qid,
                "lastrevid": 18,
                "labels": {"en": {"language": "en", "value": "Manuscript"}},
                "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q47461344"}}}}]},
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

    def _get_authenticated_user(self) -> str:
        return "User"

    def _get_first_revision_author(self, qid: str) -> str:
        return "User"

    def _is_our_item(self, qid: str) -> bool:
        return True

    def _build_wbi_item(
        self,
        item: object,
        *,
        read_only: bool = False,
    ) -> tuple[object, int, list[str]]:
        self.item.id = getattr(item, "existing_qid", None)
        return self.item, 0, []

    def _assert_modifiable(
        self,
        qid: str,
        *,
        stage: str,
        our_p3959: str | None = None,
    ) -> None:
        del qid, stage, our_p3959

    def _our_p3959(self, item: object) -> str | None:
        del item
        return None

    def _p3959_identity_accepts(self, qid: str, our_p3959: str | None) -> bool:
        del qid, our_p3959
        return False

    def _check_moratorium_for_live(self) -> None:
        return

    def _refresh_edit_token(self) -> None:
        self.refresh_count += 1


class _CsrfRetryItem:
    def __init__(self) -> None:
        self.write_count = 0
        self.write_options: list[dict[str, object]] = []

    def write(self, **kwargs: object) -> object:
        self.write_options.append(kwargs)
        self.write_count += 1
        if self.write_count == 1:
            from wikibaseintegrator.wbi_exceptions import MWApiError

            raise MWApiError({"code": "badtoken", "info": "Invalid CSRF token."})
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
    assert uploader.read_only_arguments == []


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
        authorize_send=_authorize_send,
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
            authorize_send=_authorize_send,
        )
    )

    assert qid == "Q9001"
    assert uploader.refresh_count == 1
    assert uploader.item.write_count == 2
    assert [options["max_retries"] for options in uploader.item.write_options] == [1, 1]


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
            authorize_send=_authorize_send,
        )
    )

    assert outcome.status == "pre_send_retryable"
    assert "12" in (outcome.detail or "")


@pytest.mark.asyncio
async def test_approval_is_checked_again_after_item_preparation(monkeypatch) -> None:
    uploader = _CsrfRetryUploader()
    boundary = CurrentWikidataBoundary(uploader=uploader)
    async def fetch(qid):
        return RemoteEntitySnapshot(qid=qid, revision=17, fingerprint="remote")
    monkeypatch.setattr(boundary, "fetch_entity", fetch)
    checks = []
    async def authorize():
        checks.append(True)
        return len(checks) < 3
    mutation = await _SafeWikidataGatewaySession(boundary).compile_mutation(
        _entity(), action="update", target_qid="Q9001", expected_revision=17,
    )
    outcome = await _SafeWikidataGatewaySession(boundary).write(GatewayWriteRequest(
        intent_id="intent", request_key="request", mutation=mutation, authorize_send=authorize,
    ))
    assert len(checks) == 3
    assert outcome.status == "blocked"
    assert uploader.item.write_count == 0


@pytest.mark.asyncio
async def test_missing_or_failed_send_check_never_writes() -> None:
    boundary = _Boundary()
    session = _SafeWikidataGatewaySession(boundary)
    mutation = await session.compile_mutation(_entity(), action="update", target_qid="Q42", expected_revision=17)
    request = GatewayWriteRequest(intent_id="intent", request_key="request", mutation=mutation)
    assert (await session.write(request)).status == "blocked"
    async def unavailable():
        raise RuntimeError("database unavailable")
    from dataclasses import replace
    assert (await session.write(replace(request, authorize_send=unavailable))).status == "pre_send_retryable"
    assert boundary.write_calls == []


@pytest.mark.asyncio
async def test_csrf_retry_checks_approval_before_the_second_send(monkeypatch) -> None:
    uploader = _CsrfRetryUploader()
    boundary = CurrentWikidataBoundary(uploader=uploader)
    async def fetch(qid):
        return RemoteEntitySnapshot(qid=qid, revision=17, fingerprint="remote")
    monkeypatch.setattr(boundary, "fetch_entity", fetch)
    checks = []
    async def authorize():
        checks.append(True)
        return len(checks) < 4
    session = _SafeWikidataGatewaySession(boundary)
    mutation = await session.compile_mutation(_entity(), action="update", target_qid="Q9001", expected_revision=17)
    outcome = await session.write(GatewayWriteRequest(
        intent_id="intent", request_key="request", mutation=mutation, authorize_send=authorize,
    ))
    assert outcome.status == "blocked"
    assert uploader.item.write_count == 1
    assert uploader.refresh_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "update", "foreign-update"])
@pytest.mark.parametrize("failure", ["connection", "server_error", "timeout", "malformed"])
async def test_real_wbi_sends_only_once_after_ambiguous_response(
    monkeypatch, action, failure,
) -> None:
    import requests
    from wikibaseintegrator import WikibaseIntegrator
    from wikibaseintegrator.wbi_config import config

    uploader = _P3959Uploader() if action == "foreign-update" else _CsrfRetryUploader()
    sends = []
    authorization = {"allowed": True}

    def request(**kwargs):
        sends.append(kwargs)
        authorization["allowed"] = False
        if failure == "connection":
            raise requests.exceptions.ConnectionError("The response was lost")
        if failure == "timeout":
            raise requests.exceptions.ReadTimeout("The response timed out")
        if failure == "malformed":
            def invalid_json():
                raise ValueError("The response is not JSON")
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=invalid_json)
        return SimpleNamespace(status_code=503)

    login = SimpleNamespace(mediawiki_api_url=str(config["MEDIAWIKI_API_URL"]),
        get_edit_token=lambda: "fixture-token", get_session=lambda: SimpleNamespace(request=request))
    item = WikibaseIntegrator(login=login).item.new()
    if action != "create":
        item.id = "Q9001"
    monkeypatch.setattr(uploader, "_build_wbi_item", lambda native: (item, 0, []))
    monkeypatch.setattr("wikibaseintegrator.wbi_helpers.sleep", lambda seconds: None)
    boundary = CurrentWikidataBoundary(uploader=uploader)
    async def fresh(entities):
        return tuple(TargetObservation.absent(entity.entity_key) for entity in entities)
    async def fetch(qid):
        return RemoteEntitySnapshot(qid=qid, revision=17, fingerprint="remote")
    async def authorize():
        return authorization["allowed"]
    monkeypatch.setattr(boundary, "reconcile_fresh_batch", fresh)
    monkeypatch.setattr(boundary, "fetch_entity", fetch)
    session = _SafeWikidataGatewaySession(boundary)
    mutation = await session.compile_mutation(_entity(), action="create" if action == "create" else "update",
        target_qid="Q9001" if action != "create" else None,
        expected_revision=17 if action != "create" else None,
        allow_foreign_update=action == "foreign-update")
    outcome = await session.write(GatewayWriteRequest(
        intent_id="intent", request_key="request", mutation=mutation, authorize_send=authorize,
    ))
    if action == "foreign-update":
        assert sends == []
        assert outcome.status == "blocked"
        return
    assert len(sends) == 1
    assert sends[0]["allow_redirects"] is False
    assert outcome.status == "outcome_unknown"
    confirmations = []
    async def confirm(mutation, *, qid):
        confirmations.append(qid)
        return MutationConfirmation.not_applied("The write is not visible")
    monkeypatch.setattr(boundary, "confirm_mutation", confirm)
    recovered = await session.recover_ambiguous(GatewayRecoveryRequest(
        intent_id="intent", request_key="request", mutation=mutation,
    ))
    assert confirmations == [mutation.target_qid]
    assert recovered.status == "outcome_unknown"
    if action == "foreign-update":
        assert sends == []
        assert outcome.status == "blocked"
        return
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_real_wbi_does_not_follow_a_write_redirect(monkeypatch) -> None:
    import requests
    from wikibaseintegrator import WikibaseIntegrator
    from wikibaseintegrator.wbi_config import config

    class RedirectAdapter(requests.adapters.BaseAdapter):
        def __init__(self):
            self.sends = 0
        def send(self, request, **kwargs):
            self.sends += 1
            response = requests.Response()
            response.status_code = 307
            response.headers["Location"] = "https://example.invalid/redirect"
            response._content = b"{}"
            response.request = request
            response.url = request.url
            return response
        def close(self):
            pass

    adapter = RedirectAdapter()
    transport = requests.Session()
    transport.mount("https://", adapter)
    uploader = _CsrfRetryUploader()
    login = SimpleNamespace(mediawiki_api_url=str(config["MEDIAWIKI_API_URL"]),
        get_edit_token=lambda: "fixture-token", get_session=lambda: transport)
    item = WikibaseIntegrator(login=login).item.new()
    monkeypatch.setattr(uploader, "_build_wbi_item", lambda native: (item, 0, []))
    boundary = CurrentWikidataBoundary(uploader=uploader)
    async def fresh(entities):
        return tuple(TargetObservation.absent(entity.entity_key) for entity in entities)
    monkeypatch.setattr(boundary, "reconcile_fresh_batch", fresh)
    session = _SafeWikidataGatewaySession(boundary)
    mutation = await session.compile_mutation(_entity(), action="create", target_qid=None)
    outcome = await session.write(GatewayWriteRequest(
        intent_id="intent", request_key="request", mutation=mutation, authorize_send=_authorize_send,
    ))
    assert adapter.sends == 1
    assert outcome.status == "outcome_unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "The response timed out while checking the CSRF token",
    "Invalid CSRF token.",
    "The connection closed after maxlag was mentioned",
    "The response closed after editconflict was mentioned",
])
async def test_unstructured_write_errors_do_not_trigger_a_retry(monkeypatch, message) -> None:
    uploader = _CsrfRetryUploader()
    calls = []
    def write(**kwargs):
        calls.append(kwargs)
        raise RuntimeError(message)
    monkeypatch.setattr(uploader.item, "write", write)
    boundary = CurrentWikidataBoundary(uploader=uploader)
    async def fetch(qid):
        return RemoteEntitySnapshot(qid=qid, revision=17, fingerprint="remote")
    monkeypatch.setattr(boundary, "fetch_entity", fetch)
    session = _SafeWikidataGatewaySession(boundary)
    mutation = await session.compile_mutation(_entity(), action="update", target_qid="Q9001", expected_revision=17)
    outcome = await session.write(GatewayWriteRequest(
        intent_id="intent", request_key="request", mutation=mutation, authorize_send=_authorize_send,
    ))
    assert outcome.status == "outcome_unknown"
    assert len(calls) == 1
    assert uploader.refresh_count == 0


@pytest.mark.asyncio
async def test_untrusted_error_code_does_not_trigger_a_retry(monkeypatch) -> None:
    class AmbiguousError(RuntimeError):
        code = "badtoken"
    uploader = _CsrfRetryUploader()
    calls = []
    def write(**kwargs):
        calls.append(kwargs)
        raise AmbiguousError("The response was lost")
    monkeypatch.setattr(uploader.item, "write", write)
    boundary = CurrentWikidataBoundary(uploader=uploader)
    async def fetch(qid):
        return RemoteEntitySnapshot(qid=qid, revision=17, fingerprint="remote")
    monkeypatch.setattr(boundary, "fetch_entity", fetch)
    session = _SafeWikidataGatewaySession(boundary)
    mutation = await session.compile_mutation(_entity(), action="update", target_qid="Q9001", expected_revision=17)
    outcome = await session.write(GatewayWriteRequest(
        intent_id="intent", request_key="request", mutation=mutation, authorize_send=_authorize_send,
    ))
    assert outcome.status == "outcome_unknown"
    assert len(calls) == 1
    assert uploader.refresh_count == 0


class _MissingTargetUploader:
    _mark_as_bot = True
    _is_test = False

    def __init__(self, *, is_test: bool) -> None:
        self._is_test = is_test

    def _is_our_item(self, qid: str, *, our_p3959: str | None = None) -> bool:
        del qid, our_p3959
        return False

    def _our_p3959(self, item: object) -> str | None:
        del item
        return None

    def _assert_modifiable(self, qid: str, *, stage: str) -> None:
        assert not qid

    def _p3959_identity_accepts(self, qid: str, our_p3959: str | None) -> bool:
        del qid, our_p3959
        return False

    def _wbgetentities(self, ids: list[str], *, props: str) -> dict[str, dict]:
        del props
        return {qid: {"missing": "-"} for qid in ids}


@pytest.mark.asyncio
@pytest.mark.parametrize("is_test,expected_status", [(True, "unknown"), (False, "unknown")])
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
    if not is_test:
        assert "Q141175735" in (observation.detail or "")
    else:
        assert "test Wikidata reconciliation" in (observation.detail or "")


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
async def test_test_target_write_refuses_incompatible_leftovers(monkeypatch) -> None:
    uploader = _TestWikiUploader(skipped=["P854 url not representable"])
    boundary = CurrentWikidataBoundary(uploader=uploader)
    async def fresh_absent(entities):
        return tuple(TargetObservation.absent(entity.entity_key) for entity in entities)
    monkeypatch.setattr(boundary, "reconcile_fresh_batch", fresh_absent)
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
            authorize_send=_authorize_send,
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


class _P3959Uploader:
    _mark_as_bot = True
    _is_test = False

    def __init__(self) -> None:
        self.assert_calls: list[str | None] = []
        self._foreign_accept_qids: set[str] = set()
        self._is_our_item_cache: dict[str, bool] = {}
        self.write_count = 0

    def _is_our_item(self, qid: str, *, our_p3959: str | None = None) -> bool:
        del qid, our_p3959
        return False

    def _get_authenticated_user(self) -> str:
        return "User"

    def _get_first_revision_author(self, qid: str) -> str:
        return "ForeignCreator"

    def _our_p3959(self, item: object) -> str | None:
        del item
        return "990000533850205171"

    def _wbgetentities(self, ids: list[str], *, props: str) -> dict[str, dict]:
        del props
        return {qid: {"id": qid, "lastrevid": 7, "claims": {}} for qid in ids}

    def _build_wbi_item(
        self,
        item: object,
        *,
        read_only: bool = False,
    ) -> tuple[object, int, list[str]]:
        del item, read_only
        def write(**kwargs: object) -> object:
            self.write_count += 1
            return SimpleNamespace(id="Q134619072")
        return SimpleNamespace(write=write), 1, ["P31"]

    def _assert_modifiable(
        self,
        qid: str,
        *,
        stage: str,
        our_p3959: str | None = None,
    ) -> None:
        self.assert_calls.append(our_p3959)
        if qid and qid not in self._foreign_accept_qids:
            from converter.wikidata.uploader import UnauthorisedModificationError

            raise UnauthorisedModificationError(qid=qid, stage=stage)

    def _check_moratorium_for_live(self) -> None:
        return

    def register_foreign_accept(self, qid: str) -> None:
        self._foreign_accept_qids.add(qid)
        self._is_our_item_cache[qid] = True


@pytest.mark.asyncio
async def test_write_once_requires_consent_despite_matching_p3959() -> None:
    uploader = _P3959Uploader()
    boundary = CurrentWikidataBoundary(uploader=uploader)
    mutation = GatewayMutation(
        entity_key="manuscript:1",
        entity_type="manuscript",
        action="update",
        target_qid="Q134619072",
        payload_digest="payload",
        entity_digest="1" * 64,
        document={
            "labels": {"en": "Manuscript"},
            "statements": [{"property_id": "P3959", "value": "990000533850205171"}],
        },
        identity_assertions=("P3959:990000533850205171",),
        expected_revision=7,
    )

    request = GatewayWriteRequest(
        intent_id="intent-1", request_key="execution-1:manuscript:1:1", mutation=mutation,
        authorize_send=_authorize_send,
    )
    with pytest.raises(Exception, match="not authored by the authenticated user"):
        await boundary.write_once(request)
    assert uploader.write_count == 0
    assert uploader.assert_calls == []

    from dataclasses import replace
    accepted_request = replace(request, mutation=replace(mutation, allow_foreign_update=True))
    with pytest.raises(Exception, match="not authored by the authenticated user"):
        await boundary.write_once(accepted_request)
    assert uploader.write_count == 0
    assert uploader._foreign_accept_qids == set()
    assert uploader._is_our_item_cache == {}
    with pytest.raises(Exception, match="not authored by the authenticated user"):
        await boundary.write_once(request)
    assert uploader.write_count == 0


@pytest.mark.asyncio
async def test_write_once_refuses_when_p3959_identity_is_not_proven() -> None:
    uploader = _P3959Uploader()
    uploader._our_p3959 = lambda item: None  # type: ignore[method-assign]
    boundary = CurrentWikidataBoundary(uploader=uploader)
    mutation = GatewayMutation(
        entity_key="work:1",
        entity_type="work",
        action="update",
        target_qid="Q191825",
        payload_digest="payload",
        entity_digest="2" * 64,
        document={"labels": {"he": "משנה"}, "statements": []},
        identity_assertions=(),
        expected_revision=7,
    )

    with pytest.raises(Exception) as raised:
        await boundary.write_once(
            GatewayWriteRequest(
                intent_id="intent-1",
                request_key="execution-1:work:1:1",
                mutation=mutation,
                authorize_send=_authorize_send,
            )
        )

    assert "not authored by the authenticated user" in str(raised.value)
    assert uploader.assert_calls == []
    assert uploader.write_count == 0


@pytest.mark.asyncio
async def test_reconcile_p3959_identity_keeps_foreign_creator_foreign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.pipeline.wikidata_upload as wikidata_upload
    from converter.wikidata.uploader import WikidataUploader

    def reconcile_to_our_manuscript(items: list[object]) -> list[object]:
        assert len(items) == 1
        return [
            SimpleNamespace(
                local_id="ms:1",
                label="Ms",
                entity_type="manuscript",
                existing_qid="Q134619072",
                method="p3959/shelfmark",
                message="Found Q134619072 via p3959",
            )
        ]

    monkeypatch.setattr(wikidata_upload, "_reconcile_sync", reconcile_to_our_manuscript)
    uploader = WikidataUploader(token="User@Bot:deadbeef", is_test=False)
    monkeypatch.setattr(uploader, "_get_authenticated_user", lambda: "User")
    monkeypatch.setattr(uploader, "_get_first_revision_author", lambda qid: "ForeignCreator")
    monkeypatch.setattr(
        uploader,
        "_wbgetentities",
        lambda ids, *, props: {
            i: {
                "id": i,
                "lastrevid": 7,
                "claims": {
                    "P3959": [
                        {
                            "mainsnak": {
                                "snaktype": "value",
                                "property": "P3959",
                                "datavalue": {
                                    "value": "990000533850205171",
                                    "type": "string",
                                },
                            }
                        }
                    ]
                },
            }
            for i in ids
        },
    )
    boundary = CurrentWikidataBoundary(uploader=uploader)
    entity = PublicationEntity(
        release_id="release-1",
        entity_key="ms:1",
        entity_type="manuscript",
        entity_digest="1" * 64,
        document={
            "labels": {"en": "Ms"},
            "statements": [{"property_id": "P3959", "value": "990000533850205171"}],
        },
        evidence_refs=(),
        identity_assertions=("P3959:990000533850205171",),
        local_references=(),
    )

    observations = await boundary.reconcile_batch((entity,))

    assert len(observations) == 1
    observation = observations[0]
    assert observation.status == "present_foreign"
    assert observation.qid == "Q134619072"
    assert observation.method == "p3959/shelfmark"

    # Another in-flight mutation may temporarily prime the uploader cache.
    uploader.register_foreign_accept("Q134619072")
    assert (await boundary.reconcile_batch((entity,)))[0].status == "present_foreign"
    session = _SafeWikidataGatewaySession(boundary)
    monkeypatch.setattr(boundary, "reconcile_fresh_batch", boundary.reconcile_batch)
    mutation = await session.compile_mutation(entity, action="create", target_qid=None)
    monkeypatch.setattr(
        uploader, "_build_wbi_item",
        lambda *args, **kwargs: pytest.fail("A foreign CREATE match must not reach a write"),
    )
    outcome = await session.write(GatewayWriteRequest(
        intent_id="intent-1", request_key="execution-1:ms:1:1", mutation=mutation,
    ))
    assert outcome.status == "blocked"
    assert "Q134619072" in (outcome.detail or "")

    update = await session.compile_mutation(
        entity, action="update", target_qid="Q134619072", expected_revision=7,
    )
    outcome = await session.write(GatewayWriteRequest(
        intent_id="intent-2", request_key="execution-1:ms:1:2", mutation=update,
        authorize_send=_authorize_send,
    ))
    assert outcome.status == "blocked"


class _HangingBoundary(_Boundary):
    async def reconcile_batch(self, entities):
        await asyncio.sleep(60)
        return await super().reconcile_batch(entities)

    async def write_once(self, request):
        await asyncio.sleep(60)
        return await super().write_once(request)

    async def fetch_entity(self, qid):
        await asyncio.sleep(60)
        return await super().fetch_entity(qid)


@pytest.mark.asyncio
async def test_hung_writes_and_reads_become_retryable_not_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.publication.wikidata_gateway as gateway_module

    monkeypatch.setattr(gateway_module, "_RECONCILE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(gateway_module, "_FETCH_TIMEOUT_S", 0.05)
    monkeypatch.setattr(gateway_module, "_WRITE_TIMEOUT_S", 0.05)
    session = _SafeWikidataGatewaySession(_HangingBoundary())
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

    outcome = await session.write(
        GatewayWriteRequest(
            intent_id="intent-1",
            request_key="execution-1:manuscript-1:1",
            mutation=mutation,
        )
    )
    assert outcome.status == "pre_send_retryable"
    assert "time budget" in (outcome.detail or "")

    update_mutation = GatewayMutation(
        entity_key="manuscript:1",
        entity_type="manuscript",
        action="update",
        target_qid="Q1",
        payload_digest="payload",
        entity_digest="1" * 64,
        document={"labels": {"en": "Manuscript"}, "statements": []},
        identity_assertions=(),
        expected_revision=17,
    )
    outcome = await session.write(
        GatewayWriteRequest(
            intent_id="intent-2",
            request_key="execution-1:manuscript-1:2",
            mutation=update_mutation,
        )
    )
    assert outcome.status == "pre_send_retryable"


class _HangingWriteBoundary(_Boundary):
    async def write_once(self, request):
        await asyncio.sleep(60)
        return await super().write_once(request)


@pytest.mark.asyncio
async def test_hung_wbi_write_returns_an_unknown_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.publication.wikidata_gateway as gateway_module

    monkeypatch.setattr(gateway_module, "_WRITE_TIMEOUT_S", 0.05)
    session = _SafeWikidataGatewaySession(_HangingWriteBoundary())
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

    outcome = await session.write(
        GatewayWriteRequest(
            intent_id="intent-1",
            request_key="execution-1:manuscript-1:1",
            mutation=mutation,
            authorize_send=_authorize_send,
        )
    )

    assert outcome.status == "outcome_unknown"
    assert "600 s time budget" in (outcome.detail or "") or "time budget" in (outcome.detail or "")


class _OwnedTargetBoundary(_Boundary):
    async def reconcile_batch(self, entities):
        return tuple(
            TargetObservation.present_owned(
                entity.entity_key, qid="Q42", remote_revision=7, fingerprint="remote",
                method="p3959/shelfmark",
            )
            for entity in entities
        )


class _ForeignTargetBoundary(_Boundary):
    async def reconcile_batch(self, entities):
        return tuple(
            TargetObservation.present_foreign(
                entity.entity_key, qid="Q42", remote_revision=7, fingerprint="remote",
                method="p3959/shelfmark",
            )
            for entity in entities
        )


@pytest.mark.asyncio
async def test_create_preflight_blocks_owned_identity_until_new_plan() -> None:
    boundary = _OwnedTargetBoundary()
    session = _SafeWikidataGatewaySession(boundary)
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

    outcome = await session.write(
        GatewayWriteRequest(
            intent_id="intent-1",
            request_key="execution-1:manuscript-1:1",
            mutation=mutation,
        )
    )

    assert outcome.status == "blocked"
    assert "Q42" in (outcome.detail or "")
    assert boundary.write_calls == []


@pytest.mark.asyncio
async def test_reconcile_uses_sealed_known_qid_even_without_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.pipeline.wikidata_upload as wikidata_upload
    monkeypatch.setattr(wikidata_upload, "_reconcile_sync",
        lambda items: pytest.fail("Known QID must not become an identifier-free CREATE"))
    entity = PublicationEntity(
        release_id="release-1", entity_key="work:1", entity_type="work",
        entity_digest="1" * 64,
        document={"labels": {"en": "Known work"}, "existing_qid": "Q9001", "statements": [],
            "records": ["record-1"], "work_candidate_evidence": [{"source_field": "245"}]},
        evidence_refs=(), identity_assertions=("wikidata:Q9001",), local_references=(),
    )
    boundary = CurrentWikidataBoundary(uploader=_ReadbackUploader())
    observation = (await boundary.reconcile_batch((entity,)))[0]
    assert observation.status == "present_foreign"
    assert observation.qid == "Q9001"
    assert observation.remote_revision == 18


@pytest.mark.asyncio
@pytest.mark.parametrize("reference_only", [False, True])
@pytest.mark.parametrize("p31", [[], ["Q5"], ["Q47461344", "Q5"]])
async def test_work_target_requires_written_work_type(reference_only, p31) -> None:
    class Uploader(_ReadbackUploader):
        def _wbgetentities(self, ids, *, props):
            return {qid: {"id": qid, "lastrevid": 18, "claims": {"P31": [
                {"mainsnak": {"datavalue": {"value": {"id": value}}}} for value in p31
            ]}} for qid in ids}
    document = {"existing_qid": "Q9001", "records": [], "work_candidate_evidence": [], "statements": []}
    if reference_only:
        document["publication_reference_only"] = {"qid": "Q9001", "remote_revision": 18}
    entity = PublicationEntity(release_id="release", entity_key="work:1", entity_type="work",
        entity_digest="1" * 64, document=document, evidence_refs=(), identity_assertions=(), local_references=())
    observation = (await CurrentWikidataBoundary(uploader=Uploader()).reconcile_batch((entity,)))[0]
    assert observation.status == "unknown"
    assert "P31" in (observation.detail or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [
    [{"mainsnak": {"snaktype": "somevalue"}}],
    [{"rank": "deprecated", "mainsnak": {"datavalue": {"value": {"id": "Q47461344"}}}}],
    [{"mainsnak": {"datavalue": {"type": "string", "value": {"id": "Q47461344"}}}}],
    [{"mainsnak": {"datavalue": {"value": {"id": "Q47461344", "numeric-id": 5}}}}],
])
async def test_work_target_rejects_unusable_p31(rows) -> None:
    class Uploader(_ReadbackUploader):
        def _wbgetentities(self, ids, *, props):
            return {qid: {"id": qid, "lastrevid": 18, "claims": {"P31": rows}} for qid in ids}
    entity = PublicationEntity(release_id="release", entity_key="work:1", entity_type="work",
        entity_digest="1" * 64, document={"existing_qid": "Q9001", "records": [],
            "work_candidate_evidence": [], "statements": []}, evidence_refs=(),
        identity_assertions=(), local_references=())
    observation = (await CurrentWikidataBoundary(uploader=Uploader()).reconcile_batch((entity,)))[0]
    assert observation.status == "unknown"
    assert "P31" in (observation.detail or "")


@pytest.mark.asyncio
async def test_production_reconciled_work_cannot_match_a_person(monkeypatch) -> None:
    import app.pipeline.wikidata_upload as upload
    monkeypatch.setattr(upload, "_reconcile_sync", lambda items: [
        SimpleNamespace(method="identifier", existing_qid="Q9001", message="matched") for item in items
    ])
    class Uploader(_ReadbackUploader):
        def _wbgetentities(self, ids, *, props):
            return {qid: {"id": qid, "lastrevid": 18, "claims": {"P31": [
                {"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}
            ]}} for qid in ids}
    entity = PublicationEntity(release_id="release", entity_key="work:1", entity_type="work",
        entity_digest="1" * 64, document={"records": [], "work_candidate_evidence": [], "statements": []},
        evidence_refs=(), identity_assertions=(), local_references=())
    observation = (await CurrentWikidataBoundary(uploader=Uploader()).reconcile_batch((entity,)))[0]
    assert observation.status == "unknown"
    assert "P31" in (observation.detail or "")


@pytest.mark.asyncio
async def test_work_update_preflight_refuses_human_without_writing() -> None:
    class Boundary(_Boundary):
        async def fetch_entity(self, qid):
            return RemoteEntitySnapshot(qid=qid, revision=17, fingerprint="remote",
                document={"claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}]}})
    boundary = Boundary()
    session = _SafeWikidataGatewaySession(boundary)
    entity = PublicationEntity(release_id="release", entity_key="work:1", entity_type="work",
        entity_digest="1" * 64, document={"records": [], "work_candidate_evidence": [], "statements": []},
        evidence_refs=(), identity_assertions=(), local_references=())
    mutation = await session.compile_mutation(entity, action="update", target_qid="Q42", expected_revision=17,
        allow_foreign_update=True)
    outcome = await session.write(GatewayWriteRequest(
        intent_id="intent", request_key="request", mutation=mutation, authorize_send=_authorize_send))
    assert outcome.status == "blocked"
    assert "P31" in (outcome.detail or "")
    assert boundary.write_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("qid", ["Q47461344", "Q571"])
async def test_work_target_allows_verified_written_work_type(qid) -> None:
    class Uploader(_ReadbackUploader):
        def _wbgetentities(self, ids, *, props):
            return {item: {"id": item, "lastrevid": 18, "claims": {"P31": [
                {"mainsnak": {"datavalue": {"value": {"id": qid}}}}
            ]}} for item in ids}
    entity = PublicationEntity(release_id="release", entity_key="work:1", entity_type="work",
        entity_digest="1" * 64, document={"existing_qid": "Q9001", "records": [],
            "work_candidate_evidence": [], "statements": []}, evidence_refs=(),
        identity_assertions=(), local_references=())
    observation = (await CurrentWikidataBoundary(uploader=Uploader()).reconcile_batch((entity,)))[0]
    assert observation.status == "present_foreign"


@pytest.mark.asyncio
async def test_test_wiki_work_type_does_not_reuse_production_numeric_p31() -> None:
    class Uploader(_ReadbackUploader):
        _is_test = True
    entity = PublicationEntity(release_id="release", entity_key="work:1", entity_type="work",
        entity_digest="1" * 64, document={"existing_qid": "Q9001", "records": [],
            "work_candidate_evidence": [], "statements": [],
            "publication_target_qid": {"site": "test.wikidata.org", "qid": "Q9001"}},
        evidence_refs=(), identity_assertions=(), local_references=())
    observation = (await CurrentWikidataBoundary(uploader=Uploader()).reconcile_batch((entity,)))[0]
    assert observation.status == "unknown"
    assert "P31" in (observation.detail or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh", [False, True])
async def test_test_wiki_generic_reconciliation_never_queries_production(monkeypatch, fresh) -> None:
    import app.pipeline.wikidata_upload as upload
    import converter.wikidata.reconciler as reconciler
    monkeypatch.setattr(upload, "_reconcile_sync", lambda items: pytest.fail("Production reconciliation ran"))
    monkeypatch.setattr(reconciler, "WikidataReconciler", lambda: pytest.fail("Production reconciler opened"))
    class Uploader(_ReadbackUploader):
        _is_test = True
        def _wbgetentities(self, ids, *, props):
            pytest.fail("A production QID was read on test Wikidata")
    boundary = CurrentWikidataBoundary(uploader=Uploader())
    observation = (await (boundary.reconcile_fresh_batch((_entity(),)) if fresh
        else boundary.reconcile_batch((_entity(),))))[0]
    assert observation.status == "unknown"
    if fresh:
        session = _SafeWikidataGatewaySession(boundary)
        mutation = await session.compile_mutation(_entity(), action="create", target_qid=None)
        outcome = await session.write(GatewayWriteRequest(
            intent_id="intent", request_key="request", mutation=mutation, authorize_send=_authorize_send))
        assert outcome.status == "pre_send_retryable"


@pytest.mark.asyncio
@pytest.mark.parametrize("marker,expected", [
    ({"site": "test.wikidata.org", "qid": "Q9001"}, "present_owned"),
    ({"site": "www.wikidata.org", "qid": "Q9001"}, "unknown"),
    ({"site": "test.wikidata.org", "qid": "Q9002"}, "unknown"),
    (None, "unknown"),
])
async def test_test_target_qid_requires_sealed_namespace_and_creator(marker, expected) -> None:
    class OwnedUploader(_ReadbackUploader):
        _is_test = True
        def _get_first_revision_author(self, qid):
            return "User"
        def _is_our_item(self, qid, *, our_p3959=None):
            return True
    entity = PublicationEntity(release_id="release-1", entity_key="person:1", entity_type="person",
        entity_digest="1" * 64,
        document={"existing_qid": "Q9001", "publication_target_qid": marker, "statements": [],
            "records": ["record-1"], "work_candidate_evidence": [{"source_field": "245"}]},
        evidence_refs=(), identity_assertions=("wikidata:Q9001",), local_references=())
    observation = (await CurrentWikidataBoundary(uploader=OwnedUploader()).reconcile_batch((entity,)))[0]
    assert observation.status == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("document,assertions", [
    ({"existing_qid": "Q9001", "statements": []}, ("wikidata:Q9002",)),
    ({"existing_qid": "not-a-qid", "statements": []}, ()),
])
async def test_reconcile_rejects_invalid_or_conflicting_sealed_qids(document, assertions) -> None:
    document.update({"records": ["record-1"], "work_candidate_evidence": [{"source_field": "245"}]})
    entity = PublicationEntity(release_id="release-1", entity_key="work:1", entity_type="work",
        entity_digest="1" * 64, document=document, evidence_refs=(),
        identity_assertions=assertions, local_references=())
    observation = (await CurrentWikidataBoundary(uploader=_ReadbackUploader())
        .reconcile_batch((entity,)))[0]
    assert observation.status == "unknown"


@pytest.mark.asyncio
async def test_final_create_check_does_not_use_shared_absence_cache(monkeypatch) -> None:
    import app.pipeline.wikidata_upload as wikidata_upload
    import converter.wikidata.reconciler as reconciler_module

    class FreshReconciler:
        pass

    calls = []
    monkeypatch.setattr(reconciler_module, "WikidataReconciler", FreshReconciler)
    monkeypatch.setattr(wikidata_upload, "_reconcile_sync", lambda items: [
        SimpleNamespace(method="none", existing_qid=None, message="cached absent") for item in items
    ])
    def fresh_lookup(reconciler, item, entity_type):
        calls.append(reconciler)
        assert isinstance(reconciler, FreshReconciler)
        return "Q9001", "identifier"
    monkeypatch.setattr(wikidata_upload, "_reconcile_for_upload", fresh_lookup)
    boundary = CurrentWikidataBoundary(uploader=_ReadbackUploader())
    session = _SafeWikidataGatewaySession(boundary)
    from dataclasses import replace
    entity = replace(_entity(), document={"statements": [{"property": "P3959", "value": "123"}]})
    assert (await session.reconcile_batch((entity,)))[0].status == "absent"
    mutation = await session.compile_mutation(entity, action="create", target_qid=None)
    outcome = await session.write(GatewayWriteRequest(
        intent_id="intent", request_key="request", mutation=mutation,
    ))
    assert outcome.status == "blocked"
    assert "Q9001" in (outcome.detail or "")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_final_create_without_duplicate_keys_is_inconclusive(monkeypatch) -> None:
    import converter.wikidata.reconciler as reconciler_module
    monkeypatch.setattr(reconciler_module, "WikidataReconciler", lambda: object())
    boundary = CurrentWikidataBoundary(uploader=_ReadbackUploader())
    session = _SafeWikidataGatewaySession(boundary)
    mutation = await session.compile_mutation(_entity(), action="create", target_qid=None)
    outcome = await session.write(GatewayWriteRequest(
        intent_id="intent", request_key="request", mutation=mutation,
    ))
    assert outcome.status == "pre_send_retryable"


@pytest.mark.asyncio
async def test_test_wiki_label_conflict_never_adopts_an_update(monkeypatch) -> None:
    from wikibaseintegrator.wbi_exceptions import MWApiError

    uploader = _TestWikiUploader(skipped=[])
    calls = []
    def write(**kwargs):
        calls.append(kwargs)
        raise MWApiError({"code": "modification-failed", "info": 'Item Q42 already has label "Manuscript"'})
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None, raising=False)
    monkeypatch.setattr(uploader, "_build_wbi_item",
        lambda item: (SimpleNamespace(write=write), 0, []), raising=False)
    boundary = CurrentWikidataBoundary(uploader=uploader)
    mutation = GatewayMutation(entity_key="manuscript:1", entity_type="manuscript",
        action="create", target_qid=None, payload_digest="payload", entity_digest="1" * 64,
        document={"labels": {"en": "Manuscript"}, "statements": []}, identity_assertions=())
    from app.publication.wikidata_gateway import WikidataPermanentError
    with pytest.raises(WikidataPermanentError, match="fresh Plan"):
        await boundary.write_once(GatewayWriteRequest(
            intent_id="intent", request_key="request", mutation=mutation,
            authorize_send=_authorize_send,
        ))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_absent_readback_does_not_prove_a_timed_out_request_stopped() -> None:
    class Boundary(_Boundary):
        async def confirm_mutation(self, mutation, *, qid):
            return MutationConfirmation.not_applied("No item visible")

    session = _SafeWikidataGatewaySession(Boundary())
    mutation = await session.compile_mutation(_entity(), action="create", target_qid=None)
    outcome = await session.recover_ambiguous(GatewayRecoveryRequest(
        intent_id="intent", request_key="request", mutation=mutation,
    ))
    assert outcome.status == "outcome_unknown"


@pytest.mark.asyncio
async def test_create_preflight_still_blocks_a_foreign_identity_target() -> None:
    boundary = _ForeignTargetBoundary()
    session = _SafeWikidataGatewaySession(boundary)
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

    outcome = await session.write(
        GatewayWriteRequest(
            intent_id="intent-1",
            request_key="execution-1:manuscript-1:1",
            mutation=mutation,
        )
    )

    assert outcome.status == "blocked"
    assert "found Q42" in (outcome.detail or "")
    assert boundary.write_calls == []


class _TestUpdateUploader:
    """Mirror the real test-wiki UPDATE proof gate in _build_wbi_item."""

    _mark_as_bot = True
    _is_test = True
    register_test_ledger_target = WikidataUploader.register_test_ledger_target
    register_test_local_target = WikidataUploader.register_test_local_target
    _test_local_target_issues = WikidataUploader._test_local_target_issues

    def __init__(self) -> None:
        self._foreign_accept_qids: set[str] = set()
        self._is_our_item_cache: dict[str, bool] = {}
        self._verified_test_targets: dict[str, str] = {}
        self.write_count = 0

    def _get_authenticated_user(self) -> str:
        return "User"

    def _get_first_revision_author(self, qid: str) -> str:
        del qid
        return "User"

    def _is_our_item(self, qid: str, *, our_p3959: str | None = None) -> bool:
        del qid, our_p3959
        return True

    def _wbgetentities(self, ids: list[str], *, props: str) -> dict[str, dict]:
        del props
        return {qid: {"id": qid, "lastrevid": 7, "claims": {}} for qid in ids}

    def _assert_modifiable(self, qid: str, *, stage: str, our_p3959: str | None = None) -> None:
        del qid, stage, our_p3959

    def _check_moratorium_for_live(self) -> None:
        return

    def _ensure_test_maps_for_item(self, item: object) -> object:
        del item
        return None

    def _adapt_item_for_test_wiki(self, item: object) -> tuple[object, object]:
        return item, SimpleNamespace(skipped=[])

    def _build_wbi_item(
        self, item: object, *, read_only: bool = False,
    ) -> tuple[object, int, list[str]]:
        target = item.existing_qid
        if not read_only and target and self._verified_test_targets.get(item.local_id) != target:
            raise ClaimBuildError(["Test UPDATE target lacks exact test-ledger proof."])

        def write(**kwargs: object) -> object:
            self.write_count += 1
            return SimpleNamespace(id=target)

        return SimpleNamespace(id=target, write=write), 0, []


def _test_wiki_update(marker: object) -> GatewayMutation:
    return GatewayMutation(
        entity_key="manuscript:1",
        entity_type="manuscript",
        action="update",
        target_qid="Q9001",
        payload_digest="payload",
        entity_digest="1" * 64,
        document={
            "labels": {"en": "Manuscript"},
            "statements": [],
            "existing_qid": "Q9001",
            "publication_target_qid": marker,
        },
        identity_assertions=(),
        expected_revision=7,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("marker,status,writes", [
    ({"site": "test.wikidata.org", "qid": "Q9001"}, "succeeded", 1),
    ({"site": "test.wikidata.org", "qid": "Q9002"}, "blocked", 0),
    (None, "blocked", 0),
])
async def test_test_wiki_update_registers_sealed_target_before_write(
    marker: object, status: str, writes: int,
) -> None:
    uploader = _TestUpdateUploader()
    session = _SafeWikidataGatewaySession(CurrentWikidataBoundary(uploader=uploader))

    outcome = await session.write(GatewayWriteRequest(
        intent_id="intent-1",
        request_key="request-1",
        mutation=_test_wiki_update(marker),
        authorize_send=_authorize_send,
    ))

    assert outcome.status == status
    assert uploader.write_count == writes
    if writes:
        assert uploader._verified_test_targets == {"manuscript:1": "Q9001"}


_SEALED_LOCAL_STATEMENT = {
    "id": "Q9002", "value_namespace": "local", "value_local_id": "work:ledger",
}
_SEALED_LOCAL_TARGETS = {"work:ledger": {"site": "test.wikidata.org", "qid": "Q9002"}}


def _test_local_reference_update(statement_value: object, local_targets: object) -> GatewayMutation:
    document: dict[str, object] = {
        "labels": {"en": "Manuscript"},
        "statements": [{"property": "P50", "value_type": "item", "value": statement_value}],
        "existing_qid": "Q9001",
        "publication_target_qid": {"site": "test.wikidata.org", "qid": "Q9001"},
    }
    if local_targets is not None:
        document["publication_local_targets"] = local_targets
    return GatewayMutation(
        entity_key="manuscript:1",
        entity_type="manuscript",
        action="update",
        target_qid="Q9001",
        payload_digest="payload",
        entity_digest="1" * 64,
        document=document,
        identity_assertions=(),
        expected_revision=7,
    )


@pytest.mark.asyncio
async def test_test_wiki_local_reference_uses_sealed_target_for_adapter() -> None:
    uploader = _TestUpdateUploader()
    boundary = CurrentWikidataBoundary(uploader=uploader)
    mutation = _test_local_reference_update(_SEALED_LOCAL_STATEMENT, _SEALED_LOCAL_TARGETS)
    item = boundary._native_item_from_mutation(mutation)

    boundary._register_test_target(item, mutation)

    assert uploader._verified_test_targets == {"manuscript:1": "Q9001", "work:ledger": "Q9002"}
    assert uploader._test_local_target_issues(item) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("local_targets", [
    None,
    {"work:ledger": {"site": "test.wikidata.org", "qid": "Q9003"}},
])
async def test_test_wiki_unverified_local_reference_fails_closed(local_targets: object) -> None:
    uploader = _TestUpdateUploader()
    boundary = CurrentWikidataBoundary(uploader=uploader)
    mutation = _test_local_reference_update(_SEALED_LOCAL_STATEMENT, local_targets)
    item = boundary._native_item_from_mutation(mutation)

    boundary._register_test_target(item, mutation)

    assert uploader._test_local_target_issues(item) == ["P50 local target lacks test-ledger proof"]


@pytest.mark.asyncio
async def test_test_wiki_local_target_proof_must_name_test_wiki() -> None:
    from app.publication.wikidata_gateway import WikidataPermanentError

    uploader = _TestUpdateUploader()
    boundary = CurrentWikidataBoundary(uploader=uploader)
    production_proof = {"work:ledger": {"site": "www.wikidata.org", "qid": "Q9002"}}
    mutation = _test_local_reference_update(_SEALED_LOCAL_STATEMENT, production_proof)
    item = boundary._native_item_from_mutation(mutation)

    with pytest.raises(WikidataPermanentError, match="sealed test-ledger proof"):
        boundary._register_test_target(item, mutation)


@pytest.mark.asyncio
async def test_test_wiki_bare_qid_reference_stays_refused() -> None:
    uploader = _TestUpdateUploader()
    boundary = CurrentWikidataBoundary(uploader=uploader)
    mutation = _test_local_reference_update("Q9002", _SEALED_LOCAL_TARGETS)
    item = boundary._native_item_from_mutation(mutation)

    boundary._register_test_target(item, mutation)

    assert uploader._test_local_target_issues(item) == ["P50 item has an ambiguous wiki namespace"]
