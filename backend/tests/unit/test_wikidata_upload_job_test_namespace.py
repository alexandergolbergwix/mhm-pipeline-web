"""Offline test-wiki job namespace and ledger tests."""

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from app.pipeline.wikidata_upload_job import _resolve_existing_qid_map
from converter.wikidata.item_models import WikidataItem, WikidataStatement


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


def test_test_qid_map_requires_exact_unambiguous_test_ledger():
    author = WikidataItem(local_id="author", entity_type="person", existing_qid="Q900")
    work = WikidataItem(local_id="work", entity_type="work", statements=[
        WikidataStatement("P50", "__LOCAL:author", "item"),
    ])
    key = "wikidata-test:work:work:author:__LOCAL:author"
    assert _resolve_existing_qid_map([author, work], {
        "wikidata:person:author": "Q900",
        "wikidata-test:work:work": "Q777",
    }, is_test=True) == {}
    assert _resolve_existing_qid_map([author, work], {
        "wikidata-test:person:author": "Q901", key: "Q902",
    }, is_test=True) == {"author": "Q901", "work": "Q902"}
    assert _resolve_existing_qid_map([author, work], {
        "wikidata-test:person:author": "Q901", key: "Q901",
    }, is_test=True) == {}
    assert _resolve_existing_qid_map([author, author], {
        "wikidata-test:person:author": "Q901",
    }, is_test=True) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("proven", [
    True, False, "mismatch", "mismatch_no_metadata", "collision", "ambiguous",
])
async def test_sharded_deferred_json_keeps_test_and_live_qid_distinct(
    sample_run, db_session, monkeypatch, proven,
):
    from app.models.run_job import RunJob
    from app.models.wikibase_entity_mapping import WikibaseEntityMapping
    from app.pipeline import wikidata_upload as upload
    from app.pipeline import wikidata_upload_job as job_module
    from converter.wikidata.test_wiki_compat import WikiTestAdaptStats, pid_map_key
    from converter.wikidata.uploader import WikidataUploader

    author = WikidataItem(local_id="author", entity_type="person", existing_qid="Q900")
    source = WikidataItem(local_id="source", entity_type="other", labels={"en": "Source"})
    reference = {"property": "P248", "value": "Q900", "type": "item",
                 "value_namespace": "wikidata"}
    local_reference = {"property": "P50", "value": "__LOCAL:author", "type": "item"}
    qualifier = {"property": "P3831", "value": "__LOCAL:author", "type": "item"}
    statement = WikidataStatement("P50", "__LOCAL:author", "item",
                                  qualifiers=[qualifier], references=[reference, local_reference],
                                  reference_blocks=[[reference, local_reference]])
    if proven == "mismatch_no_metadata":
        statement = WikidataStatement("P50", "__LOCAL:author", "item")
    source.statements = [statement, WikidataStatement(
        "P31", {"id": "Q47461344", "value_namespace": "wikidata"}, "item",
    )]
    first = upload.UploadOutcome("source", "Source", "other", "Q123", "updated", "ok", [],
                                 ownership="own")
    skipped_author = upload.UploadOutcome("author", "Author", "person", None, "skipped", "ok", [])
    params = {"upload_target": "test", "_wikidata_token": "fake", "_step1_state": {
        "outcomes": [asdict(first), asdict(skipped_author)],
        "created_qids": {"source": "Q123", "author": "Q900"},
        "deferred": {"source": [asdict(statement)]},
    }}
    job = RunJob(project_id=sample_run["project_id"], run_id=sample_run["run_id"],
                 kind="wikidata_upload", status="running", params=json.loads(json.dumps(params)),
                 progress={}, created_by=sample_run["user_id"])
    db_session.add(job)
    db_session.add(WikibaseEntityMapping(ontology_uri="wikidata-test:other:source",
                                        entity_kind="instance", wikibase_id="Q123", label="Source"))
    if proven:
        db_session.add(WikibaseEntityMapping(ontology_uri="wikidata-test:person:author",
                                            entity_kind="instance",
                                            wikibase_id="Q901" if proven in {
                                                "mismatch", "mismatch_no_metadata",
                                            } else "Q900",
                                            label="Author"))
    if proven == "collision":
        db_session.add(WikibaseEntityMapping(ontology_uri="wikidata-test:person:other",
                                            entity_kind="instance",
                                            wikibase_id="Q900", label="Other"))
    if proven == "ambiguous":
        db_session.add(WikibaseEntityMapping(ontology_uri="wikidata-test:person:author",
                                            entity_kind="instance",
                                            wikibase_id="Q901", label="Author"))
    await db_session.commit()
    await db_session.refresh(job)

    class FakeUploader(WikidataUploader):
        def ensure_authenticated(self):
            self._authenticated_user = "test-user"

        def _ensure_test_maps_for_item(self, item, *, allow_unmapped_live=False):
            return WikiTestAdaptStats()

    instances = []

    def create_uploader(**kwargs):
        instance = FakeUploader(**kwargs)
        instance._test_qid_map = {"Q900": "Q901", "Q47461344": "Q902"}
        pids = ("P31", "P50", "P248", "P3831")
        instance._test_pid_map = {pid_map_key(pid, "item"): pid for pid in pids}
        instance._test_property_datatypes = {pid: "wikibase-item" for pid in pids}
        instance._test_entity_exists = {"Q900": True, "Q901": True, "Q902": True}
        instances.append(instance)
        return instance

    writes = []

    async def fake_upload(items, **kwargs):
        item = items[0]
        item.statements = [WikidataStatement(**json.loads(json.dumps(asdict(s))))
                           for s in item.statements]
        uploader = kwargs["uploader"]
        mapped, stats = uploader._adapt_item_for_test_wiki(item)
        assert not stats.skipped
        writes.append(uploader._build_claim(mapped.statements[0]).get_json())
        return [first]

    async def build(*args, **kwargs):
        return [source, author]

    monkeypatch.setattr("app.routers.wikidata_studio._build_native_items", build)
    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", create_uploader)
    monkeypatch.setattr("app.pipeline.wikidata_existence.prefetch_entities_for_upload",
                        lambda *a, **kw: ({}, {}))
    monkeypatch.setattr(upload, "_make_reconciler", lambda: SimpleNamespace(_cache={}))
    monkeypatch.setattr(upload, "upload_items", fake_upload)
    monkeypatch.setattr(upload, "build_upload_corpus_quality_preflight", lambda items:
                        upload.UploadCorpusQualityPreflight(decisions={
                            item.local_id: upload.UploadQualityDecision(True) for item in items
                        }))

    await job_module.run_wikidata_upload_job(job.id)
    await db_session.refresh(job)
    if proven in ("collision", "ambiguous"):
        assert job.status == "failed"
        assert not writes
        return
    assert job.status == "succeeded"
    expected_status = "linked" if proven is True else "unresolved"
    assert job.result["link_outcomes"][0]["status"] == expected_status
    if proven is True:
        claim = writes[0]
        assert claim["mainsnak"]["datavalue"]["value"]["id"] == "Q900"
        assert claim["references"][0]["snaks"]["P248"][0]["datavalue"]["value"]["id"] == "Q901"
        assert claim["references"][0]["snaks"]["P50"][0]["datavalue"]["value"]["id"] == "Q900"
        assert claim["qualifiers"]["P3831"][0]["datavalue"]["value"]["id"] == "Q900"
    else:
        assert not writes


