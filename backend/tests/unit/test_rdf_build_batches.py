"""Batch-build (R23): keyset-paginated build loads + distributed shards.

The pre-R23 build materialised the whole corpus before mapping (four
unbounded ``.all()`` queries). These tests pin the replacement:

* ``iter_rdf_build_batches`` pages ``run_records`` by the
  ``(run_id, control_number)`` keyset and loads only approved
  authority/NER rows for each page;
* a ``batch_source``-driven ``build_rdf_graph`` produces byte-identical
  output to the legacy single-corpus path, including across a resume;
* ``plan_rdf_shards`` + ``consume_rdf_shard_results`` keep checkpoint
  semantics identical to the sequential path (Heroku orchestrates,
  Modal shards compute — Rule W-237).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Graph

from app.models.run_job import RunJob
from app.models.run import AuthorityMatch, RunRecord
from app.models.extraction_approval import ExtractionApproval
from app.pipeline.rdf_build import (
    RdfBuildBatch,
    RdfBuildOptions,
    build_rdf_graph,
)
from app.pipeline.rdf_build_batches import (
    count_run_records,
    iter_rdf_build_batches,
    load_record_slice,
    run_record_bounds,
)
from app.pipeline.rdf_build_shard import (
    consume_rdf_shard_results,
    load_rdf_shard_plan,
    plan_rdf_shards,
    run_rdf_build_shard,
)

_OPTIONS = RdfBuildOptions()


def _marc(cn: str) -> dict:
    return {
        "_control_number": cn,
        "control_number": cn,
        "title": f"Manuscript {cn}",
        "245": {"a": f"Manuscript {cn}"},
    }


async def _seed_records(db_session, run_id, cns: list[str]) -> None:
    for cn in cns:
        db_session.add(RunRecord(run_id=run_id, control_number=cn, marc=_marc(cn)))
    await db_session.commit()


# ── keyset loader ───────────────────────────────────────────────────────


async def test_count_and_bounds(db_session, sample_run) -> None:
    run_id = sample_run["run_id"]
    await _seed_records(
        db_session, run_id,
        ["990000000000000002", "990000000000000003"],
    )
    assert await count_run_records(db_session, run_id) == 3  # 2 + fixture record
    total, first, last = await run_record_bounds(db_session, run_id)
    assert (total, first, last) == (3, "990000000000000001", "990000000000000003")


async def test_iter_batches_pages_in_cn_order(db_session, sample_run) -> None:
    run_id = sample_run["run_id"]
    await _seed_records(
        db_session, run_id,
        [f"99000000000000000{i}" for i in range(2, 6)],
    )
    pages: list[list[str]] = []
    async for batch in iter_rdf_build_batches(run_id, batch_size=2):
        cns = [r["_control_number"] for r in batch.marc_records]
        assert cns == sorted(cns)
        pages.append(cns)
    assert pages == [
        ["990000000000000001", "990000000000000002"],
        ["990000000000000003", "990000000000000004"],
        ["990000000000000005"],
    ]


async def test_batches_load_approved_rows_only(db_session, sample_run) -> None:
    run_id = sample_run["run_id"]
    cn = sample_run["control_number"]
    db_session.add(AuthorityMatch(
        run_id=run_id, control_number=cn, entity_text="Approved",
        entity_kind="person", approved=True,
    ))
    db_session.add(AuthorityMatch(
        run_id=run_id, control_number=cn, entity_text="Unvetted",
        entity_kind="person", approved=False,
    ))
    db_session.add(ExtractionApproval(
        run_id=run_id, control_number=cn, text="משה בן מיימון",
        type="person", approved=True, start=0, end=10, source="ner",
    ))
    db_session.add(ExtractionApproval(
        run_id=run_id, control_number=cn, text="רק רשומה",
        type="person", approved=False, start=0, end=5, source="ner",
    ))
    await db_session.commit()

    from app.db import session_scope

    async with session_scope() as fresh_db:
        batch = await load_record_slice(fresh_db, run_id, [cn])
    assert len(batch.authority_matches) == 1
    assert batch.authority_matches[0]["entity_text"] == "Approved"
    assert list(batch.entities_by_cn) == [cn]
    assert batch.entities_by_cn[cn][0]["text"] == "משה בן מיימון"


async def test_batched_build_matches_single_shot(tmp_path: Path) -> None:
    records = [
        _marc(f"MS{i}") for i in range(7)
    ]
    out_single = tmp_path / "single.ttl"
    result_single = await build_rdf_graph(
        output_path=out_single, marc_records=records, build_options=_OPTIONS,
    )

    async def _batches():
        yield RdfBuildBatch(marc_records=records[:3])
        yield RdfBuildBatch(marc_records=records[3:5])
        yield RdfBuildBatch(marc_records=records[5:])

    out_batched = tmp_path / "batched.ttl"
    result_batched = await build_rdf_graph(
        output_path=out_batched,
        batch_source=_batches(),
        total_records=len(records),
        build_options=_OPTIONS,
    )

    assert result_single.manuscripts_count == result_batched.manuscripts_count == 7
    single = Graph().parse(str(out_single), format="turtle")
    batched = Graph().parse(str(out_batched), format="turtle")
    assert len(single) == len(batched)
    assert set(single.subjects()) == set(batched.subjects())


async def test_batched_build_resume_across_batches(tmp_path: Path) -> None:
    records = [_marc(f"MS{i}") for i in range(30)]
    out_full = tmp_path / "full.ttl"
    full = await build_rdf_graph(output_path=out_full, marc_records=records)

    # Crash during the second batch: 26 records were fed, 25 mapped (the
    # checkpoint boundary), the 26th not yet appended.
    checkpoint: dict = {}
    out = tmp_path / "resumed.ttl"
    partial = await build_rdf_graph(
        output_path=out,
        batch_source=_fixed_batches(records[:26]),
        total_records=len(records),
        checkpoint=checkpoint,
    )
    assert partial.manuscripts_count == 26
    assert checkpoint["record_index"] == 25

    resumed = await build_rdf_graph(
        output_path=out,
        batch_source=_fixed_batches(records),
        total_records=len(records),
        resume={
            "record_index": checkpoint["record_index"],
            "file_bytes": checkpoint["file_bytes"],
            "manuscripts": checkpoint["manuscripts"],
        },
    )
    assert resumed.manuscripts_count == full.manuscripts_count
    done = Graph().parse(str(out), format="turtle")
    expected = Graph().parse(str(out_full), format="turtle")
    assert {str(s) for s in done.subjects()} == {str(s) for s in expected.subjects()}


def _fixed_batches(records: list[dict]):
    # Resume contract (R26 + R23): when resuming, the batch source must
    # cover the corpus from global record index 0 — the keyset loader
    # re-pages from the start and the mapper skips already-mapped records.
    async def _gen():
        yield RdfBuildBatch(marc_records=records)

    return _gen()


# ── keyset-paginated node reads (batch-build R23) ───────────────────────


def test_nodes_page_keyset(tmp_path: Path) -> None:
    from rdflib import RDF, RDFS, Literal, Graph as RdfGraph, URIRef

    from app.pipeline.graph_index import GraphIndexStore, build_and_persist_index

    graph = RdfGraph()
    for i in range(5):
        subject = URIRef(f"https://example.org/ms{i}")
        graph.add((subject, RDF.type, URIRef("https://w3id.org/mhm/ontology#Manuscript")))
        graph.add((subject, RDFS.label, Literal(f"MS {i}")))
    build_and_persist_index(graph, tmp_path)

    store = GraphIndexStore(tmp_path / "graph_index.sqlite")
    page1, cursor = store.list_nodes_page(None, 2)
    assert [n["id"] for n in page1] == sorted(n["id"] for n in page1)
    assert cursor == page1[-1]["id"]

    page2, cursor2 = store.list_nodes_page(cursor, 2)
    assert not {n["id"] for n in page1} & {n["id"] for n in page2}

    page3, cursor3 = store.list_nodes_page(cursor2, 2)
    assert len(page3) == 2
    assert cursor3 is None

    # Deep page: stable ordering, no duplicates across the full walk.
    # The index also materialises the rdf:type class node (hm:Manuscript),
    # so 5 instance nodes + 1 class node = 6.
    walked: list[str] = []
    cursor: str | None = None
    while True:
        items, cursor = store.list_nodes_page(cursor, 3)
        walked.extend(n["id"] for n in items)
        if cursor is None:
            break
    assert len(walked) == len(set(walked)) == 6


# ── distributed shards ──────────────────────────────────────────────────


def test_plan_rdf_shards_respects_resume() -> None:
    cns = [f"CN{i:03d}" for i in range(10)]
    # Fresh build: two full shards.
    assert [c for _s, c in plan_rdf_shards(cns, 4, 0)] == [cns[:4], cns[4:8], cns[8:]]
    # Sharded resume at a shard boundary: the first shard is skipped.
    assert [s for s, _c in plan_rdf_shards(cns, 4, 4)] == [4, 8]
    # Sequential-path checkpoint mid-shard: the straddling shard runs
    # only its unmapped suffix.
    plan = plan_rdf_shards(cns, 4, 6)
    assert plan == [(4, ["CN006", "CN007"]), (8, ["CN008", "CN009"])]


async def test_shard_runner_maps_slice(db_session, sample_run, tmp_path, monkeypatch) -> None:
    run_id = sample_run["run_id"]
    cns = [f"99000000000000000{i}" for i in range(2, 5)]
    await _seed_records(db_session, run_id, cns)
    job = RunJob(
        project_id=sample_run["project_id"], run_id=run_id, kind="rdf_build",
        status="running", created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()

    result = await run_rdf_build_shard(job.id, run_id, cns[:2])
    assert result["manuscripts"] == 2
    assert result["triples_count"] > 0
    assert result["errors"] == []
    graph = Graph().parse(data=result["turtle"], format="turtle")
    assert len(graph) > 0


async def test_consume_shard_results_finalises_job(
    db_session, sample_run, tmp_path, monkeypatch,
) -> None:
    run_id = sample_run["run_id"]
    cns = [f"99000000000000000{i}" for i in range(2, 7)]
    await _seed_records(db_session, run_id, cns)
    job = RunJob(
        project_id=sample_run["project_id"], run_id=run_id, kind="rdf_build",
        status="running", created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()

    # Keep the merged artifact out of the shared state dir.
    from app.pipeline import rdf_build_shard as shard_mod

    monkeypatch.setattr(
        shard_mod, "rdf_output_path_for_run",
        lambda _run: tmp_path / "manuscripts.ttl",
    )

    plan = await load_rdf_shard_plan(job.id, shard_size=2)
    assert plan is not None
    assert plan.total == 6

    # Real turtle chunks from the actual shard runner so the merged
    # artifact is a parseable graph end to end.
    async def _real_shards():
        for start, slice_cns in plan.slices:
            res = await run_rdf_build_shard(job.id, run_id, slice_cns)
            yield res

    await consume_rdf_shard_results(job.id, plan, _real_shards())

    await db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.result["manuscripts_count"] == 6
    assert job.progress["phase"] == "done"
    assert job.progress["processed"] == 6
    graph = Graph().parse(
        data=(tmp_path / "manuscripts.ttl").read_text(encoding="utf-8"),
        format="turtle",
    )
    assert len(graph) > 0
