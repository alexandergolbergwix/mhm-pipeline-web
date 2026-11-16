"""A confirmed foreground correction cannot become a bulk or repeat write."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from app.pipeline.wikidata_foreground_repair import ForegroundReferenceGateway
from app.pipeline.wikidata_gila_execution import API_URL, LiveRepairGateway, repair_claims
from app.pipeline.wikidata_gila_repairs import build_gila_repair_proposals, entity_digest


def plan_fixture():
    reference = {"snaks": {"P3959": [{"snaktype": "value", "datavalue": {"value": "990001"}}]}}
    entity = {"id": "Q123", "lastrevid": 10, "claims": {"P1476": [{
        "id": "Q123$title", "rank": "normal",
        "mainsnak": {"snaktype": "value", "property": "P1476", "datavalue": {
            "value": {"text": "Example", "language": "en"},
        }}, "references": [reference, deepcopy(reference)],
    }]}}
    proposal = build_gila_repair_proposals({"Q123": entity})[0]
    return {"execution_mode": "foreground_reference", "authorized_qids": ["Q123"],
            "proposals": [proposal], "before_entities": {"Q123": entity}}


def multiple_reference_plan():
    plan = plan_fixture()
    entity = plan["before_entities"]["Q123"]
    author = deepcopy(entity["claims"]["P1476"][0])
    author["id"] = "Q123$author"
    author["mainsnak"] = {"snaktype": "value", "property": "P50",
                          "datavalue": {"value": {"id": "Q456"}}}
    author["qualifiers"] = {"P1932": [{"snaktype": "value", "datavalue": {
        "value": "Source author name",
    }}]}
    distinct = deepcopy(author["references"][0])
    distinct["snaks"]["P958"] = [{"snaktype": "value", "datavalue": {"value": "f. 1"}}]
    author["references"].append(distinct)
    entity["claims"]["P50"] = [author]
    plan["proposals"] = build_gila_repair_proposals({"Q123": entity})
    return plan


def uploader_fixture():
    response = requests.Response()
    response.status_code = 200
    response._content = b'{"success":1,"entity":{"id":"Q123","lastrevid":11}}'
    session = requests.Session()
    session.post = Mock(return_value=response)
    login = SimpleNamespace(mediawiki_api_url=API_URL, get_session=lambda: session,
                            get_edit_token=lambda: "fixture-edit-token")
    uploader = SimpleNamespace(_login=login, _rate_limit=Mock(), _assert_modifiable=Mock())
    return uploader, session


def foreground(plan, uploader, **kwargs):
    return ForegroundReferenceGateway(
        uploader, plan=plan, confirmed_qid="Q123",
        confirmed_proposal_sha256=entity_digest(plan["proposals"][0]), **kwargs,
    )


@pytest.mark.asyncio
async def test_foreground_omits_maxlag_for_one_exact_post_but_bulk_keeps_five():
    plan = plan_fixture()
    uploader, session = uploader_fixture()
    gateway = foreground(plan, uploader)
    claims = repair_claims(plan["proposals"][0])
    assert await gateway.write("Q123", 10, claims) == 11
    kwargs = session.post.call_args.kwargs
    assert "maxlag" not in kwargs["data"]
    assert kwargs["data"]["baserevid"] == 10
    assert json.loads(kwargs["data"]["data"]) == {"claims": claims}
    assert kwargs["allow_redirects"] is False
    uploader._assert_modifiable.assert_called_once_with("Q123", stage="exact_repair_pre_write")
    with pytest.raises(ValueError, match="attempt"):
        await gateway.write("Q123", 10, claims)
    session.post.assert_called_once()
    await LiveRepairGateway(uploader).write("Q123", 10, claims)
    assert session.post.call_args.kwargs["data"]["maxlag"] == 5


@pytest.mark.asyncio
async def test_one_item_keeps_all_confirmed_reference_corrections_in_one_post():
    plan = multiple_reference_plan()
    uploader, session = uploader_fixture()
    gateway = foreground(plan, uploader)
    claims = repair_claims(plan["proposals"][0])
    assert len(claims) == 2
    assert await gateway.write("Q123", 10, claims) == 11
    session.post.assert_called_once()
    payload = session.post.call_args.kwargs["data"]
    assert json.loads(payload["data"]) == {"claims": claims}
    assert "maxlag" not in payload and "colophon" not in payload["summary"]
    before_author = plan["before_entities"]["Q123"]["claims"]["P50"][0]
    after_author = next(claim for claim in claims if claim["id"] == "Q123$author")
    assert after_author["mainsnak"] == before_author["mainsnak"]
    assert after_author["qualifiers"] == before_author["qualifiers"]
    assert len(after_author["references"]) == 2
    assert after_author["references"][-1] == before_author["references"][-1]
    with pytest.raises(ValueError, match="attempt"):
        await gateway.write("Q123", 10, claims)
    session.post.assert_called_once()


@pytest.mark.parametrize("field", ["rank", "mainsnak", "qualifiers", "kind"])
def test_a_valid_first_change_cannot_hide_an_unsafe_later_change(field):
    plan = multiple_reference_plan()
    change = plan["proposals"][0]["changes"][1]
    if field == "kind":
        change["kind"] = "remove_duplicate_colophon"
    elif field == "rank":
        change["after"][field] = "preferred"
    else:
        change["after"][field] = {}
    uploader, session = uploader_fixture()
    with pytest.raises(ValueError):
        foreground(plan, uploader)
    session.post.assert_not_called()


@pytest.mark.asyncio
async def test_a_partial_change_list_cannot_replace_the_confirmed_item_payload():
    plan = multiple_reference_plan()
    uploader, session = uploader_fixture()
    gateway = foreground(plan, uploader)
    with pytest.raises(ValueError):
        await gateway.write("Q123", 10, repair_claims(plan["proposals"][0])[:1])
    session.post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["qid", "revision", "claims", "expired", "creator"])
async def test_changed_scope_or_creator_blocks_before_the_post(failure):
    plan = plan_fixture()
    uploader, session = uploader_fixture()
    now = [0.0]
    gateway = foreground(plan, uploader, monotonic=lambda: now[0])
    qid, revision = "Q123", 10
    claims = repair_claims(plan["proposals"][0])
    if failure == "qid":
        qid = "Q999"
    elif failure == "revision":
        revision = 12
    elif failure == "claims":
        claims[0]["rank"] = "preferred"
    elif failure == "expired":
        now[0] = 301
    else:
        uploader._assert_modifiable.side_effect = ValueError("Foreign creator")
    with pytest.raises(ValueError):
        await gateway.write(qid, revision, claims)
    session.post.assert_not_called()


@pytest.mark.asyncio
async def test_uncertain_response_consumes_the_only_attempt():
    plan = plan_fixture()
    uploader, session = uploader_fixture()
    session.post.side_effect = requests.Timeout("fixture timeout")
    gateway = foreground(plan, uploader)
    claims = repair_claims(plan["proposals"][0])
    with pytest.raises(requests.Timeout):
        await gateway.write("Q123", 10, claims)
    with pytest.raises(ValueError, match="attempt"):
        await gateway.write("Q123", 10, claims)
    session.post.assert_called_once()


@pytest.mark.parametrize("failure", ["bulk", "confirmation", "rank", "colophon"])
def test_foreground_construction_rejects_unconfirmed_or_nonreference_scope(failure):
    plan = plan_fixture()
    uploader, session = uploader_fixture()
    confirmed = "Q123"
    if failure == "bulk":
        plan["authorized_qids"].append("Q456")
    elif failure == "confirmation":
        confirmed = "Q456"
    elif failure == "rank":
        plan["proposals"][0]["changes"][0]["after"]["rank"] = "preferred"
    else:
        plan["proposals"][0]["changes"][0]["kind"] = "remove_duplicate_colophon"
    with pytest.raises(ValueError):
        ForegroundReferenceGateway(uploader, plan=plan, confirmed_qid=confirmed,
                                   confirmed_proposal_sha256=entity_digest(plan["proposals"][0]))
    session.post.assert_not_called()


@pytest.mark.asyncio
async def test_retry_runner_rejects_foreground_before_any_advance(monkeypatch):
    from app.publication import repair_retry_runner as runner

    advance = Mock(side_effect=AssertionError("The retry runner cannot accept a foreground plan"))
    monkeypatch.setattr(runner, "advance_repair_publication", advance)
    with pytest.raises(ValueError, match="foreground"):
        await runner.wait_for_repair_publication(
            plan_fixture(), None, None, None, actor_id="actor", run_id="run",
        )
    advance.assert_not_called()


@pytest.mark.asyncio
async def test_normal_gateway_cannot_execute_a_foreground_plan():
    from app.publication.historical_repairs import advance_repair_publication

    plan = {**plan_fixture(), "actor_user_id": "actor", "run_id": "run"}
    uploader, _ = uploader_fixture()
    with pytest.raises(ValueError, match="foreground"):
        await advance_repair_publication(
            plan, LiveRepairGateway(uploader), None, None, actor_id="actor", run_id="run",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.parametrize("failure", [None, "foreign", "lost_before_apply", "lost_after_apply"])
async def test_foreground_publication_retains_intent_creator_and_recovery_guards(failure, multiple):
    from app.pipeline.wikidata_gila_execution import expected_entity
    from app.publication.historical_repairs import advance_repair_publication
    from app.publication.repository import InMemoryPublicationRepository

    plan = {**(multiple_reference_plan() if multiple else plan_fixture()),
            "actor_user_id": "actor", "run_id": "run",
            "preflight": [{"qid": "Q123", "source_evidence": {"control_number": "990001"}}]}
    uploader, session = uploader_fixture()
    state = [deepcopy(plan["before_entities"]["Q123"])]
    uploader._prefetched_entities = {}
    uploader._wbgetentities = lambda *args, **kwargs: {"Q123": deepcopy(state[0])}
    uploader._is_our_item_cache = {}
    uploader._creator_cache = {}
    uploader._foreign_accept_qids = set()
    uploader._bot_exclusion_cache = {}
    uploader._check_moratorium_for_live = Mock()
    uploader.ensure_authenticated = Mock()
    uploader._creator_identity_matches = Mock(return_value=failure != "foreign")
    uploader._bot_excluded = Mock(return_value=False)
    gateway = foreground(plan, uploader)
    repository = InMemoryPublicationRepository()

    def send(*args, **kwargs):
        assert sum(len(rows) for rows in repository._write_intents.values()) == 1
        if failure == "lost_before_apply":
            raise requests.Timeout("fixture timeout")
        state[0] = expected_entity(plan["proposals"][0], state[0])
        state[0]["lastrevid"] = 11
        if failure == "lost_after_apply":
            raise requests.Timeout("fixture timeout")
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"success":1,"entity":{"id":"Q123","lastrevid":11}}'
        return response

    session.post.side_effect = send

    async def check_source(before, proposal):
        return {"control_number": "990001"}

    async def advance():
        return await advance_repair_publication(
            plan, gateway, check_source, repository, actor_id="actor", run_id="run",
        )

    first = await advance()
    publication = await repository.get_publication(first.publication_id)
    assert publication.profile.name == "gila-foreground-reference"
    if failure == "foreign":
        assert first.plan_blocked_count == 1
        session.post.assert_not_called()
        return
    if failure is None:
        assert first.execution_status == "succeeded"
    else:
        assert first.execution_outcome_unknown_count == 1
    second = await advance()
    if failure == "lost_before_apply":
        assert second.execution_outcome_unknown_count == 1
    else:
        assert second.execution_status == "succeeded"
    session.post.assert_called_once()
    uploader._creator_identity_matches.assert_any_call(
        "Q123", "Alexander Goldberg IL", required_user_id=6935067,
    )