@pytest.mark.asyncio
@pytest.mark.parametrize("sharded", [False, True])
async def test_new_test_targets_are_persisted_before_slot_registration(
    sample_run, db_session, monkeypatch, sharded,
):
    from app.models.run_job import RunJob
    from app.pipeline import wikidata_upload as upload
    from app.pipeline import wikidata_upload_job as job_module
    from app.pipeline.wikidata_qid_ledger import load_global_ledger
    from converter.wikidata.uploader import WikidataUploader

    source = WikidataItem(local_id="source", entity_type="work", existing_qid="Q123",
                          labels={"en": "Source"}, statements=[
                              WikidataStatement("P50", "__LOCAL:author", "item"),
                              WikidataStatement("P31", "Q47461344", "item"),
                          ])
    author = WikidataItem(local_id="author", entity_type="person", existing_qid="Q900",
                          labels={"en": "Author"})
    job = RunJob(project_id=sample_run["project_id"], run_id=sample_run["run_id"],
                 kind="wikidata_upload", status="running",
                 params={"upload_target": "test", "_wikidata_token": "fake"}, progress={},
                 created_by=sample_run["user_id"])
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    instances = []
    registrations = []
    persisted = set()
    class FakeUploader(WikidataUploader):
        def ensure_authenticated(self):
            self._authenticated_user = "test-user"

        def register_test_ledger_target(self, item, ledger):
            from app.pipeline.wikidata_qid_ledger import ledger_key_for_item

            key = ledger_key_for_item(item, "wikidata-test")
            with closing(sqlite3.connect(db_session.bind.url.database)) as connection:
                rows = connection.execute(
                    "SELECT wikibase_id FROM wikibase_entity_mappings "
                    "WHERE ontology_uri = ? AND run_id IS NULL AND entity_kind = 'instance'",
                    (key,),
                ).fetchall()
            assert rows == [(ledger[key],)]
            registrations.append((item.local_id, ledger[key]))
            return super().register_test_ledger_target(item, ledger)

    def create_uploader(**kwargs):
        instance = FakeUploader(**kwargs)
        instances.append(instance)
        return instance

    async def fake_upload(items, **kwargs):
        item = items[0]
        links = [s for s in item.statements if s.property_id == "P50"]
        if links:
            statement = json.loads(json.dumps(asdict(links[0])))
            assert statement["value"] == {
                "id": "Q900", "value_namespace": "local", "value_local_id": "author",
            }
            assert kwargs["uploader"]._verified_test_targets["author"] == "Q900"
            return [upload.UploadOutcome("source", "Source", "work", "Q123", "updated",
                                         "linked", [], ownership="own")]
        assert item.existing_qid is None
        qid = "Q123" if item.local_id == "source" else "Q900"
        persisted.add(item.local_id)
        return [upload.UploadOutcome(item.local_id, item.local_id, item.entity_type, qid,
                                     "created", "created", [])]

    async def build(*args, **kwargs):
        return [source, author]

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr("app.routers.wikidata_studio._build_native_items", build)
    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", create_uploader)
    monkeypatch.setattr(
        "converter.wikidata.reconciler.prewarm_reconciler_for_items", lambda *a: None,
    )
    monkeypatch.setattr("app.pipeline.wikidata_existence.prefetch_entities_for_upload",
                        lambda *a, **kw: ({}, {}))
    monkeypatch.setattr("app.db.reset_engine", noop)
    monkeypatch.setattr(upload, "_make_reconciler", lambda: SimpleNamespace(_cache={}))
    monkeypatch.setattr(upload, "upload_items", fake_upload)
    monkeypatch.setattr(upload, "build_upload_corpus_quality_preflight", lambda items:
                        upload.UploadCorpusQualityPreflight(decisions={
                            item.local_id: upload.UploadQualityDecision(True) for item in items
                        }))

    if sharded:
        state = await job_module.run_wikidata_upload_shard(
            job.id, sample_run["run_id"], ["source", "author"],
        )
        job.params = {**job.params, "_step1_state": json.loads(json.dumps(state))}
        await db_session.commit()
    await job_module.run_wikidata_upload_job(job.id)
    await db_session.refresh(job)
    ledger = await load_global_ledger(db_session, is_test=True, reject_ambiguous=True)
    assert job.status == "succeeded"
    assert job.result["link_outcomes"][0]["status"] == "linked"
    assert ledger["wikidata-test:person:author"] == "Q900"
    assert ledger["wikidata-test:work:source:author:__LOCAL:author"] == "Q123"
    assert persisted == {"source", "author"}
    assert ("author", "Q900") in registrations
    assert all(instance._verified_test_targets.get("author") == "Q900" for instance in instances)


@pytest.mark.asyncio
async def test_test_ledger_collision_does_not_replace_persisted_mapping(db_session):
    import asyncio

    from app.models.wikibase_entity_mapping import WikibaseEntityMapping
    from app.pipeline.wikidata_qid_ledger import load_global_ledger
    from app.pipeline.wikidata_upload import UploadOutcome
    from app.pipeline.wikidata_upload_job import _persist_test_outcomes

    item = WikidataItem(local_id="author", entity_type="person")
    key = "wikidata-test:person:author"
    db_session.add(WikibaseEntityMapping(ontology_uri=key, entity_kind="instance",
                                        wikibase_id="Q900", label="Author"))
    await db_session.commit()
    outcome = UploadOutcome("author", "Author", "person", "Q901", "created", "ok", [])
    with pytest.raises(ValueError, match="Conflicting test-wiki ledger mapping"):
        await _persist_test_outcomes(item, [outcome], None, [item], {}, {}, asyncio.Lock(), [])
    ledger = await load_global_ledger(db_session, is_test=True, reject_ambiguous=True)
    assert ledger[key] == "Q900"
