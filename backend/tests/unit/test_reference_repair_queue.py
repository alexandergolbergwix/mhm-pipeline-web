from copy import deepcopy
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest

from app.pipeline.wikidata_gila_execution import WriteRejectedError, expected_entity
from app.pipeline.wikidata_gila_repairs import build_gila_repair_proposals
from app.publication import reference_repair_queue as queue
from app.publication.repair_retry_runner import wait_for_repair_publication
from app.publication.repository import InMemoryPublicationRepository


class Gateway:
    def __init__(self):
        self.entities = {}
        self.writes = []
        self.error = None
        self.foreign = set()
        for qid in ("Q123", "Q456", "Q789"):
            reference = {
                "snaks": {"P3959": [{"snaktype": "value", "datavalue": {"value": "990001"}}]}
            }
            self.entities[qid] = {
                "id": qid,
                "type": "item",
                "lastrevid": 10,
                "labels": {},
                "claims": {
                    "P217": [
                        {
                            "id": f"{qid}$shelf",
                            "type": "statement",
                            "rank": "normal",
                            "mainsnak": {
                                "snaktype": "value",
                                "property": "P217",
                                "datavalue": {"value": "Ms 1"},
                            },
                            "references": [reference, deepcopy(reference)],
                        }
                    ]
                },
            }

    async def fetch(self, qid):
        return deepcopy(self.entities[qid])

    async def assert_allowed(self, qid):
        if qid in self.foreign:
            raise ValueError("Foreign item")

    async def write(self, qid, revision, claims):
        assert revision == self.entities[qid]["lastrevid"]
        self.writes.append(qid)
        if self.error:
            raise self.error
        proposal = build_gila_repair_proposals({qid: self.entities[qid]})[0]
        self.entities[qid] = expected_entity(proposal, self.entities[qid])
        self.entities[qid]["lastrevid"] += 1
        return self.entities[qid]["lastrevid"]


async def source_check(entity, proposal):
    return {"source_control_numbers": ["990001"], "source_sha256": {"990001": "a" * 64}}


@pytest.mark.asyncio
async def test_backend_checkpoint_failure_stops_before_the_public_write(tmp_path):
    gateway = Gateway()

    async def fail_checkpoint(qid, results, directory):
        assert not gateway.writes
        raise OSError("The checkpoint store is unavailable")

    with pytest.raises(OSError):
        await run(tmp_path, gateway, InMemoryPublicationRepository(), apply=True,
                  on_checkpoint=fail_checkpoint)
    assert gateway.writes == []


@pytest.mark.asyncio
async def test_backend_resume_keeps_verified_receipts_and_skips_the_completed_prefix(tmp_path):
    gateway = Gateway()
    completed = {"qid": "Q123", "status": "verified", "corrected": True}
    result = await run(tmp_path, gateway, InMemoryPublicationRepository(), apply=True,
                       resume_results=[completed])
    assert result["status"] == "complete"
    assert result["results"][0] == completed
    assert gateway.writes == ["Q456", "Q789"]


@pytest.mark.asyncio
async def test_backend_resume_refuses_another_completed_scope(tmp_path):
    gateway = Gateway()
    with pytest.raises(ValueError, match="completed prefix"):
        await run(tmp_path, gateway, InMemoryPublicationRepository(), apply=True,
                  resume_results=[{"qid": "Q456", "status": "verified", "corrected": True}])
    assert gateway.writes == []


