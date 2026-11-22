import json
import uuid
import zlib
from base64 import b64encode
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace

import pytest
from app.models.run import RunRecord
from app.models.run_job import RunJob
from app.models.user import User
from app.pipeline import wikidata_gila_case_review as worker
from app.pipeline import wikidata_gila_identity_evidence as identity
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
        excerpts, _, source_qid = identity._source_evidence(record, scribe=True)
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
    parts = identity._place_parts(record)
    assert parts == ("Mikulov", "Jihomoravský kraj", "Czech Republic")
    assert identity._place_parts({**record, "751$e": "related place"}) is None
    assert identity._place_parts({**record, "751$e": "place of writing|related place"}) is None

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
    assert identity._places_agree(place, context, parts)
    assert not identity._places_agree(place, context, (parts[0], "Another region", parts[2]))
    assert not identity._places_agree(place, context, (parts[0], parts[1], "Another country"))


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


def test_exact_marc_and_authority_are_required_for_a_scribe_claim():
    raw = {
        "001": CN, "_control_number": CN,
        "700$a": "Named scribe|Former owner", "700$e": "scribe|former owner",
    }
    case = {"control_number": CN, "qid": "Q10", "reason": "named_scribe_identity_unresolved"}

    def snak(value, pid):
        return {"snaktype": "value", "property": pid, "datavalue": {"value": value}}

    person = _human()["Q42"]
    person["claims"]["P214"] = [{"mainsnak": snak("12345", "P214")}]
    manuscript = {"id": "Q10", "claims": {"P3959": [{"mainsnak": snak(CN, "P3959")}]}}
    evidence = SimpleNamespace(
        entity_kind="person", approved=True, confidence="high", source="viaf",
        role="scribe", entity_text="Named scribe", viaf_id="12345", mazal_id="", payload={},
    )
    assert identity._repair_statement(raw, case, manuscript, person, {}) is None
    statement = identity._repair_statement(raw, case, manuscript, person, {}, authority_rows=[evidence])
    assert statement["property"] == "P11603"
    assert statement["value"] == "Q42"
    assert statement["qualifiers"][0]["value"] == "Named scribe"
    assert any(row["property"] == "P3959" and row["value"] == CN
               for row in statement["reference_blocks"][0])

    reference = {"snaks": {"P3959": [snak(CN, "P3959")]}}
    manuscript["claims"]["P11603"] = [{
        "mainsnak": snak({"id": "Q42"}, "P11603"),
        "qualifiers": {"P1932": [snak("Named scribe", "P1932")]},
        "references": [reference],
    }]
    assert identity.live_source_match(
        raw, manuscript, {"Q42": person}, CN, scribe=True, name="Named scribe",
        authority_rows=[evidence],
    )
    assert identity._repair_statement(raw, case, manuscript, person, {}, authority_rows=[evidence]) is None

    del manuscript["claims"]["P11603"]
    evidence.entity_text = "Former owner"
    assert identity._repair_statement(raw, case, manuscript, person, {}, authority_rows=[evidence]) is None


@pytest.mark.asyncio
async def test_batch_case_excludes_foreign_creators_before_search(monkeypatch):
    monkeypatch.setattr(worker.ReferenceItemReader, "creator_uid", AsyncMock(return_value=123))
    inspect = AsyncMock()
    monkeypatch.setattr(worker, "inspect_case", inspect)
    assert await worker._batch_case({"qid": "Q10"}, uuid.uuid4(), uuid.uuid4()) == ("excluded_creator", None)
    inspect.assert_not_called()


