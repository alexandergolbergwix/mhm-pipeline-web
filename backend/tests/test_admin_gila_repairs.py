import json
import uuid
import zlib
from base64 import b64encode
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.models.run_job import RunJob
from app.models.user import User
from app.pipeline import wikidata_gila_audit_job as worker
from app.pipeline.wikidata_gila_repair_job import RepairCycle
from app.pipeline import wikidata_publication_prepare_job as prepare_worker
from app.pipeline.marc_verify_context import load_run_marc_records
from app.pipeline.wikidata_qid_ledger import record_ledger_mapping
from app.publication.runtime import StudioCacheProjectionSource
from app.publication.types import TargetRef
from app.publication.wikidata_gateway import CredentialMaterial
from app.routers import admin_gila_repairs as router
from app.routers import wikidata_studio
from fastapi import HTTPException

from tests.test_publication_router import _add_canonical_cache, _canonical_item


def test_public_audit_snapshot_retains_exact_entities_across_batches(tmp_path):
    snapshot = worker.PublicAuditSnapshot(str(tmp_path))
    first = {"id": "Q1", "lastrevid": 12, "claims": {}}
    snapshot.add_batch({"Q1": first})
    assert snapshot.get("Q1") == first
    snapshot.add_batch({"Q2": {"id": "Q2", "lastrevid": 13, "claims": {}}})
    assert list(snapshot) == ["Q1", "Q2"]
    assert snapshot.get("Q3") is None
    with pytest.raises(ValueError, match="one response per exact QID"):
        snapshot.add_batch({"Q1": first})


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [
    {"requirement": 5}, {"scope": "people_evidence_batch"}, {"scope": "people_authority"},
    {"scope": "sources_append_only"}, {"scope": "all_requirements"},
])
async def test_automatic_repair_requires_admin_and_attaches_to_active_job(sample_run, db_session, monkeypatch, scope):
    client = sample_run["client"]
    user = await db_session.get(User, sample_run["user_id"])
    monkeypatch.setattr(router, "_run_id", lambda: sample_run["run_id"])
    spawn = Mock()
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", spawn)
    credential = CredentialMaterial(
        credential_id=f"wikidata:production:{user.id}",
        target=TargetRef(site="www.wikidata.org", environment="production"),
        secret="fixture-credential",
    )
    monkeypatch.setattr(router.SavedPublicationCredentialResolver, "resolve", AsyncMock(return_value=credential))
    monkeypatch.setattr(router, "seal_execution_credential", Mock(return_value="fixture-envelope"))
    endpoint = "/api/admin/wikidata-gila-repairs/repair-all"
    assert (await client.post(endpoint)).status_code == 403
    user.role = "admin"
    await db_session.commit()
    assert (await client.post(f"/api/runs/{sample_run['run_id']}/jobs", json={
        "kind": "wikidata_gila_repair", "params": {},
    })).status_code == 403
    first = await client.post(endpoint, json=scope)
    assert first.status_code == 200
    repeated = await client.post(endpoint)
    assert first.json()["id"] == repeated.json()["id"]
    assert first.json()["params"] == {}
    job = await db_session.get(RunJob, spawn.call_args.args[0])
    assert job.params["_publication_credential"] == "fixture-envelope"
    assert job.params["_cycle"].startswith("gila-repair:")
    for key, value in scope.items():
        assert job.params[f"_gila_{key}"] == value
    assert (await client.post(endpoint, json={"scope": "people_and_sources"})).status_code == 409
    spawn.assert_called_once()


