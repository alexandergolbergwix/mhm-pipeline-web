import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from app.pipeline.wikidata_gila_execution import WriteRejectedError, expected_entity
from app.pipeline.wikidata_gila_repairs import build_gila_repair_proposals
from app.publication.historical_repairs import advance_repair_publication
from app.publication.repository import InMemoryPublicationRepository
from converter.wikidata.retry_policy import retry_after_seconds


class Gateway:
    def __init__(self):
        ref = {"snaks": {"P3959": [{"snaktype": "value", "datavalue": {"value": "990001"}}]}}
        self.entity = {
            "id": "Q123",
            "type": "item",
            "lastrevid": 10,
            "labels": {},
            "claims": {
                "P217": [
                    {
                        "id": "Q123$shelf",
                        "type": "statement",
                        "rank": "normal",
                        "mainsnak": {
                            "snaktype": "value",
                            "property": "P217",
                            "datavalue": {"value": "Ms 1"},
                        },
                        "references": [ref, deepcopy(ref)],
                    }
                ]
            },
        }
        self.proposal = build_gila_repair_proposals({"Q123": self.entity})[0]
        self.writes = 0
        self.error = None
        self.apply_then_lose_response = False
        self.cancel_after_apply = False
        self.allowed = True

    async def fetch(self, qid):
        assert qid == "Q123"
        return deepcopy(self.entity)

    async def assert_allowed(self, qid):
        if not self.allowed:
            raise ValueError("Foreign item")

    async def write(self, qid, revision, claims):
        self.writes += 1
        if self.error:
            raise self.error
        assert revision == self.entity["lastrevid"]
        self.entity = expected_entity(self.proposal, self.entity)
        self.entity["lastrevid"] += 1
        if self.cancel_after_apply:
            raise asyncio.CancelledError()
        if self.apply_then_lose_response:
            raise TimeoutError("The response was lost")
        return self.entity["lastrevid"]


async def source_check(before, proposal):
    return {"control_number": "990001", "source_sha256": "a" * 64}


def plan(gateway):
    return {
        "run_id": "run-1",
        "actor_user_id": "actor-1",
        "authorized_qids": ["Q123"],
        "proposals": [gateway.proposal],
        "before_entities": {"Q123": deepcopy(gateway.entity)},
        "preflight": [
            {
                "qid": "Q123",
                "source_evidence": {"control_number": "990001", "source_sha256": "a" * 64},
            }
        ],
    }


@pytest.mark.asyncio
async def test_maxlag_persists_a_delay_and_resume_does_not_resend_early():
    gateway = Gateway()
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=60)
    reviewed = plan(gateway)
    repo = InMemoryPublicationRepository()
    now = [datetime(2026, 11, 13, tzinfo=UTC)]

    async def run():
        return await advance_repair_publication(
            reviewed,
            gateway,
            source_check,
            repo,
            actor_id="actor-1",
            run_id="run-1",
            clock=lambda: now[0],
        )

    first = await run()
    assert first.execution_status == "paused" and first.execution_pre_send_retryable_count == 1
    assert gateway.writes == 1
    intent = await repo.latest_write_intent(first.execution_id, "repair:Q123")
    assert intent.next_attempt_at >= now[0] + timedelta(seconds=60)
    await run()
    assert gateway.writes == 1
    now[0] += timedelta(seconds=62)
    gateway.error = None
    final = await run()
    assert final.execution_status == "succeeded" and gateway.writes == 2
    await run()
    assert gateway.writes == 2


@pytest.mark.asyncio
async def test_lost_response_recovers_from_the_exact_full_public_entity():
    gateway = Gateway()
    reviewed = plan(gateway)
    gateway.apply_then_lose_response = True
    repo = InMemoryPublicationRepository()

    async def run():
        return await advance_repair_publication(
            reviewed, gateway, source_check, repo, actor_id="actor-1", run_id="run-1"
        )

    first = await run()
    assert first.execution_status == "paused"
    final = await run()
    assert final.execution_status == "succeeded" and gateway.writes == 1


@pytest.mark.asyncio
async def test_unknown_result_cannot_trigger_a_blind_retry():
    gateway = Gateway()
    reviewed = plan(gateway)
    gateway.error = TimeoutError("No response")
    repo = InMemoryPublicationRepository()
    for _ in range(2):
        result = await advance_repair_publication(
            reviewed, gateway, source_check, repo, actor_id="actor-1", run_id="run-1"
        )
        assert result.execution_status == "paused"
    assert gateway.writes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["foreign", "revision"])
async def test_foreign_or_changed_revision_never_reaches_a_write(defect):
    gateway = Gateway()
    reviewed = plan(gateway)
    if defect == "foreign":
        gateway.allowed = False
    else:
        gateway.entity["lastrevid"] += 1
    result = await advance_repair_publication(
        reviewed,
        gateway,
        source_check,
        InMemoryPublicationRepository(),
        actor_id="actor-1",
        run_id="run-1",
    )
    assert result.plan_blocked_count == 1 and gateway.writes == 0


@pytest.mark.asyncio
async def test_worker_interruption_recovers_after_lease_expiry_without_a_second_write():
    gateway = Gateway()
    reviewed = plan(gateway)
    gateway.cancel_after_apply = True
    repo = InMemoryPublicationRepository()
    now = [datetime(2026, 11, 13, tzinfo=UTC)]

    async def run():
        return await advance_repair_publication(
            reviewed,
            gateway,
            source_check,
            repo,
            actor_id="actor-1",
            run_id="run-1",
            clock=lambda: now[0],
        )

    with pytest.raises(asyncio.CancelledError):
        await run()
    now[0] += timedelta(minutes=31)
    gateway.cancel_after_apply = False
    final = await run()
    assert final.execution_status == "succeeded" and gateway.writes == 1


@pytest.mark.asyncio
async def test_a_source_change_during_creator_proof_blocks_the_send():
    gateway = Gateway()
    reviewed = plan(gateway)
    changed = False
    checks = 0

    async def allowed(qid):
        nonlocal changed, checks
        checks += 1
        if checks == 2:
            changed = True

    async def current_source(before, proposal):
        proof = await source_check(before, proposal)
        return {**proof, "source_sha256": "b" * 64} if changed else proof

    gateway.assert_allowed = allowed
    final = await advance_repair_publication(
        reviewed,
        gateway,
        current_source,
        InMemoryPublicationRepository(),
        actor_id="actor-1",
        run_id="run-1",
    )
    assert final.execution_blocked_count == 1 and gateway.writes == 0


def test_http_date_and_invalid_retry_headers():
    now = datetime(2026, 11, 13, tzinfo=UTC)
    assert retry_after_seconds("Fri, 13 Nov 2026 00:01:00 GMT", now=now) == 60
    assert retry_after_seconds("nan") == 5
    assert retry_after_seconds("-1") == 0
