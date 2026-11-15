import argparse

import pytest

from scripts import execute_gila_repairs


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "-inf"])
def test_invalid_wait_limit_fails_before_authentication(monkeypatch, value):
    monkeypatch.setattr(
        "sys.argv",
        [
            "execute_gila_repairs.py",
            "--run-id",
            "unused",
            "--plan",
            "unused",
            "--receipts",
            "unused",
            "--apply",
            "--wait-seconds",
            value,
        ],
    )

    def unexpected_authentication(prompt):
        pytest.fail("An invalid wait limit cannot request a credential.")

    monkeypatch.setattr(execute_gila_repairs.getpass, "getpass", unexpected_authentication)
    with pytest.raises(SystemExit) as error:
        execute_gila_repairs.main()
    assert error.value.code == 2


def test_a_positive_wait_requires_apply_before_authentication(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "execute_gila_repairs.py",
            "--run-id",
            "unused",
            "--plan",
            "unused",
            "--receipts",
            "unused",
            "--wait-seconds",
            "60",
        ],
    )

    def unexpected_authentication(prompt):
        pytest.fail("A wait without --apply cannot request a credential.")

    monkeypatch.setattr(execute_gila_repairs.getpass, "getpass", unexpected_authentication)
    with pytest.raises(SystemExit) as error:
        execute_gila_repairs.main()
    assert error.value.code == 2


def test_zero_wait_retains_one_advance():
    assert execute_gila_repairs.wait_seconds("0") == 0
    with pytest.raises(argparse.ArgumentTypeError):
        execute_gila_repairs.wait_seconds("nan")