def test_viaf_needs_an_exact_heading_and_both_marc_life_dates():
    raw = {
        "001": CN, "_control_number": CN,
        "700$a": "Named scribe", "700$e": "scribe", "700$d": "1650-1710",
    }
    cluster = {
        "viafID": "12345", "nameType": "Personal", "birthDate": "1650", "deathDate": "1710-01-02",
        "mainHeadings": {"data": [{"text": "Named scribe, 1650-1710"}]},
    }
    assert identity._viaf_proves_scribe(raw, "12345", cluster)
    assert not identity._viaf_proves_scribe(raw, "12345", {**cluster, "birthDate": "1651"})
    assert not identity._viaf_proves_scribe(raw, "12345", {**cluster, "viafID": "12346"})
    assert not identity._viaf_proves_scribe({**raw, "700$d": "1650?"}, "12345", cluster)

    person = _human()["Q42"]
    person["claims"]["P214"] = [{"mainsnak": {"snaktype": "value", "datavalue": {"value": "12345"}}}]
    manuscript = {"id": "Q10", "claims": {"P3959": [{
        "mainsnak": {"snaktype": "value", "datavalue": {"value": CN}},
    }]}}
    case = {"qid": "Q10", "control_number": CN, "reason": "named_scribe_identity_unresolved"}
    statement = identity._repair_statement(raw, case, manuscript, person, {}, viaf_sources={"12345": cluster})
    assert [[s["property"] for s in block] for block in statement["reference_blocks"]] == [
        ["P248", "P3959", "P854", "P813"], ["P248", "P854", "P813"],
    ]


@pytest.mark.asyncio
async def test_candidate_search_identifies_the_project_to_wikimedia(monkeypatch):
    import httpx

    from app.publication import automatic_evidence

    async def without_cache(db, *, fetch, **kwargs):
        return await fetch()

    def wikimedia(request):
        assert request.url.host == "www.wikidata.org"
        assert request.url.params["action"] == "wbsearchentities"
        if "https://mhm-pipeline.org" not in request.headers.get("User-Agent", ""):
            return httpx.Response(403)
        return httpx.Response(200, json={"search": [{"id": QID}]})

    client = httpx.AsyncClient
    monkeypatch.setattr(automatic_evidence, "cache_lookup_or_call", without_cache)
    monkeypatch.setattr(
        automatic_evidence.httpx, "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(wikimedia), **kwargs),
    )

    assert await automatic_evidence.search_candidates(None, "www.wikidata.org", "Mikulov", uuid.uuid4()) == [QID]


def test_marc_place_and_one_jurisdiction_can_prove_exact_containment():
    raw = {"001": CN, "751$a": '"Náchod (Czech Republic)|Paris (France)"',
           "751$e": '"place of writing|related place"'}
    parts = identity._place_parts(raw)
    assert parts == ("Náchod", "", "Czech Republic")
    assert identity._place_parts({**raw, "751$e": "place of writing"}) is None
    assert identity._place_parts({**raw, "751$e": "place of writing|place of writing"}) is None

    def claim(value):
        return {"mainsnak": {"snaktype": "value", "datavalue": {"value": value}}}

    entity = {
        "labels": {"en": {"value": "Nachod"}},
        "claims": {"P625": [claim({"latitude": 50.4})], "P17": [claim({"id": "Q213"})]},
    }
    assert identity._places_agree(entity, {"Q213": {"labels": {"en": {"value": "Czech Republic"}}}}, parts)
    assert not identity._places_agree(entity, {"Q213": {"labels": {"en": {"value": "Another country"}}}}, parts)


