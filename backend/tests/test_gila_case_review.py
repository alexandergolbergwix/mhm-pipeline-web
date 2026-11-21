import json
import uuid
import zlib
from base64 import b64encode
from unittest.mock import AsyncMock, Mock

import pytest
from app.models.run import RunRecord
from app.models.run_job import RunJob
from app.models.user import User
from app.pipeline import wikidata_gila_case_review as worker
from app.routers import admin_gila_repairs
from sqlalchemy import select

CN = "990000000000000001"
QID = "Q42"


def _scribe_record(*, repeat=False):
    field = {"tag": "700", "subfields": [
        {"code": "a", "value": "Named scribe"},
        {"code": "e", "value": "scribe"},
        {"code": "1", "value": "https://www.wikidata.org/entity/Q42"},
    ]}
    return {"001": CN, "_control_number": CN, "marc_occurrences": [field] * (2 if repeat else 1)}


def _human():
    return {"Q42": {"id": "Q42", "lastrevid": 345, "claims": {"P31": [{
        "mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q5"}}},
    }]}}}


def test_flat_marc_and_multiple_occurrences_cannot_confirm_an_identity():
    raw = {"001": CN, "_control_number": CN, "700$a": "Named scribe", "700$e": "scribe",
           "700$1": "https://www.wikidata.org/entity/Q42"}
    for record in (raw, _scribe_record(repeat=True)):
        excerpts, _, source_qid = worker._source_evidence(record, scribe=True)
        assert excerpts
        assert source_qid is None
        review = worker.ModelReview(candidate_qid=QID, explanation="A possible match.")
        assert worker._decision(review, _human(), source_qid, scribe=True) == "candidate"


def test_foreign_qid_from_model_is_rejected():
    review = worker.ModelReview(candidate_qid="Q999", explanation="A different candidate.")
    with pytest.raises(ValueError, match="outside the checked"):
        worker._decision(review, _human(), QID, scribe=True)


def test_production_place_requires_the_exact_region_and_country():
    record = {
        "001": CN, "751$a": '"Mikulov (Jihomoravský kraj, Czech Republic)"',
        "751$e": '"place of writing"',
    }
    parts = worker._place_parts(record)
    assert parts == ("Mikulov", "Jihomoravský kraj", "Czech Republic")
    assert worker._place_parts({**record, "751$e": "related place"}) is None
    assert worker._place_parts({**record, "751$e": "place of writing|related place"}) is None

    def claim(value):
        return {"mainsnak": {"snaktype": "value", "datavalue": {"value": value}}}

    def named(name):
        return {"labels": {"cs": {"value": name}}}

    place = {**named("Mikulov"), "claims": {
        "P17": [claim({"id": "Q213"})],
        "P131": [claim({"id": "Q582488"})],
        "P625": [claim({"latitude": 48.8, "longitude": 16.6})],
    }}
    context = {
        "Q213": named("Czech Republic"),
        "Q582488": {"claims": {"P131": [claim({"id": "Q192697"})]}},
        "Q192697": named("Jihomoravský kraj"),
    }
    assert worker._places_agree(place, context, parts)
    assert not worker._places_agree(place, context, (parts[0], "Another region", parts[2]))
    assert not worker._places_agree(place, context, (parts[0], parts[1], "Another country"))


@pytest.mark.asyncio
async def test_admin_can_run_and_reopen_a_case_review(sample_run, db_session, monkeypatch):
    run_id = sample_run["run_id"]
    user = await db_session.get(User, sample_run["user_id"])
    user.role = "admin"
    record = await db_session.scalar(select(RunRecord).where(RunRecord.run_id == run_id))
    record.marc = _scribe_record()
    case = {
        "case_id": "source:scribe:1", "reason": "named_scribe_identity_unresolved",
        "disposition": "manual_review", "control_number": CN, "qid": "Q10",
    }
    audit = RunJob(
        project_id=sample_run["project_id"], run_id=run_id, created_by=user.id,
        kind=worker.KIND, status="succeeded", result={},
        params={"_gila_cases": b64encode(zlib.compress(json.dumps([case]).encode())).decode()},
    )
    db_session.add(audit)
    await db_session.commit()
    monkeypatch.setattr(admin_gila_repairs, "_run_id", lambda: run_id)
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", Mock())
    monkeypatch.setattr(worker, "search_candidates", AsyncMock(return_value=[QID]))
    monkeypatch.setattr(worker.ReferenceItemReader, "fetch_batch", AsyncMock(return_value=_human()))
    model = AsyncMock(return_value=worker.ModelReview(
        candidate_qid=QID, explanation="The source links the scribe to Q42.",
    ))
    monkeypatch.setattr(worker, "_ask_model", model)

    client = sample_run["client"]
    endpoint = "/api/admin/wikidata-gila-repairs/case-review"
    params = {"audit_id": str(audit.id), "case_id": case["case_id"]}
    result = await client.post(endpoint, json=params)
    assert result.status_code == 200, result.text
    job_id = uuid.UUID(result.json()["id"])
    job = await db_session.get(RunJob, job_id)
    job.status = "running"
    await db_session.commit()
    await worker.run_gila_case_review(job_id)
    result = await client.get(endpoint, params=params)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "succeeded", result.text
    assert result.json()["result"]["decision"] == "verified_match"
    assert result.json()["result"]["candidate_revision"] == 345
    model.assert_awaited_once()
    assert model.await_args.args[0]["role"] == "scribe"
    generic = await client.post(f"/api/runs/{run_id}/jobs", json={"kind": job.kind})
    assert generic.status_code == 403
