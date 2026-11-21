import json
import uuid
import zlib
from base64 import b64encode
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest
from app.models.run_job import RunJob
from app.models.user import User
from app.pipeline import wikidata_gila_audit_job as audit_worker
from app.pipeline import wikidata_publication_prepare_job as prepare_worker
from app.pipeline.marc_verify_context import load_run_marc_records
from app.publication.credentials import configured_publication_gateway_factory
from app.publication.runtime import (
    PublicationRuntime,
    PublicationSourceError,
    StudioCacheProjectionSource,
)
from app.publication.types import ProfileRef, PublicationEntity
from app.publication.wikidata_gateway import (
    CurrentWikidataBoundary,
    RemoteEntitySnapshot,
    _work_target_identity_error,
)
from app.routers import admin_gila_repairs
from app.schemas.publication import PreparePublicationRequest
from converter.wikidata import troki_author_repair as troki

from tests.test_publication_router import _add_canonical_cache, _canonical_item


def _source_work():
    item = _canonical_item(troki.WORK_LOCAL_ID, "Ḥizzuq Emunah")
    item["records"] = [troki.WORK_SOURCE, troki.MANUSCRIPT_SOURCE]
    item["work_candidate_evidence"] = [{"title": "חזוק אמונה", "accepted": True}]
    refs = [
        {"property": "P248", "value": "Q118384267", "type": "item"},
        {"property": "P3959", "value": troki.WORK_SOURCE, "type": "external-id"},
    ]
    item["statements"].append({
        "property": "P50", "value": troki.AUTHOR_QID, "value_type": "item",
        "qualifiers": [{"property": "P1932", "value": troki.AUTHOR_HEADING, "type": "string"}],
        "references": refs, "reference_blocks": [refs],
    })
    return item


def _snak(value):
    return {"datavalue": {"value": value}}


def _claim(value, control=None):
    return {
        "mainsnak": _snak(value),
        "references": [{"snaks": {
            "P3959": [_snak(control)], "P248": [_snak({"id": "Q118384267"})],
        }}] if control else [],
    }


def _live_entities():
    return {
        troki.WORK_QID: {
            "id": troki.WORK_QID, "lastrevid": 10, "labels": {"he": {"value": "חזוק אמונה"}},
            "claims": {
                "P31": [_claim({"id": "Q47461344"}, troki.WORK_SOURCE)],
                "P2093": [_claim(troki.AUTHOR_HEADING, troki.WORK_SOURCE)],
            },
        },
        troki.MANUSCRIPT_QID: {
            "id": troki.MANUSCRIPT_QID,
            "claims": {
                "P3959": [_claim(troki.MANUSCRIPT_SOURCE)],
                "P1574": [_claim({"id": troki.WORK_QID}, troki.MANUSCRIPT_SOURCE)],
            },
        },
        troki.AUTHOR_QID: {
            "id": troki.AUTHOR_QID,
            "claims": {
                "P31": [_claim({"id": "Q5"})],
                "P8189": [_claim("987007297464205171")],
                "P214": [_claim("67288238")],
            },
        },
    }


@pytest.mark.parametrize("changed", [
    None, "origin", "manuscript", "author", "name", "mapped_target", "authority",
])
def test_audit_offers_the_author_repair_only_with_exact_evidence(changed):
    item = _source_work()
    entities = _live_entities()
    if changed == "origin":
        entities[troki.WORK_QID]["claims"]["P31"][0]["references"] = []
    if changed == "manuscript":
        entities[troki.MANUSCRIPT_QID]["claims"]["P1574"] = []
    if changed == "author":
        entities[troki.WORK_QID]["claims"]["P50"] = [_claim({"id": "Q999"})]
    if changed == "name":
        entities[troki.WORK_QID]["claims"]["P2093"] = [_claim("Another author", troki.WORK_SOURCE)]
    if changed == "authority":
        entities[troki.AUTHOR_QID]["claims"]["P8189"] = []
    if changed == "mapped_target":
        item["existing_qid"] = "Q98"
        entities["Q98"] = deepcopy(entities[troki.WORK_QID])
        entities["Q98"]["id"] = "Q98"
    case, = audit_worker._work_author_cases([item], entities)
    assert case["can_prepare"] is (changed is None)
    assert case.get("repair_kind") == ("troki_author" if changed is None else None)


