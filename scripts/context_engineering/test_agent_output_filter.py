"""Unit tests for agent_output_filter."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILTER = ROOT / "agent_output_filter.py"


class AgentOutputFilterTests(unittest.TestCase):
    def run_filter(self, text: str, *args: str) -> str:
        proc = subprocess.run(
            [sys.executable, str(FILTER), *args, "-"],
            input=text,
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout

    def test_strips_ansi(self) -> None:
        raw = "\x1b[31mERROR\x1b[0m something failed\n"
        out = self.run_filter(raw, "--preset", "generic")
        self.assertNotIn("\x1b[", out)
        self.assertIn("ERROR", out)

    def test_pytest_mode_keeps_failures(self) -> None:
        raw = """\
tests/test_foo.py::test_bar PASSED
tests/test_foo.py::test_baz FAILED
E   assert 1 == 2
======================== short test summary info ========================
FAILED tests/test_foo.py::test_baz - assert 1 == 2
1 failed, 1 passed in 0.01s
"""
        out = self.run_filter(raw, "--preset", "pytest")
        self.assertIn("FAILED", out)
        self.assertIn("assert 1 == 2", out)
        self.assertNotIn("PASSED", out)

    def test_heroku_keep_errors(self) -> None:
        raw = """\
2026-01-01 app[web] GET /api/health 200
2026-01-01 app[web] Error R14 - memory quota exceeded
"""
        out = self.run_filter(raw, "--preset", "heroku")
        self.assertIn("memory quota", out)
        self.assertNotIn("/api/health 200", out)

    def test_exit_code_prepended(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(FILTER), "--preset", "generic", "--exit-code", "1", "-"],
            input="line\n",
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertTrue(proc.stdout.startswith("[exit_code=1]"))


if __name__ == "__main__":
    unittest.main()
