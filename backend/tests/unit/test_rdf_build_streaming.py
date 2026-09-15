"""Streaming RDF build + resume (job-service R26).

The old build accumulated one rdflib ``Graph`` for the whole corpus —
the memory spike that R14/R15-killed the 512 MB web dyno. The streaming
build maps and serializes one record subgraph at a time, appends Turtle
chunks to the artifact, and checkpoints for crash resume.
"""

from __future__ import annotations

from pathlib import Path

from rdflib import Graph, Literal

from app.pipeline.rdf_build import (
    RdfBuildOptions,
    _run_mapper_sync,
)

_OPTIONS = RdfBuildOptions()


def _record(cn: str, label: str) -> dict:
    return {
        "_control_number": cn,
        "control_number": cn,
        "title": label,
        "245": {"a": label},
    }


def test_streaming_build_writes_parseable_turtle(tmp_path: Path) -> None:
    out = tmp_path / "run.ttl"
    records = [_record(f"MS{i}", f"Manuscript {i}") for i in range(5)]
    triples, manuscripts, errors, *_rest = _run_mapper_sync(
        records, [], out, build_options=_OPTIONS,
    )
    assert manuscripts == 5
    assert not errors
    assert triples > 0

    graph = Graph().parse(str(out), format="turtle")
    assert len(graph) == triples


def test_streaming_build_applies_overrides(tmp_path: Path) -> None:
    out = tmp_path / "run.ttl"
    records = [_record("MS1", "Manuscript One")]
    base_triples, *_ = _run_mapper_sync(records, [], out, build_options=_OPTIONS)

    graph = Graph().parse(str(out), format="turtle")
    subject, predicate, old_value = next(
        (s, p, o) for s, p, o in graph if isinstance(o, Literal)
    )

    override_triples, *_rest = _run_mapper_sync(
        records, [], out,
        overrides=[{
            "subject_uri": str(subject),
            "predicate_uri": str(predicate),
            "new_value": "Overridden value",
        }],
        build_options=_OPTIONS,
    )
    assert override_triples == base_triples

    reloaded = Graph().parse(str(out), format="turtle")
    values = [str(o) for s, p, o in reloaded if s == subject and p == predicate]
    assert values == ["Overridden value"]
    assert str(old_value) not in values


def test_streaming_build_resume_skips_mapped_records(tmp_path: Path) -> None:
    out = tmp_path / "run.ttl"
    records = [_record(f"MS{i}", f"Manuscript {i}") for i in range(6)]
    checkpoint: dict = {}

    full_triples, full_manuscripts, *_ = _run_mapper_sync(
        records, [], tmp_path / "full.ttl", build_options=_OPTIONS,
    )

    # Simulate a crash after 2 records: a checkpointed artifact truncated
    # to the checkpoint's byte offset.
    _run_mapper_sync(
        records[:2], [], out, build_options=_OPTIONS, checkpoint=checkpoint,
    )
    with open(out, "r+b") as fh:
        fh.truncate(checkpoint["file_bytes"])

    triples, manuscripts, errors, *_rest = _run_mapper_sync(
        records, [], out, build_options=_OPTIONS,
        resume={
            "record_index": checkpoint["record_index"],
            "file_bytes": checkpoint["file_bytes"],
            "manuscripts": checkpoint["manuscripts"],
        },
    )

    assert errors == []
    assert manuscripts == full_manuscripts
    assert triples == full_triples

    resumed = Graph().parse(str(out), format="turtle")
    full = Graph().parse(str(tmp_path / "full.ttl"), format="turtle")
    assert len(resumed) == len(full)
    assert {str(s) for s in resumed.subjects()} == {str(s) for s in full.subjects()}


def test_streaming_build_resume_ignores_wiped_artifact(tmp_path: Path) -> None:
    """A dyno /tmp wipe removes the artifact — resume must start fresh."""
    out = tmp_path / "run.ttl"
    records = [_record("MS1", "Manuscript One")]
    triples, manuscripts, errors, *_rest = _run_mapper_sync(
        records, [], out, build_options=_OPTIONS,
        resume={"record_index": 3, "file_bytes": 999_999},
    )
    assert manuscripts == 1
    assert not errors
    assert triples > 0