async def run(tmp_path, gateway, repository, **kwargs):
    return await queue.run_reference_repair_queue(
        ("Q123", "Q456", "Q789"),
        gateway,
        kwargs.pop("source_check", source_check),
        repository,
        state_dir=tmp_path,
        actor_id=kwargs.pop("actor_id", "actor"),
        run_id="run",
        project_id="project",
        wait_seconds=kwargs.pop("wait_seconds", 0),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_preview_then_apply_is_serial_and_rerun_does_not_repeat_a_write(tmp_path):
    gateway, repo = Gateway(), InMemoryPublicationRepository()
    preview = await run(tmp_path, gateway, repo)
    assert preview["status"] == "complete" and not gateway.writes
    result = await run(tmp_path, gateway, repo, apply=True)
    assert result["status"] == "complete"
    assert gateway.writes == ["Q123", "Q456", "Q789"]
    assert all(row["corrected"] for row in result["results"])
    await run(tmp_path, gateway, repo, apply=True)
    assert gateway.writes == ["Q123", "Q456", "Q789"]


@pytest.mark.asyncio
async def test_retry_keeps_execution_and_does_not_reach_the_next_item_early(tmp_path, monkeypatch):
    now = [datetime(2026, 11, 17, tzinfo=UTC)]
    monkeypatch.setattr(
        queue,
        "wait_for_repair_publication",
        partial(
            wait_for_repair_publication,
            clock=lambda: now[0],
        ),
    )
    gateway, repo = Gateway(), InMemoryPublicationRepository()
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=60)
    first = await run(tmp_path, gateway, repo, apply=True)
    assert first["status"] == "paused"
    assert gateway.writes == ["Q123"]
    execution = first["results"][0]["execution_id"]
    early = await run(tmp_path, gateway, repo, apply=True)
    assert early["results"][0]["execution_id"] == execution
    assert gateway.writes == ["Q123"]
    now[0] += timedelta(seconds=120)
    gateway.error = None
    final = await run(tmp_path, gateway, repo, apply=True)
    assert final["status"] == "complete"
    assert final["results"][0]["execution_id"] == execution
    assert gateway.writes == ["Q123", "Q123", "Q456", "Q789"]


@pytest.mark.asyncio
async def test_unknown_outcome_stops_without_a_blind_resend(tmp_path):
    gateway, repo = Gateway(), InMemoryPublicationRepository()
    gateway.error = TimeoutError("The response was lost")
    result = await run(tmp_path, gateway, repo, apply=True)
    assert result["results"][0]["status"] == "outcome_unknown"
    assert gateway.writes == ["Q123"]
    await run(tmp_path, gateway, repo, apply=True)
    assert gateway.writes == ["Q123"]


@pytest.mark.asyncio
async def test_foreign_target_stops_before_a_write_or_later_item(tmp_path):
    gateway, repo = Gateway(), InMemoryPublicationRepository()
    gateway.foreign.add("Q123")
    result = await run(tmp_path, gateway, repo, apply=True)
    assert result["status"] == "stopped" and len(result["results"]) == 1
    assert not gateway.writes


@pytest.mark.asyncio
async def test_source_change_cannot_replace_the_saved_plan(tmp_path):
    gateway, repo = Gateway(), InMemoryPublicationRepository()
    await run(tmp_path, gateway, repo)
    plan = (tmp_path / "plan-Q123.json").read_bytes()

    async def changed_source(entity, proposal):
        return {"source_control_numbers": ["990001"], "source_sha256": {"990001": "b" * 64}}

    result = await run(tmp_path, gateway, repo, source_check=changed_source, apply=True)
    assert result["status"] == "stopped" and not gateway.writes
    assert (tmp_path / "plan-Q123.json").read_bytes() == plan


@pytest.mark.asyncio
async def test_changed_public_entity_blocks_a_saved_plan(tmp_path):
    gateway, repo = Gateway(), InMemoryPublicationRepository()
    await run(tmp_path, gateway, repo)
    gateway.entities["Q123"]["labels"] = {"en": {"language": "en", "value": "Changed"}}
    gateway.entities["Q123"]["lastrevid"] += 1
    result = await run(tmp_path, gateway, repo, apply=True)
    assert result["status"] == "stopped" and not gateway.writes


