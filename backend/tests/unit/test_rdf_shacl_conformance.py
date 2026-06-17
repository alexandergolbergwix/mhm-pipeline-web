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


@pytest.mark.skipif(not SHAPES_PATH.exists(), reason="SHACL shapes file missing")
def test_large_corpus_ttl_does_not_crash_shacl_engine() -> None:
    corpus = Path(
        "/Users/alexandergo/Downloads/"
        "run-48ba6c13-115c-4763-bff1-c08b9031b518-manuscripts (2).ttl"
    )
    if not corpus.exists():
        pytest.skip("local corpus TTL fixture not present")
    conforms, violations = _run_shacl_sync(corpus, SHAPES_PATH)
    assert not any("Unknown namespace prefix" in v.message for v in violations)
    assert isinstance(conforms, bool)
