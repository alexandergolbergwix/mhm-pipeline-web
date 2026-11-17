import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.pipeline.wikidata_gila_execution import WriteRejectedError, expected_entity
from app.pipeline.wikidata_gila_repairs import build_gila_repair_proposals
from app.publication.historical_repairs import advance_repair_publication
from app.publication.repair_retry_runner import wait_for_repair_publication
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


class RetryClock:
    def __init__(self):
        self.start = datetime(2026, 11, 14, tzinfo=UTC)
        self.elapsed = 0.0
        self.sleeps = []

    def now(self):
        return self.start + timedelta(seconds=self.elapsed)

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += seconds


async def wait_for_repair(reviewed, gateway, repo, timer, **options):
    return await wait_for_repair_publication(
        reviewed,
        gateway,
        source_check,
        repo,
        actor_id="actor-1",
        run_id="run-1",
        max_wait_seconds=options.pop("limit", 600),
        clock=timer.now,
        monotonic=lambda: timer.elapsed,
        sleep=timer.sleep,
        **options,
    )


@pytest.mark.asyncio
async def test_automatic_retry_keeps_execution_and_honors_the_durable_date():
    gateway = Gateway()
    reviewed = plan(gateway)
    repo = InMemoryPublicationRepository()
    timer = RetryClock()
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=65)
    first = await advance_repair_publication(
        reviewed,
        gateway,
        source_check,
        repo,
        actor_id="actor-1",
        run_id="run-1",
        clock=timer.now,
    )
    original = await repo.latest_write_intent(first.execution_id, "repair:Q123")
    waits = []

    def on_wait(event):
        waits.append(event)
        assert gateway.writes == 1
        assert event.execution_id == first.execution_id
        assert event.next_attempt_at == original.next_attempt_at
        gateway.error = None

    final = await wait_for_repair(reviewed, gateway, repo, timer, on_wait=on_wait)
    assert final.execution_status == "succeeded"
    assert final.execution_id == first.execution_id and gateway.writes == 2
    assert timer.now() >= original.next_attempt_at
    assert waits and all(0 < seconds <= 30 for seconds in timer.sleeps)
    await wait_for_repair(reviewed, gateway, repo, timer)
    assert gateway.writes == 2


@pytest.mark.asyncio
async def test_retry_wait_releases_the_repository_transaction(monkeypatch):
    gateway = Gateway()
    reviewed = plan(gateway)
    repo = InMemoryPublicationRepository()
    timer = RetryClock()
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=60)
    needs_checkpoint = False
    real_latest = repo.latest_write_intent

    async def latest(execution_id, entity_key):
        nonlocal needs_checkpoint
        intent = await real_latest(execution_id, entity_key)
        needs_checkpoint = True
        return intent

    async def checkpoint():
        nonlocal needs_checkpoint
        needs_checkpoint = False

    monkeypatch.setattr(repo, "checkpoint", checkpoint)
    monkeypatch.setattr(repo, "latest_write_intent", latest)

    def on_wait(event):
        assert not needs_checkpoint
        gateway.error = None

    result = await wait_for_repair(reviewed, gateway, repo, timer, on_wait=on_wait)
    assert result.execution_status == "succeeded"