@pytest.mark.asyncio
async def test_changed_actor_or_input_rejects_the_checkpoint(tmp_path):
    gateway, repo = Gateway(), InMemoryPublicationRepository()
    await run(tmp_path, gateway, repo)
    with pytest.raises(ValueError, match="scope or authenticated actor"):
        await run(tmp_path, gateway, repo, actor_id="another", apply=True)
    assert not gateway.writes


@pytest.mark.asyncio
async def test_foreground_plan_cannot_enter_automatic_queue(tmp_path):
    import json

    gateway, repo = Gateway(), InMemoryPublicationRepository()
    await run(tmp_path, gateway, repo)
    path = tmp_path / "plan-Q123.json"
    plan = json.loads(path.read_text())
    plan["execution_mode"] = "foreground_reference"
    path.write_text(json.dumps(plan))
    result = await run(tmp_path, gateway, repo, apply=True)
    assert result["status"] == "stopped" and not gateway.writes


@pytest.mark.asyncio
async def test_concurrent_runner_cannot_acquire_the_same_state(tmp_path):
    gateway, repo = Gateway(), InMemoryPublicationRepository()
    with queue.queue_lock(tmp_path):
        with pytest.raises(ValueError, match="Another process"):
            await run(tmp_path, gateway, repo, apply=True)
    assert not gateway.writes
    assert (await run(tmp_path, gateway, repo))["status"] == "complete"


@pytest.mark.asyncio
async def test_prior_uncertain_write_stops_before_another_read_or_write(tmp_path):
    gateway, repo = Gateway(), InMemoryPublicationRepository()

    async def prior_unknown(qid):
        raise ValueError("A prior uncertain write requires recovery.")

    result = await run(tmp_path, gateway, repo, apply=True, before_item=prior_unknown)
    assert result["status"] == "stopped" and len(result["results"]) == 1
    assert not gateway.writes and not (tmp_path / "plan-Q123.json").exists()


@pytest.mark.asyncio
async def test_audit_failure_after_success_cannot_cause_a_duplicate_write(tmp_path):
    gateway, repo = Gateway(), InMemoryPublicationRepository()

    async def audit_unavailable(receipt):
        raise ConnectionError("The audit service is unavailable.")

    result = await run(tmp_path, gateway, repo, apply=True, on_receipt=audit_unavailable)
    assert result["status"] == "stopped" and gateway.writes == ["Q123"]
    recovered = await run(tmp_path, gateway, repo, apply=True)
    assert recovered["status"] == "complete"
    assert gateway.writes == ["Q123", "Q456", "Q789"]


@pytest.mark.parametrize("value", [[], ["Q0"], ["Q123", "Q123"], ["../Q123"], [None]])
def test_candidate_input_rejects_ambiguous_or_invalid_targets(value):
    with pytest.raises(ValueError):
        queue.candidate_qids(value)


def test_candidates_accept_existing_inventory_rows():
    assert queue.candidate_qids([{"qid": "Q123"}, {"qid": "Q456"}]) == ("Q123", "Q456")


@pytest.mark.parametrize("seconds", ["-1", "nan", "inf", "-inf"])
def test_cli_rejects_invalid_wait_before_credentials(seconds):
    from scripts.run_gila_reference_queue import arguments

    with pytest.raises(SystemExit) as error:
        arguments(
            [
                "--run-id",
                "47e03437-09f2-4d6b-aea7-89fe79d76198",
                "--build-job-id",
                "140b0e5d-7a54-49d9-a3fb-808ce376f3cd",
                "--qids-from",
                "items.json",
                "--state-dir",
                "state",
                "--apply",
                "--wait-seconds",
                seconds,
            ]
        )
    assert error.value.code == 2