@pytest.mark.asyncio
async def test_source_preflight_download_uses_saved_marc_audit(sample_run, db_session, monkeypatch):
    run_id = sample_run["run_id"]
    user = await db_session.get(User, sample_run["user_id"])
    monkeypatch.setattr(router, "_run_id", lambda: run_id)
    cn = "990000403370205171"
    field = {
        "tag": "590", "occurrence": 1, "indicators": [" ", " "],
        "subfields": [{"code": "a", "value": "Temporary catalogue record"}],
    }
    saved_marc = b64encode(zlib.compress(json.dumps({cn: {"marc_occurrences": [field]}}).encode())).decode()
    audit = RunJob(
        id=uuid.uuid4(), project_id=sample_run["project_id"], run_id=run_id, created_by=user.id,
        kind=worker.KIND, status="succeeded", result={}, params={"_gila_people_marc": saved_marc},
    )
    repair = RunJob(
        id=uuid.uuid4(), project_id=sample_run["project_id"], run_id=run_id, created_by=user.id,
        kind="wikidata_gila_repair", status="succeeded",
        result={"corpus_preflight": {
            "source_archive": True, "baseline_audit_id": str(audit.id), "source_audit_id": str(audit.id),
        }},
        params={
            "_publication_credential": "private-envelope",
            "_gila_corpus_preflight": [{
                "case_id": "source:note", "control_number": cn, "points": [8], "occurrence": "590:1:1",
                "status": "source_metadata", "outcome": "unresolved",
            }],
        },
    )
    db_session.add_all([audit, repair])
    await db_session.commit()
    endpoint = "/api/admin/wikidata-gila-repairs/preflight"
    client = sample_run["client"]
    params = {"job_id": str(repair.id), "archive": "true"}
    assert (await client.get(endpoint, params=params)).status_code == 403
    user.role = "admin"
    await db_session.commit()
    response = await client.get(endpoint, params=params)
    assert response.status_code == 200
    assert response.json()["cases"][0]["marc"] == field
    assert response.json()["cases"][0]["outcome"] == "unresolved"
    assert "private-envelope" not in response.text
    page = await client.get(endpoint, params={"job_id": str(repair.id), "status": "source_metadata"})
    assert page.status_code == 200
    assert page.json()["total"] == 1


def test_automatic_repair_selects_only_the_exact_audited_claim():
    correct = {"property": "P571", "value": "+1650-00-00T00:00:00Z", "precision": 9}
    wrong_precision = {**correct, "precision": 11}
    unrelated = {"property": "P50", "value": "Q42"}
    fact = {"property": "P571", "expected_value": correct["value"], "expected_precision": 9}
    assert prepare_worker.audited_statements(
        {"statements": [correct, wrong_precision, unrelated]}, [fact],
    ) == [correct]
    assert prepare_worker.audited_statements(
        {"statements": [correct, {**correct, "references": [{"property": "P248"}]}]}, [fact],
    ) == []
    assert prepare_worker.audited_statements(
        {"statements": [correct, {**correct, "references": [{"property": "P248"}]}, unrelated]},
        [fact, {"property": "P50", "expected_value": "Q42"}],
    ) == [unrelated]


@pytest.mark.parametrize(("scope", "subjects_only", "expected"), [
    ("sources_append_only", True, 100),
    ("sources_append_only", False, 50),
    ("people_authority", True, 50),
])
def test_repair_cycle_selects_maxlag_for_its_write_scope(scope, subjects_only, expected):
    cycle = RepairCycle(SimpleNamespace(
        id=uuid.uuid4(), run_id=uuid.uuid4(), project_id=uuid.uuid4(), created_by=uuid.uuid4(),
        params={
            "_publication_credential": "fixture-envelope", "_cycle": "fixture-cycle",
            "_gila_scope": scope, "_gila_subjects_only": subjects_only,
        },
    ))
    assert cycle.write_maxlag == expected


@pytest.mark.asyncio
async def test_full_repair_loads_the_final_audit_archive_when_no_item_needs_an_edit():
    cycle = RepairCycle(SimpleNamespace(
        id=uuid.uuid4(), run_id=uuid.uuid4(), project_id=uuid.uuid4(), created_by=uuid.uuid4(),
        params={
            "_publication_credential": "fixture-envelope", "_cycle": "fixture-cycle",
            "_gila_scope": "all_requirements",
        },
    ))
    result = {
        "email_points": [{"point": 5, "title": "People and roles", "status": "manual_review",
                          "counts": {"manual_review": 1}}],
        "reference_blocks": 0,
    }
    audit = SimpleNamespace(id=uuid.uuid4(), status="succeeded", result=result)
    final = SimpleNamespace(id=uuid.uuid4(), status="succeeded", result=result, params={})
    cycle.stage = AsyncMock(side_effect=[audit, final])
    cycle.result["people_batch"] = {"items_verified": 0}
    cycle.people_batch = AsyncMock(return_value=set())

    await cycle.run()

    assert cycle.stage.await_args.kwargs["full"] is True
    assert cycle.result["audit_id"] == str(final.id)
    assert cycle.result["people_batch"]["cases_verified"] == 0


