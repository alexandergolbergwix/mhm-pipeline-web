import logging
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.routers import runs
from app.settings import Settings


def test_retired_authority_mutation_fails_closed_and_emits_telemetry(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    run_id = UUID("48ba6c13-115c-4763-bff1-c08b9031b518")
    actor_id = UUID("00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(runs, "get_settings", lambda: Settings(legacy_authority_mutations_enabled=False))

    with caplog.at_level(logging.WARNING, logger=runs.logger.name):
        with pytest.raises(HTTPException) as error:
            runs._ensure_legacy_authority_mutations_enabled(run_id=run_id, actor_id=actor_id)

    assert error.value.status_code == 410
    record = next(record for record in caplog.records if record.message == "legacy_authority_mutation_retired")
    assert record.event_name == "legacy_authority_mutation_retired"
    assert record.route_family == "/runs/{run_id}"
    assert record.run_id == str(run_id)
    assert record.actor_id == str(actor_id)
    assert record.status_code == 410


def test_legacy_authority_mutations_can_be_reopened_for_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runs, "get_settings", lambda: Settings(legacy_authority_mutations_enabled=True))
    runs._ensure_legacy_authority_mutations_enabled()