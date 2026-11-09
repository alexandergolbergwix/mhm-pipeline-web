"""Live upload progress payload helpers (Rule W-141 modal)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.pipeline.wikidata_upload_job import (
    slim_upload_progress_outcome,
    upload_outcome_counts,
)


@pytest.fixture(autouse=True)
def _stub_studio_source_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    async def get_cache_row(*args, **kwargs):
        return SimpleNamespace(
            input_fingerprint="a" * 64,
            summary={"overrides_applied": True, "source_items_sha256": "b" * 64},
        )

    async def cache_is_current(*args, **kwargs):
        return True

    monkeypatch.setattr("app.routers.wikidata_studio._get_studio_cache_row", get_cache_row)
    monkeypatch.setattr("app.routers.wikidata_studio._studio_cache_is_current", cache_is_current)


def test_slim_upload_progress_outcome_includes_label_and_entity_type() -> None:
    row = slim_upload_progress_outcome(
        SimpleNamespace(
            local_id="QDraft_MS_1",
            label="Ms. Heb. 1",
            entity_type="manuscript",
            status="success",
            qid="Q999",
            message="Created Q999 (3 claims)",
        ),
    )
    assert row["local_id"] == "QDraft_MS_1"
    assert row["label"] == "Ms. Heb. 1"
    assert row["entity_type"] == "manuscript"
    assert row["status"] == "success"
    assert row["qid"] == "Q999"
    assert row["wikibase_id"] == "Q999"


def test_upload_outcome_counts_tallies_statuses() -> None:
    outcomes = [
        SimpleNamespace(status="success"),
        SimpleNamespace(status="updated"),
        SimpleNamespace(status="adopted"),
        SimpleNamespace(status="blocked"),
        SimpleNamespace(status="skipped"),
        SimpleNamespace(status="failed"),
        SimpleNamespace(status="pending"),
    ]
    counts = upload_outcome_counts(outcomes)
    assert counts == {
        "created": 1,
        "updated": 1,
        "adopted": 1,
        "blocked": 1,
        "skipped": 1,
        "failed": 1,
        "pending": 1,
    }


def test_slim_upload_progress_outcome_processing_shape() -> None:
    row = slim_upload_progress_outcome(
        SimpleNamespace(
            local_id="QDraft_MS_1",
            label="Ms. Heb. 1",
            entity_type="manuscript",
            status="processing",
            qid=None,
            message="Processing…",
        ),
    )
    assert row["status"] == "processing"
    assert row["message"] == "Processing…"
    assert row["qid"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("sharded,first_qid,mapped_qid,ownership,accepted,current,links", [
    (False, "Q123", "Q123", "own", None, None, True),
    (False, "Q123", "Q999", "own", None, None, False),
    (False, "Q123", None, "own", None, None, False),
    (True, "Q123", "Q123", "own", None, None, True),
    (True, "Q123", "Q999", "own", None, None, False),
    (True, None, "Q123", "own", None, None, False),
    (True, "Q123", "Q123", "foreign", "Q123", "Q123", True),
    (True, "Q123", "Q999", "foreign", "Q123", "Q123", False),
    (True, "Q123", "Q123", "foreign", "Q123", "Q999", False),
    (True, "Q123", "Q123", "foreign", None, "Q123", False),
])
async def test_pass2_job_checks_actual_target_before_link_write(
    sample_run, db_session, monkeypatch, sharded, first_qid, mapped_qid,
    ownership, accepted, current, links,
) -> None:
    from app.models.item_override import WikidataItemOverride
    from app.models.run_job import RunJob
    from app.pipeline import wikidata_upload as upload
    from app.pipeline import wikidata_upload_job as upload_job
    from converter.wikidata.item_models import WikidataItem, WikidataStatement

    source = WikidataItem(
        local_id="source", entity_type="other", labels={"en": "Source"},
        statements=[WikidataStatement("P50", "__LOCAL:target", "item")],
    )
    first = upload.UploadOutcome(
        local_id="source", label="Source", entity_type="other", qid=first_qid,
        status="updated", message="first pass", added_properties=[],
        ownership=ownership, accepted_foreign_qid=accepted,
    )
    params = {"upload_target": "live", "_wikidata_token": "fake"}
    if sharded:
        params["_step1_state"] = {
            "outcomes": [dict(first.__dict__)],
            "created_qids": {"source": mapped_qid, "target": "Q500"},
            "deferred": {"source": [dict(source.statements[0].__dict__)]},
        }
    job = RunJob(
        project_id=sample_run["project_id"], run_id=sample_run["run_id"],
        kind="wikidata_upload", status="running", params=params, progress={},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    if current:
        db_session.add(WikidataItemOverride(
            run_id=sample_run["run_id"], local_id="source",
            accept_foreign_modify=True, accepted_foreign_qid=current,
        ))
    await db_session.commit()
    await db_session.refresh(job)

    async def build(*args, **kwargs):
        return [source]

    class FakeUploader:
        def __init__(self, **kwargs):
            pass

        def ensure_authenticated(self):
            return None

    writes = []

    async def upload_items(items, **kwargs):
        item = items[0]
        writes.append(item.existing_qid)
        if item.existing_qid is None:
            return [first]
        assert item.existing_qid == "Q123"
        if ownership == "foreign":
            assert kwargs["accept_by_local_id"]["source"].accepted_foreign_qid == item.existing_qid
        return [upload.UploadOutcome(
            local_id="source", label="Source", entity_type="other", qid=item.existing_qid,
            status="updated", message="linked", added_properties=[], ownership=ownership,
        )]

    async def persist(*args, **kwargs):
        return None

    def remember(mapping, local_id, qid, status, **kwargs):
        mapping["target"] = "Q500"
        if mapped_qid:
            mapping[local_id] = mapped_qid
        else:
            mapping.pop(local_id, None)

    monkeypatch.setattr("app.routers.wikidata_studio._build_native_items", build)
    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", FakeUploader)
    monkeypatch.setattr(
        "converter.wikidata.reconciler.prewarm_reconciler_for_items", lambda *a: None,
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.prefetch_entities_for_upload",
        lambda *a, **kw: ({}, {}),
    )
    monkeypatch.setattr(upload, "_make_reconciler", lambda: SimpleNamespace(_cache={}))
    monkeypatch.setattr(upload, "upload_items", upload_items)
    monkeypatch.setattr(upload, "persist_upload_outcomes", persist)
    monkeypatch.setattr(upload, "remember_created_qid", remember)
    monkeypatch.setattr(
        upload, "build_upload_corpus_quality_preflight",
        lambda items: upload.UploadCorpusQualityPreflight(
            decisions={"source": upload.UploadQualityDecision(eligible=True)},
        ),
    )

    await upload_job.run_wikidata_upload_job(job.id)
    await db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.result["link_outcomes"][0]["status"] == (
        "linked" if links else "unresolved" if not mapped_qid else "skipped_foreign"
    )
    assert writes == ([] if sharded else [None]) + (["Q123"] if links else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("sharded,dry_run", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("metadata,author_resolved,expected", [
    ("none", False, "fallback"),
    ("reference", False, "unresolved"),
    ("blocks", False, "unresolved"),
    ("qualifier", False, "unresolved"),
    ("local_reference", False, "unresolved"),
    ("blocks", True, "resolved"),
    ("reference", True, "resolved"),
    ("qualifier", True, "resolved"),
    ("local_reference", True, "unresolved"),
])
async def test_deferred_author_fallback_preserves_metadata_boundary(
    sample_run, db_session, monkeypatch, sharded, dry_run, metadata, author_resolved, expected,
) -> None:
    from app.models.run_job import RunJob
    from app.pipeline import wikidata_upload as upload
    from app.pipeline import wikidata_upload_job as upload_job
    from converter.wikidata.item_models import WikidataItem, WikidataStatement

    ref = {"property": "P248", "value": "Q1234", "type": "item"}
    if metadata == "local_reference":
        ref = {"property": "P248", "value": "__LOCAL:missing-source", "type": "item"}
    stmt = WikidataStatement(
        "P50", "__LOCAL:author", "item",
        qualifiers=[{"property": "P1932", "value": "Author", "type": "string"}]
        if metadata == "qualifier" else [],
        references=[ref] if metadata in {"reference", "blocks", "local_reference"} else [],
        reference_blocks=[[ref]] if metadata == "blocks" else [],
    )
    source = WikidataItem(local_id="source", entity_type="work", labels={"en": "Work"},
                          statements=[stmt])
    author = WikidataItem(local_id="author", entity_type="person", labels={"en": "Author"})
    first = upload.UploadOutcome("source", "Work", "work", "Q123", "updated", "ok", [],
                                 ownership="own")
    person = upload.UploadOutcome(
        "author", "Author", "person", "Q500" if author_resolved else None,
        "created" if author_resolved else "skipped", "ok", [],
    )
    params = {"upload_target": "dry_run" if dry_run else "live", "_wikidata_token": "fake"}
    if sharded:
        params["_step1_state"] = {
            "outcomes": [dict(first.__dict__), dict(person.__dict__)],
            "created_qids": {"source": "Q123", **({"author": "Q500"} if author_resolved else {})},
            "deferred": {"source": [dict(stmt.__dict__)]},
        }
    job = RunJob(project_id=sample_run["project_id"], run_id=sample_run["run_id"],
                 kind="wikidata_upload", status="running", params=params, progress={},
                 created_by=sample_run["user_id"])
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    async def build(*args, **kwargs):
        return [source, author]

    class FakeUploader:
        def __init__(self, **kwargs):
            pass

        def ensure_authenticated(self):
            return None

    writes = []

    async def upload_items(items, **kwargs):
        item = items[0]
        if item.existing_qid:
            writes.append(item)
            return [first]
        return [first if item.local_id == "source" else person]

    async def persist(*args, **kwargs):
        return None

    monkeypatch.setattr("app.routers.wikidata_studio._build_native_items", build)
    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", FakeUploader)
    monkeypatch.setattr(
        "converter.wikidata.reconciler.prewarm_reconciler_for_items", lambda *a: None,
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.prefetch_entities_for_upload", lambda *a, **kw: ({}, {}),
    )
    monkeypatch.setattr(upload, "_make_reconciler", lambda: SimpleNamespace(_cache={}))
    monkeypatch.setattr(upload, "upload_items", upload_items)
    monkeypatch.setattr(upload, "persist_upload_outcomes", persist)
    monkeypatch.setattr(upload, "build_upload_corpus_quality_preflight", lambda items:
                        upload.UploadCorpusQualityPreflight(decisions={
                            item.local_id: upload.UploadQualityDecision(True) for item in items
                        }))

    await upload_job.run_wikidata_upload_job(job.id)
    await db_session.refresh(job)
    assert job.status == "succeeded"
    result = job.result["link_outcomes"][0]
    assert result["status"] == ("unresolved" if expected == "unresolved" else
                                "would_link" if dry_run else "linked")
    assert len(writes) == (0 if dry_run or expected == "unresolved" else 1)
    if expected == "unresolved":
        assert result["property_id"] == "P50"
        unresolved_target = "missing-source" if author_resolved else "author"
        assert result["target_local_id"] == unresolved_target
        assert unresolved_target in result["message"]
    elif writes:
        written = writes[0].statements[0]
        assert (written.property_id, written.value) == (
            ("P2093", "Author") if expected == "fallback" else ("P50", "Q500")
        )
        if expected == "resolved":
            assert written.references == stmt.references
            assert written.reference_blocks == stmt.reference_blocks
            assert written.qualifiers == stmt.qualifiers


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "regular_first", "regular_second", "shard_first", "sharded_second",
])
async def test_job_checks_revocation_at_each_actual_write(
    sample_run, db_session, monkeypatch, path,
) -> None:
    import asyncio

    from app.models.item_override import WikidataItemOverride
    from app.models.run_job import RunJob
    from app.pipeline import wikidata_upload as upload
    from app.pipeline import wikidata_upload_job as upload_job
    from converter.wikidata.item_models import WikidataItem, WikidataStatement

    source = WikidataItem(
        local_id="source", entity_type="other", existing_qid="Q123",
        labels={"en": "Source"}, statements=[WikidataStatement("P50", "__LOCAL:target", "item")],
    )
    first = upload.UploadOutcome(
        local_id="source", label="Source", entity_type="other", qid="Q123",
        status="updated", message="first pass", added_properties=[],
        ownership="foreign", accepted_foreign_qid="Q123",
    )
    params = {"upload_target": "live", "_wikidata_token": "fake"}
    if path == "sharded_second":
        params["_step1_state"] = {
            "outcomes": [dict(first.__dict__)],
            "created_qids": {"source": "Q123", "target": "Q500"},
            "deferred": {"source": [dict(source.statements[0].__dict__)]},
        }
    job = RunJob(
        project_id=sample_run["project_id"], run_id=sample_run["run_id"],
        kind="wikidata_upload", status="running", params=params, progress={},
        created_by=sample_run["user_id"],
    )
    approval = WikidataItemOverride(
        run_id=sample_run["run_id"], local_id="source",
        accept_foreign_modify=True, accepted_foreign_qid="Q123",
    )
    db_session.add_all([job, approval])
    await db_session.commit()
    await db_session.refresh(job)
    writes = []

    class FakeUploader:
        def __init__(self, **kwargs):
            self._foreign_accept_qids = set()

        def ensure_authenticated(self):
            return None

        def _is_our_item(self, qid):
            return False

        def register_foreign_accept(self, qid):
            self._foreign_accept_qids.add(qid)

        def upload_item(self, item, *, created_qids):
            writes.append(item.existing_qid)
            created_qids["target"] = "Q500"
            return SimpleNamespace(
                qid=item.existing_qid, status="updated", message="ok", added_properties=[],
            )

    async def build(*args, **kwargs):
        return [source]

    async def persist(*args, **kwargs):
        return None

    def prepare(items, *args, **kwargs):
        item = items[0]
        return [upload.PreparedItem(
            item=item, local_id="source", label="Source", entity_type="other",
            existing_qid="Q123", method="prebuilt", blocked=False,
            block_status="", block_message="", ownership="foreign", allow_foreign_modify=True,
        )]

    entered = asyncio.Event()
    proceed = asyncio.Event()
    real_check = upload._persisted_foreign_accept
    calls = 0
    pause_on = 2 if path == "regular_second" else 1

    async def check(*args):
        nonlocal calls
        calls += 1
        if calls == pause_on:
            entered.set()
            await proceed.wait()
        return await real_check(*args)

    monkeypatch.setattr("app.routers.wikidata_studio._build_native_items", build)
    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", FakeUploader)
    monkeypatch.setattr(
        "converter.wikidata.reconciler.prewarm_reconciler_for_items", lambda *a: None,
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.prefetch_entities_for_upload", lambda *a, **kw: ({}, {}),
    )
    monkeypatch.setattr("app.db.reset_engine", persist)
    monkeypatch.setattr(upload, "_make_reconciler", lambda: SimpleNamespace(_cache={}))
    monkeypatch.setattr(upload, "_prepare_for_upload", prepare)
    monkeypatch.setattr(upload, "_persisted_foreign_accept", check)
    monkeypatch.setattr(upload, "persist_upload_outcomes", persist)
    monkeypatch.setattr(
        upload, "build_upload_corpus_quality_preflight",
        lambda items: upload.UploadCorpusQualityPreflight(
            decisions={"source": upload.UploadQualityDecision(eligible=True)},
        ),
    )

    operation = (
        upload_job.run_wikidata_upload_shard(job.id, sample_run["run_id"], ["source"])
        if path == "shard_first" else upload_job.run_wikidata_upload_job(job.id)
    )
    task = asyncio.create_task(operation)
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        approval.accept_foreign_modify = False
        await db_session.commit()
    finally:
        proceed.set()
    result = await task
    if path == "shard_first":
        assert result["outcomes"][0]["status"] == "blocked"
    else:
        await db_session.refresh(job)
        if path == "regular_first":
            assert job.result["outcomes"][0]["status"] == "blocked"
        else:
            assert job.result["link_outcomes"][0]["status"] == "blocked"
    assert writes == (["Q123"] if path == "regular_second" else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,reason,count,slots", [
    (1, "cancel", 3, 4), (2, "cancel", 3, 4),
    (1, "cancel", 3, 1), (2, "cancel", 3, 1),
    (1, "cancel_tail", 2, 4), (2, "cancel_tail", 2, 4),
    (1, "external", 2, 4), (2, "external", 2, 4),
    (1, "auth", 4, 4), (2, "auth", 4, 4),
    (1, "auth", 2, 4), (2, "auth", 2, 4),
    (1, "error", 4, 4), (2, "error", 4, 4),
    (1, "error", 2, 4), (2, "error", 2, 4),
])
async def test_regular_job_settles_inflight_before_terminal(
    sample_run, db_session, monkeypatch, phase, reason, count, slots,
) -> None:
    import asyncio
    import threading

    from app.models.run_job import RunJob
    from app.pipeline import wikidata_upload as upload
    from app.pipeline import wikidata_upload_job as upload_job
    from converter.wikidata.item_models import WikidataItem, WikidataStatement

    sources = [WikidataItem(
        local_id=f"source{i}", entity_type="other", labels={"en": f"Source {i}"},
        statements=[WikidataStatement("P50", "__LOCAL:target", "item")] if phase == 2 else [],
    ) for i in range(count)]
    params = {"upload_target": "live", "_wikidata_token": "fake"}
    if phase == 2:
        params["_step1_state"] = {
            "outcomes": [dict(upload.UploadOutcome(
                local_id=item.local_id, label=item.local_id, entity_type="other",
                qid=f"Q{i + 1}", status="updated", message="ok", added_properties=[],
                ownership="own",
            ).__dict__) for i, item in enumerate(sources)],
            "created_qids": {**{item.local_id: f"Q{i + 1}" for i, item in enumerate(sources)},
                             "target": "Q500"},
            "deferred": {item.local_id: [dict(item.statements[0].__dict__)] for item in sources},
        }
    job = RunJob(
        project_id=sample_run["project_id"], run_id=sample_run["run_id"],
        kind="wikidata_upload", status="running", params=params, progress={},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    started = []
    finished = []
    audited = []
    terminal = []
    running = asyncio.Event()
    release = asyncio.Event()
    thread_release = threading.Event()
    stop_seen = asyncio.Event()
    cancel_calls = 0
    required = min(2, slots) if reason in {"cancel", "cancel_tail"} else count

    async def is_cancelled(job_id):
        nonlocal cancel_calls
        cancel_calls += 1
        if reason in {"cancel", "cancel_tail"} and cancel_calls == 4:
            await running.wait()
            stop_seen.set()
            return True
        return False

    async def write(items, **kwargs):
        item = items[0]
        started.append(item.local_id)
        if len(started) == required:
            running.set()
        if reason in {"auth", "error"} and item.local_id == "source0":
            await running.wait()
            stop_seen.set()
            if reason == "error":
                raise RuntimeError("write failure")
            return [upload.UploadOutcome(
                local_id=item.local_id, label=item.local_id, entity_type="other", qid="Q1",
                status="failed", message="permissiondenied", added_properties=[],
                ownership="own",
            )]
        if reason == "external":
            await asyncio.to_thread(thread_release.wait)
        else:
            await release.wait()
        finished.append(item.local_id)
        return [upload.UploadOutcome(
            local_id=item.local_id, label=item.local_id, entity_type="other", qid="Q2",
            status="updated", message="ok", added_properties=[], ownership="own",
        )]

    async def finish(job_id, **kwargs):
        terminal.append(kwargs)

    async def noop(*args, **kwargs):
        return None

    async def persist(db, items, rows, **kwargs):
        audited.extend(row.local_id for row in rows)

    class FakeUploader:
        instances = 0

        def __init__(self, **kwargs):
            type(self).instances += 1
            if type(self).instances > slots:
                raise RuntimeError("no extra slot")

        def ensure_authenticated(self):
            return None

    async def build(*args, **kwargs):
        return sources

    monkeypatch.setattr("app.routers.wikidata_studio._build_native_items", build)
    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", FakeUploader)
    monkeypatch.setattr(
        "converter.wikidata.reconciler.prewarm_reconciler_for_items", lambda *a: None,
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.prefetch_entities_for_upload", lambda *a, **kw: ({}, {}),
    )
    monkeypatch.setattr(upload, "_make_reconciler", lambda: SimpleNamespace(_cache={}))
    monkeypatch.setattr(upload, "upload_items", write)
    monkeypatch.setattr(upload, "persist_upload_outcomes", persist)
    monkeypatch.setattr(
        upload, "build_upload_corpus_quality_preflight",
        lambda items: upload.UploadCorpusQualityPreflight(
            decisions={item.local_id: upload.UploadQualityDecision(True) for item in items},
        ),
    )
    monkeypatch.setattr(upload_job, "is_cancel_requested", is_cancelled)
    monkeypatch.setattr(upload_job, "finish_job", finish)
    monkeypatch.setattr(upload_job, "update_job_progress", noop)
    task = asyncio.create_task(upload_job.run_wikidata_upload_job(job.id))
    try:
        if reason == "external":
            await asyncio.wait_for(running.wait(), timeout=5)
            task.cancel()
        else:
            await asyncio.wait_for(stop_seen.wait(), timeout=5)
        await asyncio.sleep(0.05)
        assert not task.done()
        assert terminal == []
    finally:
        release.set()
        thread_release.set()
    if reason == "external":
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert len(finished) == required
        assert set(finished).issubset(set(audited))
        return
    await asyncio.wait_for(task, timeout=5)
    assert len(terminal) == 1
    assert terminal[0]["status"] == (
        "cancelled" if reason in {"cancel", "cancel_tail"} else "failed"
    )
    assert len(finished) == (required if reason in {"cancel", "cancel_tail"} else required - 1)
    assert set(finished).issubset(set(audited))
    assert len(started) == required
    rows = terminal[0]["result"]["outcomes" if phase == 1 else "link_outcomes"]
    assert len(rows) == required