def test_cli_invalid_candidate_does_not_load_credentials(tmp_path, monkeypatch):
    from scripts import run_gila_reference_queue as cli

    path = tmp_path / "bad.json"
    path.write_text('["../Q123"]')

    def unexpected_environment(app):
        pytest.fail("Invalid input cannot load a server credential.")

    monkeypatch.setattr(cli, "load_environment", unexpected_environment)
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "--run-id",
                "47e03437-09f2-4d6b-aea7-89fe79d76198",
                "--build-job-id",
                "140b0e5d-7a54-49d9-a3fb-808ce376f3cd",
                "--qids-from",
                str(path),
                "--state-dir",
                str(tmp_path / "state"),
                "--heroku-app",
                "mhm-pipeline-web",
            ]
        )
    assert error.value.code == 2


@pytest.mark.asyncio
async def test_default_queue_wait_survives_more_than_one_hour(tmp_path, monkeypatch):
    import json

    from tests.unit.test_publication_historical_repairs import RetryClock

    timer = RetryClock()
    monkeypatch.setattr(queue, "wait_for_repair_publication", partial(
        wait_for_repair_publication, clock=timer.now,
        monotonic=lambda: timer.elapsed, sleep=timer.sleep,
    ))
    gateway, repo = Gateway(), InMemoryPublicationRepository()
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=3700)
    events = []

    def on_event(event):
        events.append(event)
        if event["status"] == "service_delay":
            saved = json.loads((tmp_path / "progress.json").read_text())
            assert saved["status"] == "service_delay"
            assert saved["current"]["qid"] == "Q123"
            assert saved["current"]["next_attempt_at"] == event["next_attempt_at"]
            assert not saved["results"]
            assert gateway.writes == ["Q123"]
            gateway.error = None

    result = await queue.run_reference_repair_queue(
        ["Q123", "Q456"], gateway, source_check, repo, state_dir=tmp_path,
        actor_id="actor", run_id="run", project_id="project", apply=True,
        on_event=on_event,
    )
    assert result["status"] == "complete"
    assert timer.elapsed >= 3700
    assert any(event["status"] == "service_delay" for event in events)
    assert gateway.writes == ["Q123", "Q123", "Q456"]
    assert len({row["execution_id"] for row in result["results"]}) == 2


@pytest.mark.asyncio
async def test_budget_pause_has_a_saved_date_and_retains_the_execution(tmp_path, monkeypatch):
    import json

    from tests.unit.test_publication_historical_repairs import RetryClock

    timer = RetryClock()
    monkeypatch.setattr(queue, "wait_for_repair_publication", partial(
        wait_for_repair_publication, clock=timer.now,
        monotonic=lambda: timer.elapsed, sleep=timer.sleep,
    ))
    gateway, repo = Gateway(), InMemoryPublicationRepository()
    gateway.error = WriteRejectedError("maxlag", code="maxlag", retry_after_seconds=3700)
    first = await run(tmp_path, gateway, repo, apply=True, wait_seconds=3600)
    assert first["status"] == "paused"
    saved = json.loads((tmp_path / "progress.json").read_text())
    assert saved["current"]["status"] == "service_delay"
    assert saved["current"]["next_attempt_at"]
    assert first["results"][0]["next_attempt_at"]
    execution = first["results"][0]["execution_id"]
    second = await run(tmp_path, gateway, repo, apply=True, wait_seconds=0)
    assert second["results"][0]["execution_id"] == execution
    assert gateway.writes == ["Q123"]
    timer.elapsed += 3701
    gateway.error = None
    final = await run(tmp_path, gateway, repo, apply=True)
    assert final["status"] == "complete"
    assert final["results"][0]["execution_id"] == execution
    assert gateway.writes == ["Q123", "Q123", "Q456", "Q789"]


def test_cli_defaults_to_a_finite_day_and_preview_does_not_wait():
    from scripts.run_gila_reference_queue import arguments

    flags = ["--run-id", "47e03437-09f2-4d6b-aea7-89fe79d76198",
             "--build-job-id", "140b0e5d-7a54-49d9-a3fb-808ce376f3cd",
             "--qids-from", "items.json", "--state-dir", "state"]
    assert arguments(flags).wait_seconds == 0
    assert arguments([*flags, "--apply"]).wait_seconds == 86400