@pytest.mark.asyncio
async def test_retry_limit_preserves_the_saved_pause_without_an_early_send():
    gateway = Gateway()
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=120)
    repo = InMemoryPublicationRepository()
    timer = RetryClock()
    result = await wait_for_repair(plan(gateway), gateway, repo, timer, limit=30)
    assert result.execution_status == "paused" and gateway.writes == 1
    assert not timer.sleeps
    intent = await repo.latest_write_intent(result.execution_id, "repair:Q123")
    assert intent.state == "pre_send_retryable"
    assert intent.next_attempt_at >= timer.now() + timedelta(seconds=120)


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["foreign", "revision", "source"])
async def test_automatic_resume_rechecks_creator_revision_and_source(defect):
    gateway = Gateway()
    reviewed = plan(gateway)
    repo = InMemoryPublicationRepository()
    timer = RetryClock()
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=60)
    changed = False

    async def current_source(before, proposal):
        evidence = await source_check(before, proposal)
        return {**evidence, "source_sha256": "changed"} if changed else evidence

    def on_wait(event):
        nonlocal changed
        gateway.error = None
        if defect == "foreign":
            gateway.allowed = False
        elif defect == "revision":
            gateway.entity["lastrevid"] = 11
        else:
            changed = True

    result = await wait_for_repair_publication(
        reviewed,
        gateway,
        current_source,
        repo,
        actor_id="actor-1",
        run_id="run-1",
        max_wait_seconds=600,
        clock=timer.now,
        monotonic=lambda: timer.elapsed,
        sleep=timer.sleep,
        on_wait=on_wait,
    )
    assert result.execution_blocked_count == 1
    assert gateway.writes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError("No response"), WriteRejectedError("blocked")])
async def test_automatic_resume_stops_without_a_confirmed_retry_date(error):
    gateway = Gateway()
    gateway.error = error
    timer = RetryClock()
    result = await wait_for_repair(
        plan(gateway),
        gateway,
        InMemoryPublicationRepository(),
        timer,
    )
    assert result.execution_status in {"paused", "failed"}
    assert gateway.writes == 1 and not timer.sleeps


@pytest.mark.asyncio
async def test_retry_cancellation_retains_the_existing_execution():
    gateway = Gateway()
    reviewed = plan(gateway)
    repo = InMemoryPublicationRepository()
    timer = RetryClock()
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=60)

    async def cancelled_sleep(seconds):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await wait_for_repair_publication(
            reviewed,
            gateway,
            source_check,
            repo,
            actor_id="actor-1",
            run_id="run-1",
            max_wait_seconds=600,
            clock=timer.now,
            monotonic=lambda: timer.elapsed,
            sleep=cancelled_sleep,
        )
    assert gateway.writes == 1
    gateway.error = None
    final = await wait_for_repair(reviewed, gateway, repo, timer)
    assert final.execution_status == "succeeded" and gateway.writes == 2


@pytest.mark.asyncio
async def test_a_missing_retry_date_does_not_create_a_new_retry(monkeypatch):
    gateway = Gateway()
    reviewed = plan(gateway)
    repo = InMemoryPublicationRepository()
    timer = RetryClock()
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=60)
    real_latest = repo.latest_write_intent

    async def without_date(execution_id, entity_key):
        intent = await real_latest(execution_id, entity_key)
        return replace(intent, next_attempt_at=None) if intent else None

    monkeypatch.setattr(repo, "latest_write_intent", without_date)
    result = await wait_for_repair(reviewed, gateway, repo, timer)
    assert result.execution_status == "paused"
    assert gateway.writes == 1 and not timer.sleeps


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [-1, float("nan"), float("inf")])
async def test_invalid_retry_budgets_cannot_reach_a_write(limit):
    gateway = Gateway()
    with pytest.raises(ValueError, match="wait limit"):
        await wait_for_repair(
            plan(gateway),
            gateway,
            InMemoryPublicationRepository(),
            RetryClock(),
            limit=limit,
        )
    assert gateway.writes == 0


@pytest.mark.asyncio
async def test_external_plan_changes_do_not_create_a_second_publication():
    gateway = Gateway()
    reviewed = plan(gateway)
    repo = InMemoryPublicationRepository()
    timer = RetryClock()
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=60)
    execution_ids = set()

    def on_wait(event):
        execution_ids.add(event.execution_id)
        reviewed["actor_user_id"] = "changed"
        reviewed["authorized_qids"] = ["Q999"]
        gateway.error = None

    result = await wait_for_repair(reviewed, gateway, repo, timer, on_wait=on_wait)
    assert result.execution_status == "succeeded" and gateway.writes == 2
    assert execution_ids == {result.execution_id}