@pytest.mark.asyncio
async def test_kimi_retries_truncated_review_and_accepts_one_json_fence(monkeypatch):
    import httpx

    monkeypatch.setenv("QUBRID_API_KEY", "test-key")
    results = iter([
        {"finish_reason": "length", "message": {"content": "", "reasoning_content": "work"}},
        {"finish_reason": "stop", "message": {"content": '```json\n{"candidate_qid":null,"explanation":"No match."}\n```'}},
    ])
    client = httpx.AsyncClient
    requests = []
    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if not any("json" in message["content"].casefold() for message in payload["messages"]):
            return httpx.Response(400, json={"error": {"message": "JSON mode needs an explicit JSON request."}})
        return httpx.Response(200, json={"choices": [next(results)]})

    monkeypatch.setattr(worker.httpx, "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    result = await worker._ask_model({"wikidata_candidates": [{"qid": QID}]})
    assert result.candidate_qid is None
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_batch_keeps_exact_source_proof_when_kimi_is_unavailable(sample_run, monkeypatch):
    monkeypatch.setattr(worker, "search_candidates", AsyncMock(return_value=[QID]))
    monkeypatch.setattr(worker.ReferenceItemReader, "fetch_batch", AsyncMock(return_value=_human()))
    model = AsyncMock(side_effect=worker.ReviewUnavailable("Kimi is unavailable."))
    monkeypatch.setattr(worker, "_ask_model", model)
    result = await worker.inspect_case(
        _scribe_record(), {"qid": "Q10", "reason": "named_scribe_identity_unresolved"},
        sample_run["user_id"], automatic=True,
    )
    assert result["decision"] == "verified_match"
    assert result["candidate_qid"] == QID
    assert result["model"] is None
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_leaves_a_scribe_with_only_a_name_open(monkeypatch):
    search = AsyncMock()
    monkeypatch.setattr(worker, "search_candidates", search)
    raw = {"001": CN, "_control_number": CN, "700$a": "Named scribe", "700$e": "scribe"}
    result = await worker.inspect_case(
        raw, {"qid": "Q10", "reason": "named_scribe_identity_unresolved"}, uuid.uuid4(),
        automatic=True,
    )
    assert result["decision"] == "needs_evidence"
    search.assert_not_awaited()


def test_scribe_gnd_proof_requires_both_dates_and_the_exact_source_heading():
    raw = {
        "001": CN, "700$a": "Scribe, David son of Samuel,",
        "700$e": "scribe", "700$d": "1600-1660",
    }
    candidate = _human()[QID]
    candidate["labels"] = {"en": {"value": "Scribe, David son of Samuel, 1600-1660"}}
    candidate["claims"]["P227"] = [{"mainsnak": {"snaktype": "value", "datavalue": {"value": "1194270336"}}}]
    record = {
        "gndIdentifier": "1194270336", "preferredName": "Scribe, David son of Samuel",
        "dateOfBirth": ["1600"], "dateOfDeath": ["1660"],
    }
    sources = {"1194270336": record}
    assert identity.verified_candidate(raw, candidate, {}, scribe=True, gnd_sources=sources)
    manuscript = {"id": "Q100", "claims": {
        "P3959": [{"mainsnak": {"snaktype": "value", "datavalue": {"value": CN}}}],
        "P11603": [{
            "mainsnak": {"snaktype": "somevalue"},
            "qualifiers": {"P1932": [{"datavalue": {"value": "Scribe, David son of Samuel"}}]},
        }],
    }}
    statement = identity._repair_statement(
        raw, {"qid": "Q100", "control_number": CN, "reason": "named_scribe_identity_unresolved"},
        manuscript, candidate, {}, gnd_sources=sources,
    )
    assert statement["value"] == QID
    assert "https://d-nb.info/gnd/1194270336" in {
        snak["value"] for block in statement["reference_blocks"] for snak in block
    }
    assert not identity.verified_candidate(
        {**raw, "700$d": "1600-1661"}, candidate, {}, scribe=True, gnd_sources=sources,
    )
    assert not identity.verified_candidate(
        {**raw, "700$a": "Another David Scribe"}, candidate, {}, scribe=True, gnd_sources=sources,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_batch_evidence_recovers_from_wikidata_read_delay(monkeypatch, status):
    from contextlib import asynccontextmanager

    import httpx

    case = {
        "qid": "Q10", "local_id": "manuscript:one", "control_number": CN,
        "case_id": "source:scribe:one", "reason": "named_scribe_identity_unresolved",
        "disposition": "manual_review",
    }
    calls = 0

    def wikimedia(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(status, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"query": {}})

    async def batch_case(*_):
        async with httpx.AsyncClient(transport=httpx.MockTransport(wikimedia)) as client:
            await worker.ReferenceItemReader._get(
                client, "https://www.wikidata.org/w/api.php", {"action": "query"},
            )
        return "supported", {"case_id": case["case_id"]}

    @asynccontextmanager
    async def session_scope():
        yield SimpleNamespace(get=AsyncMock())

    finish = AsyncMock()
    monkeypatch.setattr(worker, "session_scope", session_scope)
    monkeypatch.setattr(worker, "gila_cases", lambda _: [case])
    monkeypatch.setattr(worker, "_authorize", AsyncMock())
    monkeypatch.setattr(worker, "_require_current_audit", AsyncMock())
    monkeypatch.setattr(worker, "update_job_progress", AsyncMock())
    monkeypatch.setattr(worker, "_batch_case", batch_case)
    monkeypatch.setattr(worker, "finish_job", finish)

    await worker._batch_review(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), SimpleNamespace(params={}))
    result = finish.await_args.kwargs["result"]
    assert (result["supported"], result["unavailable"], calls) == (1, 0, 2)
