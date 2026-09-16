"""Every job runner module must import cleanly (Rule R26 follow-up).

A SyntaxError in any ``*_job.py`` module ships silently — the unit suite
only imports the modules a test touches, and the failing module blows up
at dispatch time in production, surfacing raw interpreter errors
("invalid syntax (rdf_build_job.py, line 49)") to curators.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_PIPELINE_DIR = Path(__file__).resolve().parents[2] / "app" / "pipeline"


def _job_modules() -> list[str]:
    names = sorted(
        p.name for p in _PIPELINE_DIR.glob("*_job*.py")
        if p.name != "__init__.py" and not p.name.endswith(".pyc")
    )
    return [f"app.pipeline.{name[:-3]}" for name in names]


@pytest.mark.parametrize("module_name", _job_modules())
def test_job_module_imports_cleanly(module_name: str) -> None:
    importlib.import_module(module_name)


def test_job_module_discovery_is_not_empty() -> None:
    modules = _job_modules()
    assert len(modules) >= 10, f"discovery broken: {modules}"