@pytest.mark.asyncio
async def test_repair_cycle_rebuilds_then_runs_both_repair_scopes_and_verifies():
    cycle = RepairCycle(SimpleNamespace(
        id=uuid.uuid4(), run_id=uuid.uuid4(), project_id=uuid.uuid4(), created_by=uuid.uuid4(),
        params={"_publication_credential": "fixture-envelope", "_cycle": "fixture-cycle"},
    ))
    audit = SimpleNamespace(id=uuid.uuid4(), status="succeeded", result={"email_points": [
        {"point": 5, "title": "People and roles", "status": "repair_required",
         "counts": {"repair_required": 1, "manual_review": 1325}},
    ]})
    verified = SimpleNamespace(
        id=uuid.uuid4(), status="succeeded",
        result={"email_points": [
            {"point": 4, "title": "Ktiv membership", "status": "verified"},
            {"point": 5, "title": "People and roles", "status": "manual_review",
             "counts": {"verified": 1, "manual_review": 1325}},
        ], "reference_blocks": 459},
    )
    evidence = SimpleNamespace(id=uuid.uuid4(), status="succeeded", result={"checked": 2, "supported": 1})
    cycle.stage = AsyncMock(side_effect=[
        SimpleNamespace(status="failed", progress={"phase": "source_rebuild_required"}),
        SimpleNamespace(status="succeeded"), audit, evidence, verified,
    ])
    cycle.references = AsyncMock()
    cycle.people_batch = AsyncMock(return_value=set())
    cycle.publication = AsyncMock(side_effect=ValueError("The source changed before publication."))
    await cycle.run()
    assert [call.args[1] for call in cycle.stage.await_args_list] == [
        "initial_audit", "rebuild", "audit_after_rebuild", "find_evidence", "verify",
    ]
    assert [call.args for call in cycle.references.await_args_list] == [
        ("original", audit), ("remaining", audit),
    ]
    cycle.people_batch.assert_awaited_once_with(audit)
    cycle.publication.assert_awaited_once_with(audit, evidence)
    assert cycle.result["evidence_review"]["supported"] == 1
    assert cycle.result["requirements_verified"] == 1
    assert cycle.result["repeated_references"] == 459
    assert cycle.result["requirement_comparison"] == [
        {"point": 5, "title": "People and roles",
         "before": {"repair_required": 1, "manual_review": 1325},
         "after": {"verified": 1, "manual_review": 1325}},
    ]
    assert cycle.result["stages_needing_attention"][0]["stage"] == "publication"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["current", "source_changed", "foreign", "mapping_changed"])
