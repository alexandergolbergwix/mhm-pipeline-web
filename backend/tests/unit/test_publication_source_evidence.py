"""Publication must not discard source-scoped work identity."""

from dataclasses import replace

import pytest

from app.publication.digests import entity_digest, freeze_json, thaw_json
from app.publication.gateway import (
    GatewayMutation,
    GatewayWriteRequest,
)
from app.publication.types import PublicationEntity, PublicationEntityInput
from app.publication.wikidata_gateway import (
    CurrentWikidataBoundary,
    RemoteEntitySnapshot,
    _SafeWikidataGatewaySession,
)


def _work(document):
    return PublicationEntity(
        release_id="release", entity_key="work:1", entity_type="work",
        entity_digest="digest", document=freeze_json(document), evidence_refs=(),
        identity_assertions=(), local_references=(),
    )


@pytest.mark.parametrize("field", ["records", "work_candidate_evidence"])
def test_source_evidence_is_sealed_and_digest_bound(field):
    document = {
        "records": ["990000000000000001"],
        "work_candidate_evidence": [{"identity_scope": "record", "author_name": "Author"}],
    }
    original = PublicationEntityInput("work:1", "work", document)
    frozen = freeze_json(document)
    changed = dict(document)
    changed[field] = []
    assert entity_digest(original) != entity_digest(replace(original, document=changed))
    document[field].clear()
    assert thaw_json(frozen)[field]


@pytest.mark.parametrize("document", [
    {"records": ["record-1"], "work_candidate_evidence": [{"identity_scope": "invalid"}]},
    {"records": ["record-1"], "work_candidate_evidence": ["invalid"]},
    {"records": ["record-1"]},
    {"records": [42], "work_candidate_evidence": []},
])
def test_invalid_evidence_blocks_native_construction(document):
    with pytest.raises(ValueError, match="sealed work"):
        CurrentWikidataBoundary._native_item(_work(document))


def _document():
    return {
        "records": ["record-1"],
        "work_candidate_evidence": [{"identity_scope": "record", "author_name": "Author"}],
        "statements": [{"property": "P2093", "value": "Author"}],
        "existing_qid": "Q42",
    }


def _remote(record):
    return {"claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q47461344"}}}}], "P2093": [{
        "mainsnak": {"datavalue": {"value": "Author"}},
        "references": [{"snaks": {"P3959": [{"datavalue": {"value": record}}]}}],
    }]}}


def test_native_preserves_sealed_evidence():
    item = CurrentWikidataBoundary._native_item(_work(_document()))
    assert item.records == ["record-1"]
    assert item.work_candidate_evidence == _document()["work_candidate_evidence"]
    from converter.wikidata.work_identity import work_identity_for_item
    assert work_identity_for_item(item).source_scoped


@pytest.mark.parametrize("fresh", [False, True])
def test_source_scoped_absence_is_not_a_duplicate_check(monkeypatch, fresh):
    import app.pipeline.wikidata_upload as upload
    import converter.wikidata.reconciler as reconciler

    class Uploader:
        _is_test = False

    monkeypatch.setattr(upload, "_reconcile_sync", lambda items: [
        upload.ReconcileOutcome(item.local_id, "", "work", None, "source_scoped_work", "")
        for item in items
    ])
    monkeypatch.setattr(upload, "_reconcile_for_upload", lambda *args: (None, "source_scoped_work"))
    monkeypatch.setattr(reconciler, "WikidataReconciler", lambda: object())
    document = _document()
    document.pop("existing_qid")
    document["labels"] = {"en": "A work"}
    observation = CurrentWikidataBoundary(uploader=Uploader())._reconcile_sync(
        (_work(document),), fresh=fresh,
    )[0]
    assert observation.status == "unknown"
    assert "duplicate check" in observation.detail


def test_missing_test_target_does_not_become_a_source_scoped_create(monkeypatch):
    import app.pipeline.wikidata_upload as upload

    class Uploader:
        _is_test = True

        def _wbgetentities(self, ids, *, props):
            return {"Q42": {"missing": ""}}

    monkeypatch.setattr(upload, "_reconcile_sync", lambda items: [
        upload.ReconcileOutcome(item.local_id, "", "work", "Q42", "p2888_identifier", "")
        for item in items
    ])
    document = _document()
    document.pop("existing_qid")
    observation = CurrentWikidataBoundary(uploader=Uploader())._reconcile_sync(
        (_work(document),),
    )[0]
    assert observation.status == "unknown"


@pytest.mark.parametrize(("record", "expected"), [
    ("record-1", "present_owned"), ("record-2", "unknown"), ("", "unknown"),
])
def test_sealed_qid_checks_remote_source(record, expected):
    class Uploader:
        _is_test = False

        def _wbgetentities(self, ids, *, props):
            return {"Q42": {"id": "Q42", "lastrevid": 3, **_remote(record)}}

        def _get_authenticated_user(self):
            return "user"

        def _get_first_revision_author(self, qid):
            return "user"

        def _is_our_item(self, qid):
            return True

    boundary = CurrentWikidataBoundary(uploader=Uploader())
    observation = boundary._reconcile_sync((_work(_document()),))[0]
    assert observation.status == expected


@pytest.mark.asyncio
async def test_old_update_plan_rechecks_source_before_send():
    class Boundary:
        async def fetch_entity(self, qid):
            return RemoteEntitySnapshot(qid, 3, "fingerprint", _remote("record-2"))

        async def write_once(self, request):
            raise AssertionError("An incompatible update must not be sent")

    mutation = GatewayMutation(
        entity_key="work:1", entity_type="work", action="update", target_qid="Q42",
        payload_digest="payload", entity_digest="digest", document=freeze_json(_document()),
        identity_assertions=(), expected_revision=3,
    )
    outcome = await _SafeWikidataGatewaySession(Boundary()).write(
        GatewayWriteRequest("intent", "request", mutation),
    )
    assert outcome.status == "blocked"
