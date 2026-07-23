from __future__ import annotations

import sys
import uuid

import pytest

from scripts import run_hmo_phase8


def test_live_mode_requires_environment_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_hmo_phase8",
            "--live",
            "--run-id",
            str(uuid.uuid4()),
            "--confirm-live-writes",
        ],
    )
    monkeypatch.delenv("HMO_PHASE8_LIVE_WRITES", raising=False)

    with pytest.raises(SystemExit):
        run_hmo_phase8.main()


def test_live_mode_requires_cli_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_hmo_phase8", "--live", "--run-id", str(uuid.uuid4())],
    )
    monkeypatch.setenv("HMO_PHASE8_LIVE_WRITES", "1")

    with pytest.raises(SystemExit):
        run_hmo_phase8.main()


def test_test_sweep_stops_after_first_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], cwd: object) -> run_hmo_phase8.CommandResult:
        calls.append(command)
        return run_hmo_phase8.CommandResult(command, 1, "failed")

    monkeypatch.setattr(run_hmo_phase8, "_run_command", fake_run)

    result = run_hmo_phase8.run_test_sweep()

    assert result["passed"] is False
    assert len(calls) == 1
    assert calls[0][-2:] == ["-k", "hmo"]
