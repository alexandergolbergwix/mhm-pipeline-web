"""SHACL conformance smoke test on a minimal built graph."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.pipeline.rdf_build import (
    RdfBuildOptions,
    SHAPES_PATH,
    _run_mapper_sync,
    _run_shacl_sync,
)


@pytest.mark.skipif(not SHAPES_PATH.exists(), reason="SHACL shapes file missing")
def test_minimal_record_runs_shacl_without_ontology_prefix_errors() -> None:
    rec = {
        "_control_number": "990000827290205171",
        "title": "פירוש המשנה",
        "authors": [{"name": "משה בן מיימון", "role": "author", "field": "100"}],
        "genres": ["manuscript"],
        "contents": [{"title": "פירוש המשנה"}],
    }
    opts = RdfBuildOptions(add_philological_overlay=False)
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "manuscripts.ttl"
        _, manuscripts, errors, _, _ = _run_mapper_sync([rec], [], out, build_options=opts)
        assert errors == []
        assert manuscripts == 1
        conforms, violations = _run_shacl_sync(out, SHAPES_PATH)
        messages = [v.message for v in violations]
        assert not any("Unknown namespace prefix" in m for m in messages), messages
        assert isinstance(conforms, bool)