async def test_repair_preparation_checks_freshness_in_the_worker(
    sample_run, db_session, monkeypatch, state,
):
    run_id = sample_run["run_id"]
    user = await db_session.get(User, sample_run["user_id"])
    user.role = "admin"
    item = _canonical_item("work:1", "A work")
    item["existing_qid"] = "Q42"
    await _add_canonical_cache(db_session, run_id, items=[item])
    snapshot = await StudioCacheProjectionSource(db_session).current_snapshot(
        run_id=run_id, source="canonical", approved_only=True,
    )
    records = await load_run_marc_records(db_session, run_id)
    cases = [{"case_id": "repair:1", "qid": "Q42", "local_id": "work:1", "can_prepare": True}]
    report = RunJob(
        project_id=sample_run["project_id"], run_id=run_id, created_by=user.id,
        kind=worker.KIND, status="succeeded", result={},
        params={
            "_gila_cases": b64encode(zlib.compress(json.dumps(cases).encode())).decode(),
            "_gila_build_revision": snapshot.revision,
            "_gila_build_digest": snapshot.digest,
            "_gila_source_fingerprint": "stale" if state == "source_changed"
            else worker.gila_record_fingerprint(records),
        },
    )
    db_session.add(report)
    await db_session.commit()
    monkeypatch.setattr(router, "_run_id", lambda: run_id)
    creator = AsyncMock(return_value=12 if state == "foreign" else 6935067)
    monkeypatch.setattr(router.ReferenceItemReader, "creator_uid", creator)
    fetch_item = AsyncMock(return_value=None if state == "mapping_changed" else item)
    monkeypatch.setattr(prepare_worker, "fetch_merged_wikidata_item", fetch_item)
    spawn = Mock()
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", spawn)
    check_current = wikidata_studio._studio_cache_is_current
    timeouts = []

    async def checked(*args, timeout_seconds=None, **kwargs):
        timeouts.append(timeout_seconds)
        return await check_current(*args, timeout_seconds=timeout_seconds, **kwargs)

    monkeypatch.setattr(wikidata_studio, "_studio_cache_is_current", checked)
    client = sample_run["client"]
    endpoint = "/api/admin/wikidata-gila-repairs"
    audit = {"audit_id": str(report.id)}
    if state == "current":
        response = await client.post(f"{endpoint}/prepare-next", json=audit)
    else:
        response = await client.post(f"{endpoint}/prepare", json={**audit, "case_id": "repair:1"})
    if state == "foreign":
        assert response.status_code == 403
        spawn.assert_not_called()
        return
    assert response.status_code == 200, response.text
    assert timeouts == []
    fetch_item.assert_not_awaited()
    job_id = uuid.UUID(response.json()["id"])
    if state == "current":
        resumed = await client.post(f"{endpoint}/prepare-next", json=audit)
        assert resumed.json()["id"] == str(job_id)
        assert creator.await_count == 1
    await prepare_worker.run_wikidata_publication_prepare_job(job_id)
    job = await db_session.get(RunJob, job_id)
    if state != "current":
        assert job.status == "failed"
        assert job.result is None
        assert timeouts == []
        assert fetch_item.await_count == (1 if state == "mapping_changed" else 0)
        return
    assert job.status == "succeeded", job.error
    assert job.result["publication_id"]
    restored = await client.get(f"{endpoint}/preparations")
    assert restored.json()["items"][0]["qids"] == ["Q42"]
    assert restored.json()["items"][0]["publication_id"] == job.result["publication_id"]
    assert restored.json()["items"][0]["case_id"] == "repair:1"
    exhausted = await client.post(f"{endpoint}/prepare-next", json=audit)
    assert exhausted.status_code == 200, exhausted.text
    assert exhausted.json() is None
    assert creator.await_count == 1
    fetch_item.assert_awaited_once()
    assert timeouts and set(timeouts) == {wikidata_studio.STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS}


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh", [True, False])
async def test_worker_streams_the_canonical_source_and_saves_read_only_report(
    sample_run,
    db_session,
    monkeypatch,
    fresh,
):
    run_id = sample_run["run_id"]
    user = await db_session.get(User, sample_run["user_id"])
    user.role = "admin"
    item = _canonical_item("manuscript:one", "One manuscript")
    item.update(entity_type="manuscript", existing_qid="Q42", records=["990000000000000001"])
    item["validation_issues"] = [{"code": "STALE_CATALOGUE_EDIT", "severity": "warning"}]
    await _add_canonical_cache(db_session, run_id, items=[item])
    await record_ledger_mapping(db_session, "wikidata:marc:990000000000000001", "Q42")
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=run_id,
        created_by=user.id,
        kind=worker.KIND,
        status="running",
        params={},
    )
    db_session.add(job)
    await db_session.commit()
    monkeypatch.setattr(worker, "load_seed", lambda: ({"manifest": {"qids": []}}, ""))
    monkeypatch.setattr(worker, "remaining_reference_inventory", lambda: {"items": []})
    entity = {
        "id": "Q42",
        "lastrevid": 22,
        "labels": {},
        "descriptions": {},
        "aliases": {},
        "sitelinks": {},
        "claims": {
            "P3959": [
                {
                    "rank": "normal",
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P3959",
                        "datavalue": {"type": "string", "value": "990000000000000001"},
                    },
                }
            ]
        },
    }
    batch = AsyncMock(return_value={"Q42": entity})
    monkeypatch.setattr(worker.ReferenceItemReader, "fetch_batch", batch)
    check_current = wikidata_studio._studio_cache_is_current
    timeout_values = []

    async def check_with_production_delay(*args, timeout_seconds=None, **kwargs):
        timeout_values.append(timeout_seconds)
        if timeout_seconds != wikidata_studio.STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS:
            raise HTTPException(409, "The source check exceeded the web request time limit.")
        return fresh and await check_current(*args, timeout_seconds=timeout_seconds, **kwargs)

    monkeypatch.setattr(wikidata_studio, "_studio_cache_is_current", check_with_production_delay)
    await worker.run_gila_audit_job(job.id)
    await db_session.refresh(job)
    if not fresh:
        assert job.status == "failed"
        assert job.progress["phase"] == "source_rebuild_required"
        assert job.result is None
        batch.assert_not_awaited()
        assert timeout_values == [wikidata_studio.STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS]
        return
    assert job.status == "succeeded", job.error
    assert job.result["record_count"] == job.result["public_read"] == 1
    assert len(job.result["email_points"]) == 9
    assert job.result["all_requirements_verified"] is False
    assert job.params["_gila_checked_manuscripts"] == [{
        "control_number": "990000000000000001",
        "qid": "Q42",
        "revision": 22,
        "disposition": "verified",
    }]
    assert any(case["case_id"] == "model:manuscript-and-work" for case in worker.gila_cases(job))
    stale_edit = next(
        case for case in worker.gila_cases(job)
        if case["case_id"] == "stale-catalogue-edit:manuscript:one"
    )
    assert stale_edit["points"] == [6]
    assert stale_edit["disposition"] == "manual_review"
    assert stale_edit["can_prepare"] is False
    assert job.result["email_points"][5]["counts"]["manual_review"] >= 1
    batch.assert_awaited_once()
    assert timeout_values == [wikidata_studio.STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS] * 2