@pytest.mark.asyncio
async def test_persistent_maxlag_stops_at_the_budget_without_a_busy_loop():
    gateway = Gateway()
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=5)
    timer = RetryClock()
    result = await wait_for_repair(
        plan(gateway),
        gateway,
        InMemoryPublicationRepository(),
        timer,
        limit=45,
    )
    assert result.execution_status == "paused"
    assert gateway.writes == 4
    assert 35 <= timer.elapsed <= 45
    assert all(seconds > 0 for seconds in timer.sleeps)


@pytest.mark.asyncio
async def test_automatic_resume_uses_the_sql_retry_date(db_session, sample_run):
    from app.publication.sql_repository import SqlAlchemyPublicationRepository

    gateway = Gateway()
    reviewed = plan(gateway)
    reviewed["run_id"] = str(sample_run["run_id"])
    reviewed["actor_user_id"] = str(sample_run["user_id"])
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=65)
    timer = RetryClock()
    waits = []

    def on_wait(event):
        waits.append(event)
        gateway.error = None

    result = await wait_for_repair_publication(
        reviewed,
        gateway,
        source_check,
        SqlAlchemyPublicationRepository(db_session),
        actor_id=reviewed["actor_user_id"],
        run_id=reviewed["run_id"],
        max_wait_seconds=600,
        clock=timer.now,
        monotonic=lambda: timer.elapsed,
        sleep=timer.sleep,
        on_wait=on_wait,
    )
    assert waits
    assert result.execution_status == "succeeded" and gateway.writes == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [{"attempt_count": 99}, {"state": "blocked"}])
async def test_sql_does_not_attach_a_date_from_a_different_attempt_or_state(
    db_session,
    sample_run,
    mismatch,
):
    import uuid

    from sqlalchemy import update

    from app.models.publication import PublicationExecutionAction
    from app.publication.sql_repository import SqlAlchemyPublicationRepository

    gateway = Gateway()
    reviewed = plan(gateway)
    reviewed["run_id"] = str(sample_run["run_id"])
    reviewed["actor_user_id"] = str(sample_run["user_id"])
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=65)
    repo = SqlAlchemyPublicationRepository(db_session)
    result = await wait_for_repair_publication(
        reviewed,
        gateway,
        source_check,
        repo,
        actor_id=reviewed["actor_user_id"],
        run_id=reviewed["run_id"],
    )
    intent = await repo.latest_write_intent(result.execution_id, "repair:Q123")
    assert intent.next_attempt_at is not None
    await db_session.execute(
        update(PublicationExecutionAction)
        .where(
            PublicationExecutionAction.execution_id == uuid.UUID(result.execution_id),
        )
        .values(**mismatch)
    )
    await db_session.commit()
    mismatched = await repo.latest_write_intent(result.execution_id, "repair:Q123")
    assert mismatched.state == "pre_send_retryable" and mismatched.next_attempt_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed_after_due", [0, 1, 60])