@pytest.mark.asyncio
async def test_exact_author_release_contains_only_the_sourced_author(sample_run, db_session):
    run_id = sample_run["run_id"]
    user = await db_session.get(User, sample_run["user_id"])
    await _add_canonical_cache(db_session, run_id, items=[_source_work()])
    runtime = PublicationRuntime(
        session=db_session, gateway_factory=configured_publication_gateway_factory,
    )
    request = PreparePublicationRequest.model_validate({
        "profile_id": "mhm-wikidata", "profile_version": "1-nodes", "target": "live",
        "source": {"projection_source": "canonical"},
    })
    response = await runtime.prepare(
        run_id=run_id, request=request, actor_id=str(user.id), troki_author_only=True,
        selected_existing={troki.WORK_LOCAL_ID: troki.WORK_QID},
    )
    assert response.publication.current_release.entity_count == 1
    source = runtime._projection_source
    snap = await source.current_snapshot(run_id=run_id, source="canonical", approved_only=True)
    [page] = [page async for page in source.project(snap, ProfileRef("mhm-wikidata", "1-nodes"))]
    [draft] = page
    assert draft.document["labels"] == {}
    assert draft.document["existing_qid"] == troki.WORK_QID
    assert [row["property"] for row in draft.document["statements"]] == ["P50"]
    entity = PublicationEntity(
        release_id="test", entity_key=draft.entity_key, entity_type=draft.entity_type,
        document=draft.document, entity_digest="digest", evidence_refs=draft.evidence_refs,
        identity_assertions=draft.identity_assertions, local_references=draft.local_references,
    )
    native = CurrentWikidataBoundary._native_item(entity, target_qid=troki.WORK_QID)
    [statement] = native.statements
    assert (statement.property_id, statement.value) == ("P50", troki.AUTHOR_QID)
    assert troki.has_author_evidence(statement.qualifiers, statement.reference_blocks)
    live = _live_entities()[troki.WORK_QID]
    remote = RemoteEntitySnapshot(qid=troki.WORK_QID, revision=10, fingerprint="x", document=live)
    assert _work_target_identity_error(entity, remote) is None
    live["claims"]["P50"] = [_claim({"id": "Q999"})]
    assert _work_target_identity_error(entity, remote) == "The exact author repair evidence changed"
    with pytest.raises(PublicationSourceError, match="target changed"):
        await runtime.prepare(
            run_id=run_id, request=request, actor_id=str(user.id), troki_author_only=True,
            selected_existing={troki.WORK_LOCAL_ID: "Q999"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["current", "foreign", "changed_manuscript"])
async def test_prepare_author_repair_from_admin_audit(
    sample_run, db_session, monkeypatch, condition,
):
    run_id = sample_run["run_id"]
    user = await db_session.get(User, sample_run["user_id"])
    user.role = "admin"
    item = _source_work()
    await _add_canonical_cache(db_session, run_id, items=[item])
    snapshot = await StudioCacheProjectionSource(db_session).current_snapshot(
        run_id=run_id, source="canonical", approved_only=True,
    )
    records = await load_run_marc_records(db_session, run_id)
    entities = _live_entities()
    cases = audit_worker._work_author_cases([item], entities)
    report = RunJob(
        project_id=sample_run["project_id"], run_id=run_id, created_by=user.id,
        kind=audit_worker.KIND, status="succeeded", result={},
        params={
            "_gila_cases": b64encode(zlib.compress(json.dumps(cases).encode())).decode(),
            "_gila_build_revision": snapshot.revision,
            "_gila_build_digest": snapshot.digest,
            "_gila_source_fingerprint": audit_worker.gila_record_fingerprint(records),
        },
    )
    db_session.add(report)
    await db_session.commit()

    if condition == "changed_manuscript":
        entities[troki.MANUSCRIPT_QID]["claims"]["P1574"] = []
    monkeypatch.setattr(admin_gila_repairs, "_run_id", lambda: run_id)
    reader = prepare_worker.ReferenceItemReader
    creator = AsyncMock(return_value=7 if condition == "foreign" else 6935067)
    monkeypatch.setattr(reader, "creator_uid", creator)
    fetch = AsyncMock(side_effect=entities.__getitem__)
    monkeypatch.setattr(reader, "fetch", fetch)
    spawn = Mock()
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", spawn)

    response = await sample_run["client"].post(
        "/api/admin/wikidata-gila-repairs/prepare",
        json={"audit_id": str(report.id), "case_id": cases[0]["case_id"]},
    )
    creator.assert_awaited_once_with(troki.WORK_QID)
    if condition == "foreign":
        assert response.status_code == 403
        spawn.assert_not_called()
        fetch.assert_not_awaited()
        return

    assert response.status_code == 200, response.text
    job_id = uuid.UUID(response.json()["id"])
    await prepare_worker.run_wikidata_publication_prepare_job(job_id)
    job = await db_session.get(RunJob, job_id)
    assert {call.args[0] for call in fetch.await_args_list} == set(entities)
    if condition == "changed_manuscript":
        assert job.status == "failed"
        assert "repair evidence changed" in job.error
        assert job.result is None
    else:
        assert job.status == "succeeded", job.error
        assert job.result["publication_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("all_foreign", [False, True])
async def test_batch_seals_all_owned_targets_and_excludes_other_creators(
    sample_run, db_session, monkeypatch, all_foreign,
):
    from app.models.publication import Publication, PublicationEntityRow
    from sqlalchemy import select

    run_id = sample_run["run_id"]
    user = await db_session.get(User, sample_run["user_id"])
    user.role = "admin"
    exact = _source_work()
    items = [exact]
    cases = audit_worker._work_author_cases([exact], _live_entities())
    for index in (42, 43, 44):
        item = _canonical_item(f"work:{index}", f"Work {index}")
        item["existing_qid"] = f"Q{index}"
        items.append(item)
        row = {
            "case_id": f"repair:{index}", "local_id": item["local_id"],
            "qid": item["existing_qid"], "can_prepare": True,
        }
        cases.append(row)
        if index == 42:
            cases.append({**row, "case_id": "repair:repeat"})
    cases.append({"case_id": "source:review", "disposition": "manual_review"})
    await _add_canonical_cache(db_session, run_id, items=items)
    snapshot = await StudioCacheProjectionSource(db_session).current_snapshot(
        run_id=run_id, source="canonical", approved_only=True,
    )
    records = await load_run_marc_records(db_session, run_id)
    audit = RunJob(
        project_id=sample_run["project_id"], run_id=run_id, created_by=user.id,
        kind=audit_worker.KIND, status="succeeded", result={},
        params={
            "_gila_cases": b64encode(zlib.compress(json.dumps(cases).encode())).decode(),
            "_gila_build_revision": snapshot.revision,
            "_gila_build_digest": snapshot.digest,
            "_gila_source_fingerprint": audit_worker.gila_record_fingerprint(records),
        },
    )
    db_session.add(audit)
    await db_session.commit()
    monkeypatch.setattr(admin_gila_repairs, "_run_id", lambda: run_id)
    reader = prepare_worker.ReferenceItemReader
    owners = {troki.WORK_QID: 6935067, "Q42": 6935067, "Q43": 7, "Q44": None}
    creator = AsyncMock(side_effect=lambda qid: 7 if all_foreign else owners[qid])
    monkeypatch.setattr(reader, "creator_uid", creator)
    monkeypatch.setattr(reader, "fetch", AsyncMock(side_effect=_live_entities().__getitem__))
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", Mock())

    client = sample_run["client"]
    endpoint = "/api/admin/wikidata-gila-repairs"
    request = {"audit_id": str(audit.id)}
    response = await client.post(f"{endpoint}/prepare-batch", json=request)
    assert response.status_code == 200, response.text
    job_id = uuid.UUID(response.json()["id"])
    duplicate = await client.post(f"{endpoint}/prepare-batch", json=request)
    assert duplicate.json()["id"] == str(job_id)
    creator.assert_not_awaited()
    await prepare_worker.run_wikidata_publication_prepare_job(job_id)
    job = await db_session.get(RunJob, job_id)
    assert job.status == "succeeded", job.error
    assert creator.await_count == 4
    if all_foreign:
        assert job.result["batch_counts"] == {"eligible": 4, "prepared": 0}
        assert all(row["reason"] == "other_creator" for row in job.result["exclusions"])
        assert "publication_id" not in job.result
        return
    assert job.result["batch_counts"] == {"eligible": 4, "prepared": 2}
    assert job.result["exclusions"] == [
        {"qid": "Q43", "reason": "other_creator"},
        {"qid": "Q44", "reason": "creator_unavailable"},
    ]

    publication = await db_session.get(Publication, uuid.UUID(job.result["publication_id"]))
    docs = {row.entity_key: row.document for row in (await db_session.scalars(
        select(PublicationEntityRow).where(
            PublicationEntityRow.release_id == publication.latest_release_id,
        ),
    )).all()}
    assert set(docs) == {troki.WORK_LOCAL_ID, "work:42"}
    assert docs["work:42"]["publication_gila_repair_qid"] == "Q42"
    assert troki.REPAIR_MARKER not in docs["work:42"]
    assert [row["property"] for row in docs[troki.WORK_LOCAL_ID]["statements"]] == ["P50"]
    assert docs[troki.WORK_LOCAL_ID][troki.REPAIR_MARKER] is True
    saved = await client.get(f"{endpoint}/preparations")
    assert saved.json()["items"][0]["open_case_counts"] == {"manual_review": 1, "not_checked": 0}
    duplicate = await client.post(f"{endpoint}/prepare-batch", json=request)
    assert duplicate.json()["id"] == str(job_id)
