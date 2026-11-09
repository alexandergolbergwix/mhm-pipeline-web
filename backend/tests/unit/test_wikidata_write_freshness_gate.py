import pytest

from app.pipeline import wikidata_upload_job as job


async def test_gate_checks_once_per_batch_not_per_write(monkeypatch):
    monkeypatch.setattr(job, "WRITE_FRESHNESS_BATCH_SIZE", 50)
    calls = []

    async def check() -> None:
        calls.append(1)

    gate = job._BatchFreshnessGate(check)
    for _ in range(120):
        await gate.before_write()
    assert len(calls) == 3


async def test_failed_check_blocks_the_write_and_is_retried(monkeypatch):
    monkeypatch.setattr(job, "WRITE_FRESHNESS_BATCH_SIZE", 50)
    attempts = []

    async def check() -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("source changed")

    gate = job._BatchFreshnessGate(check)
    with pytest.raises(RuntimeError, match="source changed"):
        await gate.before_write()
    await gate.before_write()
    assert len(attempts) == 2