async def test_retry_resumes_when_commit_finishes_after_the_saved_date(
    monkeypatch, elapsed_after_due
):
    import app.publication.repair_retry_runner as runner

    gateway = Gateway()
    reviewed = plan(gateway)
    repo = InMemoryPublicationRepository()
    timer = RetryClock()
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=5)
    real_advance = runner.advance_repair_publication
    execution_ids = []

    async def slow_commit(*args, **kwargs):
        result = await real_advance(*args, **kwargs)
        execution_ids.append(result.execution_id)
        if len(execution_ids) == 1:
            intent = await repo.latest_write_intent(result.execution_id, "repair:Q123")
            assert intent.state == "pre_send_retryable"
            timer.elapsed = (
                intent.next_attempt_at - timer.start
            ).total_seconds() + elapsed_after_due
            gateway.error = None
        return result

    monkeypatch.setattr(runner, "advance_repair_publication", slow_commit)
    result = await wait_for_repair(reviewed, gateway, repo, timer)

    assert result.execution_status == "succeeded"
    assert gateway.writes == 2
    assert len(execution_ids) == 2 and len(set(execution_ids)) == 1
    assert not timer.sleeps


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["foreign", "revision", "source", "budget"])
async def test_elapsed_retry_date_preserves_current_proof_and_budget(monkeypatch, defect):
    import app.publication.repair_retry_runner as runner

    gateway = Gateway()
    reviewed = plan(gateway)
    repo = InMemoryPublicationRepository()
    timer = RetryClock()
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=5)
    real_advance = runner.advance_repair_publication
    changed = False
    commits = 0

    async def current_source(before, proposal):
        evidence = await source_check(before, proposal)
        return {**evidence, "source_sha256": "changed"} if changed else evidence

    async def slow_commit(*args, **kwargs):
        nonlocal changed, commits
        result = await real_advance(*args, **kwargs)
        commits += 1
        if commits == 1:
            intent = await repo.latest_write_intent(result.execution_id, "repair:Q123")
            timer.elapsed = (intent.next_attempt_at - timer.start).total_seconds() + 1
            gateway.error = None
            if defect == "foreign":
                gateway.allowed = False
            elif defect == "revision":
                gateway.entity["lastrevid"] += 1
            elif defect == "source":
                changed = True
            else:
                timer.elapsed = 601
        return result

    monkeypatch.setattr(runner, "advance_repair_publication", slow_commit)
    result = await wait_for_repair_publication(
        reviewed,
        gateway,
        current_source,
        repo,
        actor_id="actor-1",
        run_id="run-1",
        max_wait_seconds=600,
        clock=timer.now,
        monotonic=lambda: timer.elapsed,
        sleep=timer.sleep,
    )

    assert gateway.writes == 1
    assert not timer.sleeps
    if defect == "budget":
        assert result.execution_status == "paused"
    else:
        assert result.execution_blocked_count == 1


@pytest.mark.asyncio
async def test_sql_resume_retains_safe_lag_diagnostics(db_session, sample_run):
    from app.pipeline.wikidata_retry_diagnostics import ApiRetryDiagnostics
    from app.publication.sql_repository import SqlAlchemyPublicationRepository

    timer = RetryClock()
    diagnostics = ApiRetryDiagnostics.from_error({
        "code": "maxlag", "host": "wdqs1014", "type": "wikibase-queryservice",
        "lag": 15.0666666667, "queryserviceLag": 904,
        "info": "private-response", "token": "private-value",
    }, retry_after_seconds=3700, observed_at=timer.now())
    gateway = Gateway()
    reviewed = plan(gateway)
    reviewed["run_id"] = str(sample_run["run_id"])
    reviewed["actor_user_id"] = str(sample_run["user_id"])
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=3700,
                                       diagnostics=diagnostics)
    repo = SqlAlchemyPublicationRepository(db_session)
    first = await advance_repair_publication(
        reviewed, gateway, source_check, repo,
        actor_id=reviewed["actor_user_id"], run_id=reviewed["run_id"], clock=timer.now,
    )
    db_session.expunge_all()
    restored = SqlAlchemyPublicationRepository(db_session)
    intent = await restored.latest_write_intent(first.execution_id, "repair:Q123")
    assert ApiRetryDiagnostics.from_detail(intent.detail) == diagnostics
    assert "private-" not in intent.detail
    waits = []

    def on_wait(event):
        assert event.diagnostics == diagnostics
        assert gateway.writes == 1
        waits.append(event)
        gateway.error = None

    final = await wait_for_repair_publication(
        reviewed, gateway, source_check, restored,
        actor_id=reviewed["actor_user_id"], run_id=reviewed["run_id"],
        max_wait_seconds=4000, clock=timer.now, monotonic=lambda: timer.elapsed,
        sleep=timer.sleep, on_wait=on_wait,
    )
    assert final.execution_status == "succeeded"
    assert final.execution_id == first.execution_id
    assert timer.elapsed >= 3700 and gateway.writes == 2
    assert waits and all(0 < seconds <= 30 for seconds in timer.sleeps)
    successful = await restored.latest_write_intent(final.execution_id, "repair:Q123")
    assert ApiRetryDiagnostics.from_detail(successful.detail) is None