@pytest.mark.asyncio
async def test_admin_audit_start_progress_and_case_pagination(sample_run, db_session, monkeypatch):
    client = sample_run["client"]
    run_id = sample_run["run_id"]
    user = await db_session.get(User, sample_run["user_id"])
    monkeypatch.setattr(router, "_run_id", lambda: run_id)
    spawn = Mock()
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", spawn)
    base = "/api/admin/wikidata-gila-repairs"
    assert (await client.get(base)).status_code == 403
    user.role = "admin"
    await db_session.commit()
    direct = await client.post(f"/api/runs/{run_id}/jobs", json={"kind": router.KIND, "params": {}})
    assert direct.status_code == 403
    launched = await client.post(base + "/start")
    assert launched.status_code == 200
    again = await client.post(base + "/start")
    assert again.json()["id"] == launched.json()["id"]
    spawn.assert_called_once()
    job = await db_session.get(RunJob, spawn.call_args.args[0])
    job.status = "succeeded"
    job.result = {
        "case_count": 4,
        "email_points": [{"point": 1, "status": "manual_review"}],
        "all_requirements_verified": False,
        "requirement_coverage": {"unchecked_passages": 3},
    }
    cases = [
        {
            "case_id": "a",
            "disposition": "manual_review",
            "points": [1],
            "qid": "Q1",
            "classification": "absent_in_source",
        },
        {"case_id": "b", "disposition": "manual_review", "points": [1], "qid": "Q2"},
        {"case_id": "c", "disposition": "manual_review", "points": [8], "qid": "Q2"},
        {"case_id": "d", "disposition": "repair_required", "points": [8], "can_prepare": True},
    ]
    job.params = {"_gila_cases": b64encode(zlib.compress(json.dumps(cases).encode())).decode()}
    db_session.add(RunJob(
        project_id=sample_run["project_id"],
        run_id=run_id,
        created_by=user.id,
        kind="wikidata_reference_repair",
        status="succeeded",
        params={
            "reference_scope": "remaining",
            "_reference_checkpoint": {"results": [
                {"qid": "Q6", "status": "verified", "corrected": True, "after_revision": 24},
                {"qid": "Q7", "status": "outcome_unknown", "corrected": False},
            ]},
        },
    ))
    await db_session.commit()
    status = await client.get(base)
    assert status.json()["report"]["case_count"] == 4
    assert "_gila_cases" not in status.text
    response = await client.get(
        base + "/cases",
        params={"audit_id": str(job.id), "disposition": "manual_review", "query": "Q2", "limit": 1},
    )
    assert response.json() == {"items": [cases[1]], "total": 2}
    ready = await client.get(
        base + "/cases", params={"audit_id": str(job.id), "disposition": "ready_to_prepare"},
    )
    assert ready.json() == {"items": [cases[3]], "total": 1}
    result = await client.get(base + "/evidence", params={"audit_id": str(job.id)})
    assert result.status_code == 200
    assert result.json()["checked_manuscripts"] is None
    assert result.json()["unchecked_passages"] == 3
    receipts = [row for row in result.json()["reference_repairs"] if row["scope"] == "remaining"]
    assert [row["qid"] for row in receipts] == ["Q6"]
    assert receipts[0]["after_revision"] == 24
    rejected = await client.post(base + "/prepare", json={"audit_id": str(job.id), "case_id": "b"})
    assert rejected.status_code == 409
    injected = await client.post(
        base + "/prepare",
        json={"audit_id": str(job.id), "case_id": "b", "qid": "Q999"},
    )
    assert injected.status_code == 422
    user.role = "editor"
    await db_session.commit()
    assert (await client.get(f"/api/runs/{run_id}/jobs/{job.id}")).status_code == 403
    forbidden = await client.get(base + "/evidence", params={"audit_id": str(job.id)})
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_evidence_only_batch_seals_one_sourced_update(sample_run, db_session, monkeypatch):
    from app.models.publication import Publication, PublicationEntityRow
    from app.pipeline import wikidata_gila_case_review as evidence_worker
    from app.pipeline.wikidata_gila_identity_evidence import _repair_statement
    from app.pipeline.wikidata_source_choices import digest, source_hash
    from sqlalchemy import select

    cn = "990000000000000001"
    run_id, actor, project = (sample_run[key] for key in ("run_id", "user_id", "project_id"))
    local_id = f"QDraft_MS_{cn}"
    item = {
        **_canonical_item(local_id, "A manuscript"),
        "entity_type": "manuscript", "existing_qid": "Q10", "records": [cn],
    }
    await _add_canonical_cache(db_session, run_id, items=[item])
    snapshot = await StudioCacheProjectionSource(db_session).current_snapshot(
        run_id=run_id, source="canonical", approved_only=True,
    )
    records = await load_run_marc_records(db_session, run_id)
    case = {
        "case_id": "source:scribe", "reason": "named_scribe_identity_unresolved",
        "disposition": "manual_review", "control_number": cn, "qid": "Q10", "local_id": local_id,
    }
    audit = RunJob(
        id=uuid.uuid4(), project_id=project, run_id=run_id, created_by=actor,
        kind=worker.KIND, status="succeeded", result={},
        params={
            "_gila_cases": b64encode(zlib.compress(json.dumps([case]).encode())).decode(),
            "_gila_build_revision": snapshot.revision, "_gila_build_digest": snapshot.digest,
            "_gila_source_fingerprint": worker.gila_record_fingerprint(records),
        },
    )
    raw = {"001": cn, "_control_number": cn, "marc_occurrences": [{"tag": "700", "subfields": [
        {"code": "a", "value": "A scribe"}, {"code": "e", "value": "scribe"},
        {"code": "1", "value": "https://www.wikidata.org/entity/Q42"},
    ]}]}
    manuscript = {"id": "Q10", "lastrevid": 123, "claims": {"P3959": [{
        "mainsnak": {"snaktype": "value", "datavalue": {"value": cn}},
    }]}}
    candidate = {"id": "Q42", "lastrevid": 456, "claims": {"P31": [{
        "mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q5"}}},
    }]}}
    assert _repair_statement(raw, case, manuscript, candidate, {})
    parent_id = str(uuid.uuid4())
    evidence = RunJob(
        id=uuid.uuid4(), project_id=project, run_id=run_id, created_by=actor,
        kind="wikidata_gila_case_review", status="succeeded",
        result={"_repairs": [{
            "case_id": case["case_id"], "candidate_qid": "Q42", "candidate_revision": 456,
            "base_revision": 123, "source_hash": source_hash(raw), "authority_digest": digest([]),
        }]},
        params={"_audit_id": str(audit.id), "_gila_cycle_parent": parent_id,
                "_gila_cycle_step": "find_evidence"},
    )
    assert prepare_worker.audited_batch_params(audit, actor) is None
    params = prepare_worker.audited_batch_params(audit, actor, evidence=evidence)
    job = RunJob(
        id=uuid.uuid4(), project_id=project, run_id=run_id, created_by=actor,
        kind="wikidata_publication_prepare", status="running",
        params={**params, "_gila_cycle_parent": parent_id},
    )
    db_session.add_all([audit, evidence, job])
    await db_session.commit()
    monkeypatch.setattr(prepare_worker, "fetch_merged_wikidata_item", AsyncMock(return_value=item))
    monkeypatch.setattr(evidence_worker, "_case_record", AsyncMock(return_value=raw))
    monkeypatch.setattr(evidence_worker.ReferenceItemReader, "creator_uid", AsyncMock(return_value=6935067))
    monkeypatch.setattr(evidence_worker.ReferenceItemReader, "fetch_batch",
                        AsyncMock(return_value={"Q10": manuscript, "Q42": candidate}))
    await prepare_worker.run_wikidata_publication_prepare_job(job.id)
    await db_session.refresh(job)
    assert job.status == "succeeded", job.error
    publication = await db_session.get(Publication, uuid.UUID(job.result["publication_id"]))
    row = await db_session.scalar(select(PublicationEntityRow).where(
        PublicationEntityRow.release_id == publication.latest_release_id,
    ))
    assert row.document["publication_gila_repair_qid"] == "Q10"
    assert row.document["publication_gila_audited_revision"] == 123
    assert [(claim["property"], claim["value"]) for claim in row.document["statements"]] == [("P11603", "Q42")]
