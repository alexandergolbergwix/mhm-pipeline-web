from types import SimpleNamespace

import pytest

from app.pipeline import wikidata_upload as upload
from app.pipeline.wikidata_upload_job import _owner_targets
from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.uploader import WikidataUploader


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


def item(local_id="a", qid="Q123"):
    return WikidataItem(local_id=local_id, entity_type="manuscript", existing_qid=qid)


def record_item(cn, qid):
    return WikidataItem(
        local_id=f"QDraft_MS_{cn}",
        entity_type="manuscript",
        existing_qid=qid,
        records=[cn],
        statements=[WikidataStatement("P3959", cn, "external-id")],
    )


def test_selection_rejects_conflicts_duplicates_and_malformed_ids():
    items = [
        record_item("10001", "Q123"),
        record_item("10002", "Q123"),
        record_item("10003", "Q0"),
        record_item("10004", None),
        record_item("10005", "Q500"),
        record_item("10006", "Q600"),
    ]
    targets = _owner_targets(
        items,
        {"wikidata:marc:QDraft_MS_10005": "Q501", "wikidata:marc:QDraft_MS_10006": "Q600"},
        {"10001", "10002", "10003", "10004", "10005", "10006"},
    )
    assert dict(targets) == {"QDraft_MS_10006": "Q600"}
    with pytest.raises(TypeError):
        targets["f"] = "Q999"


def test_selection_uses_exact_ledger_and_duplicate_local_ids_block():
    targets = _owner_targets(
        [record_item("10001", None), record_item("10002", "Q2"), record_item("10002", "Q3")],
        {"wikidata:marc:QDraft_MS_10001": "Q1"},
        {"10001", "10002"},
    )
    assert dict(targets) == {"QDraft_MS_10001": "Q1"}


def test_selection_rejects_duplicate_in_a_conflicting_row():
    targets = _owner_targets(
        [record_item("10001", "Q123"), record_item("10002", "Q123")],
        {"wikidata:marc:QDraft_MS_10002": "Q124"},
        {"10001", "10002"},
    )
    assert not targets


def test_override_only_qid_is_not_a_selected_target():
    it = record_item("10001", "Q123")
    assert not _owner_targets([it], {}, {"10001"})
    assert dict(_owner_targets([it], {"wikidata:marc:QDraft_MS_10001": "Q123"}, {"10001"})) == {
        "QDraft_MS_10001": "Q123"
    }


def test_work_override_cannot_select_a_claim_derived_ledger_key():
    from converter.wikidata.item_models import WikidataStatement

    work = WikidataItem(
        local_id="work-a",
        entity_type="work",
        statements=[WikidataStatement("P2888", "https://example.invalid/work-b", "url")],
    )
    ledger = {"wikidata:work:p2888:https://example.invalid/work-b": "Q999"}
    assert not _owner_targets([work], ledger, set())
    work.statements = [WikidataStatement("P50", "Q999", "item")]
    assert not _owner_targets([work], {"wikidata:work:work-a:author:Q999": "Q999"}, set())


def test_owner_target_requires_matching_run_record_and_stable_manuscript_id():
    it = record_item("10001", "Q1")
    ledger = {"wikidata:marc:QDraft_MS_10001": "Q1"}
    assert not _owner_targets([it], ledger, {"10002"})
    it.records = ["10002"]
    assert not _owner_targets([it], ledger, {"10001", "10002"})
    it.records = ["10001", "10002"]
    assert not _owner_targets([it], ledger, {"10001", "10002"})
    person = WikidataItem(local_id="QDraft_Person_1", entity_type="person", existing_qid="Q1")
    assert not _owner_targets([person], {"wikidata:person:QDraft_Person_1": "Q1"}, {"10001"})


@pytest.mark.asyncio
async def test_owner_record_evidence_comes_from_selected_run(sample_run, db_session):
    import uuid

    from app.models.run import RunRecord
    from app.pipeline.wikidata_upload_job import _owner_run_record_ids

    db_session.add(RunRecord(run_id=sample_run["run_id"], control_number='"10001"', marc={}))
    await db_session.commit()
    assert "10001" in await _owner_run_record_ids(db_session, sample_run["run_id"])
    assert not await _owner_run_record_ids(db_session, uuid.uuid4())


@pytest.mark.parametrize(
    "pid,location",
    [
        ("P1574", "value"),
        ("P50", "value"),
        ("P31", "qualifier"),
        ("P31", "reference"),
    ],
)
def test_owner_uploader_blocks_original_local_claim_before_partition_or_write(
    pid, location, monkeypatch
):
    from converter.wikidata.item_models import WikidataStatement
    from converter.wikidata.uploader import partition_unresolved_local

    statement = WikidataStatement(pid, "Q7", "item")
    if location == "value":
        statement.value = "__LOCAL:missing"
    elif location == "qualifier":
        statement.qualifiers = [{"property": "P1", "value": "__LOCAL:missing"}]
    else:
        statement.references = [{"property": "P1", "value": "__LOCAL:missing"}]
    source = item()
    source.statements = [WikidataStatement("P31", "Q7", "item"), statement]
    partial, deferred = partition_unresolved_local(source, {})
    assert len(partial.statements) == 1 and len(deferred) == 1
    uploader = WikidataUploader(token="fixture", allow_live=True)
    builds = []
    monkeypatch.setattr(uploader, "_build_wbi_item", lambda *a, **kw: builds.append(a))
    result = uploader.upload_item(source, created_qids={"missing": "Q456"}, owner_only_qid="Q123")
    assert result.status == "blocked"
    assert not builds
    assert source.statements[1] is statement


@pytest.mark.asyncio
async def test_owner_audit_does_not_change_ledger_mapping(sample_run, db_session, monkeypatch):
    from sqlalchemy import select

    from app.models.wikibase_entity_mapping import WikibaseEntityMapping
    from app.services.wikibase_audit import WikibaseAuditContext

    mapping = WikibaseEntityMapping(
        ontology_uri="wikidata:work:p2888:other",
        entity_kind="instance",
        wikibase_id="Q999",
        label="Other",
    )
    db_session.add(mapping)
    await db_session.commit()
    work = WikidataItem(local_id="a", entity_type="work")
    outcome = upload.UploadOutcome("a", "A", "work", "Q123", "updated", "ok", [])
    ctx = WikibaseAuditContext(
        actor_user_id=sample_run["user_id"],
        channel="wikidata_upload",
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
    )
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(upload, "record_ledger_mapping", forbidden)
    await upload.persist_upload_outcomes(
        db_session, [work], [outcome], audit_ctx=ctx, record_ledger=False
    )
    await db_session.refresh(mapping)
    assert not calls and mapping.wikibase_id == "Q999"
    assert (await db_session.execute(select(WikibaseEntityMapping))).scalars().all() == [mapping]


@pytest.mark.asyncio
async def test_native_builder_does_not_adopt_foreign_override(monkeypatch):
    import uuid

    from app.models.item_override import WikidataItemOverride
    from app.routers import wikidata_studio

    override = WikidataItemOverride(
        local_id="a", accepted_foreign_qid="Q123", accept_foreign_modify=True
    )

    class DB:
        async def execute(self, query):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [override]))

    cache = SimpleNamespace(
        input_fingerprint="f" * 64, summary={},
        result_items=[
            {
                "local_id": "a",
                "entity_type": "manuscript",
                "labels": {"en": "a"},
                "statements": [],
            }
        ]
    )

    async def lookup(*args, **kwargs):
        return None

    async def cache_row(*args, **kwargs):
        return cache

    async def current(*args, **kwargs):
        return True

    monkeypatch.setattr(wikidata_studio, "_lookup_run_with_access", lookup)
    monkeypatch.setattr(wikidata_studio, "_get_studio_cache_row", cache_row)
    monkeypatch.setattr(wikidata_studio, "_studio_cache_is_current", current)
    monkeypatch.setattr(
        "app.pipeline.wikidata_canonical_enrichment.prepare_wikidata_upload_native_items",
        lambda items: items,
    )
    native = await wikidata_studio._build_native_items(
        DB(), uuid.uuid4(), None, approved_only=True, source="legacy", adopt_probe=False
    )
    assert len(native) == 1
    assert native[0].existing_qid is None


@pytest.mark.asyncio
async def test_conflicting_ledger_rows_do_not_choose_last_value():
    from app.pipeline.wikidata_qid_ledger import load_global_ledger

    class DB:
        async def execute(self, query):
            return SimpleNamespace(
                all=lambda: [
                    ("wikidata:marc:QDraft_MS_10001", "Q1"),
                    ("wikidata:marc:QDraft_MS_10001", "Q2"),
                    ("wikidata:marc:QDraft_MS_10001", "Q1"),
                ]
            )

    ledger = await load_global_ledger(DB(), is_test=False, reject_ambiguous=True)
    assert not _owner_targets([record_item("10001", "Q1")], ledger, {"10001"})


@pytest.mark.parametrize(
    "entity,page,author,expected",
    [
        ({"id": "Q123"}, {"title": "Q123"}, "Account", True),
        ({"id": "Q123"}, {"title": "Q123"}, "Other", False),
        ({"id": "Q124"}, {"title": "Q123"}, "Account", False),
        ({"id": "Q123", "missing": ""}, {"title": "Q123"}, "Account", False),
        ({"id": "Q123", "redirect": ""}, {"title": "Q123"}, "Account", False),
        ({"id": "Q123"}, {"title": "Q124"}, "Account", False),
        ({"id": "Q123"}, {"title": "Q123", "redirect": ""}, "Account", False),
        ({"id": "Q123"}, {"title": "Q123"}, None, False),
    ],
)
def test_remote_creator_and_exact_target(monkeypatch, entity, page, author, expected):
    uploader = WikidataUploader(token="fixture", allow_live=True)
    monkeypatch.setattr(uploader, "ensure_owner_only_authenticated", lambda: None)
    monkeypatch.setattr(uploader, "_query_userinfo_rights", lambda: {"name": "Account"})
    revision_response = SimpleNamespace(
        status_code=200,
        raise_for_status=lambda: None,
        json=lambda: {
            "query": {
                "pages": {"1": {"pageid": 1, "ns": 0, **page, "revisions": [{"user": author}]}}
            }
        },
    )
    entity_response = SimpleNamespace(
        status_code=200,
        raise_for_status=lambda: None,
        json=lambda: {
            "entities": {
                "Q123": {
                    "type": "item",
                    "lastrevid": 11,
                    "claims": {
                        "P3959": [
                            {
                                "rank": "normal",
                                "mainsnak": {
                                    "property": "P3959",
                                    "snaktype": "value",
                                    "datatype": "external-id",
                                    "datavalue": {"type": "string", "value": "10001"},
                                },
                            }
                        ]
                    },
                    **entity,
                }
            }
        },
    )
    session = SimpleNamespace(
        get=lambda *a, **kw: (
            entity_response if kw["params"]["action"] == "wbgetentities" else revision_response
        )
    )
    uploader._login = SimpleNamespace(get_session=lambda: session)
    assert uploader.owner_only_target_valid("Q123", "10001") is expected
    assert not uploader.owner_only_target_valid("Q01", "10001")


def test_uploader_rechecks_immediately_before_write_and_ignores_accept(monkeypatch):
    uploader = WikidataUploader(token="fixture", allow_live=True)
    monkeypatch.setattr(uploader, "owner_only_target_valid", lambda qid, cn: True)
    monkeypatch.setattr(uploader, "_owner_only_revision", lambda qid, cn: None)
    monkeypatch.setattr(uploader, "_init_wbi", lambda: None)
    monkeypatch.setattr(uploader, "_is_our_item", lambda *a, **kw: True)
    monkeypatch.setattr(uploader, "_bot_excluded", lambda qid: False)
    monkeypatch.setattr(uploader, "_rate_limit", lambda: None)
    writes = []
    fake = SimpleNamespace(id="Q123", write=lambda **kw: writes.append(kw))
    monkeypatch.setattr(uploader, "_build_wbi_item", lambda it, **kwargs: (fake, 1, []))
    uploader.register_foreign_accept("Q123")
    assert (
        uploader.upload_item(record_item("10001", "Q123"), owner_only_qid="Q123").status
        == "blocked"
    )
    assert not writes
    assert not uploader._foreign_accept_qids


def test_uploader_rejects_create_and_target_shift(monkeypatch):
    uploader = WikidataUploader(token="fixture", allow_live=True)
    monkeypatch.setattr(uploader, "owner_only_target_valid", lambda qid, cn: True)
    assert uploader.upload_item(item(qid=None), owner_only_qid="Q123").status == "blocked"
    assert uploader.upload_item(item(qid="Q124"), owner_only_qid="Q123").status == "blocked"


def test_owned_update_passes_both_guards_before_one_write(monkeypatch):
    uploader = WikidataUploader(token="fixture", allow_live=True)
    checks = []
    monkeypatch.setattr(
        uploader, "owner_only_target_valid", lambda qid, cn: checks.append(qid) or True
    )
    monkeypatch.setattr(
        uploader, "_owner_only_revision", lambda qid, cn: checks.append(qid) or 12345
    )
    monkeypatch.setattr(uploader, "_init_wbi", lambda: None)
    monkeypatch.setattr(uploader, "_is_our_item", lambda *a, **kw: True)
    monkeypatch.setattr(uploader, "_bot_excluded", lambda qid: False)
    monkeypatch.setattr(uploader, "_rate_limit", lambda: None)
    writes = []
    fake = SimpleNamespace(id="Q123", lastrevid=12345)
    fake.write = lambda **kw: writes.append(kw) or fake
    monkeypatch.setattr(uploader, "_build_wbi_item", lambda it, **kwargs: (fake, 1, []))
    assert (
        uploader.upload_item(record_item("10001", "Q123"), owner_only_qid="Q123").status
        == "updated"
    )
    assert checks == ["Q123", "Q123"]
    assert len(writes) == 1
    assert writes[0]["baserevid"] == 12345
    assert writes[0]["mediawiki_api_url"] == "https://www.wikidata.org/w/api.php"
    assert writes[0]["allow_redirects"] is False


def test_foreign_approval_never_overrides_owner_guard(monkeypatch):
    uploader = WikidataUploader(token="fixture", allow_live=True)
    monkeypatch.setattr(uploader, "owner_only_target_valid", lambda qid, cn: False)
    uploader.register_foreign_accept("Q123")
    assert (
        uploader.upload_item(record_item("10001", "Q123"), owner_only_qid="Q123").status
        == "blocked"
    )
    assert not uploader._foreign_accept_qids


@pytest.mark.parametrize("dry_run", [True, False])
def test_owner_mode_blocks_post_preparation_target_shift(monkeypatch, dry_run):
    candidate = record_item("10001", "Q123")
    uploader = SimpleNamespace(
        owner_only_target_valid=lambda qid, cn: True, _foreign_accept_qids={"Q123"}
    )
    row = upload.PreparedItem(
        item=item(qid="Q124"),
        local_id="a",
        label="",
        entity_type="manuscript",
        existing_qid="Q124",
        method="prebuilt",
        blocked=False,
        block_status="",
        block_message="",
        ownership="own",
    )
    monkeypatch.setattr(upload, "_make_reconciler", lambda: object())
    monkeypatch.setattr(upload, "_prepare_for_upload", lambda *a, **kw: [row])
    result = upload._owner_only_upload_sync(
        [candidate], {candidate.local_id: "Q123"}, uploader, dry_run, False, {}, None
    )
    assert result[0].status == "blocked"
    assert not uploader._foreign_accept_qids


@pytest.mark.parametrize("dry_run", [True, False])
def test_owner_mode_no_target_never_creates(dry_run):
    result = upload._owner_only_upload_sync([item(qid=None)], {}, None, dry_run, False, {}, None)
    assert result[0].status == "blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["false", "true", 1, 0, None, [], {}])
async def test_job_rejects_non_boolean_owner_only(value):
    import uuid

    from fastapi import HTTPException

    from app.pipeline.run_job_params import prepare_job_params

    with pytest.raises(HTTPException, match="owner_only"):
        await prepare_job_params(
            None, None, run_id=uuid.uuid4(), kind="wikidata_upload", params={"owner_only": value}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["invalid", "", None, [], 1, "CANONICAL"])
async def test_upload_rejects_invalid_source_before_credentials(source):
    import uuid

    from fastapi import HTTPException

    from app.pipeline.run_job_params import prepare_job_params

    with pytest.raises(HTTPException, match="source"):
        await prepare_job_params(
            None,
            None,
            run_id=uuid.uuid4(),
            kind="wikidata_upload",
            params={"owner_only": True, "source": source},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["dry_run", "update_existing", "approved_only", "item_approved_only", "ai_verified_only"],
)
@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
async def test_owner_job_rejects_coerced_booleans(field, value):
    import uuid

    from fastapi import HTTPException

    from app.pipeline.run_job_params import prepare_job_params

    with pytest.raises(HTTPException, match=field):
        await prepare_job_params(
            None,
            None,
            run_id=uuid.uuid4(),
            kind="wikidata_upload",
            params={"owner_only": True, field: value},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [None, [], "a", [""], ["  "], ["a", None], [123]])
async def test_owner_job_rejects_invalid_scope(ids):
    import uuid

    from fastapi import HTTPException

    from app.pipeline.run_job_params import prepare_job_params

    with pytest.raises(HTTPException, match="local_ids"):
        await prepare_job_params(
            None,
            None,
            run_id=uuid.uuid4(),
            kind="wikidata_upload",
            params={"owner_only": True, "local_ids": ids},
        )


@pytest.mark.asyncio
async def test_owner_only_uses_live_credential_for_dry_run(monkeypatch):
    import uuid

    from fastapi import HTTPException

    from app.pipeline.run_job_params import prepare_job_params

    calls = []

    async def secret(db, auth, name):
        calls.append(name)
        return None

    monkeypatch.setattr("app.routers.wikidata_studio._unwrap_user_secret", secret)
    with pytest.raises(HTTPException):
        await prepare_job_params(
            None,
            None,
            run_id=uuid.uuid4(),
            kind="wikidata_upload",
            params={"owner_only": True, "upload_target": "dry_run"},
        )
    assert calls == ["wikidata"]


@pytest.mark.asyncio
async def test_owner_only_shard_rejects_dispatch(sample_run, db_session):
    from app.models.run_job import RunJob
    from app.pipeline.wikidata_upload_job import run_wikidata_upload_shard

    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind="wikidata_upload",
        status="running",
        params={"owner_only": True},
        progress={},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()
    with pytest.raises(ValueError, match="cannot run in a shard"):
        await run_wikidata_upload_shard(job.id, sample_run["run_id"], ["a"])


@pytest.mark.asyncio
async def test_owner_only_ledger_failure_stops_before_write(sample_run, db_session, monkeypatch):
    from app.models.run_job import RunJob
    from app.pipeline.wikidata_upload_job import run_wikidata_upload_job

    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind="wikidata_upload",
        status="running",
        params={"owner_only": True, "upload_target": "live", "_wikidata_token": "fixture"},
        progress={},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()

    async def build(*args, **kwargs):
        return [item()]

    async def fail(*args, **kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr("app.routers.wikidata_studio._build_native_items", build)
    monkeypatch.setattr("app.pipeline.wikidata_qid_ledger.load_global_ledger", fail)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        await run_wikidata_upload_job(job.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
async def test_owner_only_blocks_local_links_before_any_pass(
    sample_run, db_session, monkeypatch, dry_run
):
    from app.models.run_job import RunJob
    from app.pipeline import wikidata_upload_job as job_module
    from converter.wikidata.item_models import WikidataStatement

    source = item("source", "Q123")
    source.statements = [WikidataStatement("P50", "__LOCAL:target", "item")]
    target = item("target", "Q456")
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind="wikidata_upload",
        status="running",
        progress={},
        params={
            "owner_only": True,
            "upload_target": "dry_run" if dry_run else "live",
            "_wikidata_token": "fixture",
            "local_ids": ["source"],
        },
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()
    writes = []

    class FakeUploader:
        def __init__(self, **kwargs):
            self._foreign_accept_qids = set()

        def owner_only_target_valid(self, qid, cn):
            return qid == "Q123"

        def ensure_owner_only_authenticated(self):
            return None

        def upload_item(self, it, *, created_qids, owner_only_qid):
            writes.append((it.existing_qid, owner_only_qid))
            return SimpleNamespace(
                qid=it.existing_qid, status="updated", message="ok", added_properties=[]
            )

    async def build(*args, **kwargs):
        assert kwargs["adopt_probe"] is False
        return [source, target]

    async def persist(*args, **kwargs):
        return None

    async def ledger(*args, **kwargs):
        return {"wikidata:marc:source": "Q123", "wikidata:marc:target": "Q456"}

    def prepare(items, *args, **kwargs):
        candidate = items[0]
        return [
            upload.PreparedItem(
                item=candidate,
                local_id="source",
                label="Source",
                entity_type="manuscript",
                existing_qid="Q123",
                method="prebuilt",
                blocked=False,
                block_status="",
                block_message="",
                ownership="own",
            )
        ]

    monkeypatch.setattr("app.routers.wikidata_studio._build_native_items", build)
    monkeypatch.setattr("app.pipeline.wikidata_qid_ledger.load_global_ledger", ledger)
    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", FakeUploader)
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.prefetch_entities_for_upload", lambda *a, **kw: ({}, {})
    )
    monkeypatch.setattr(upload, "_make_reconciler", lambda: SimpleNamespace(_cache={}))
    monkeypatch.setattr(upload, "_prepare_for_upload", prepare)
    monkeypatch.setattr(upload, "persist_upload_outcomes", persist)
    monkeypatch.setattr(
        upload,
        "build_upload_corpus_quality_preflight",
        lambda items: upload.UploadCorpusQualityPreflight(
            decisions={it.local_id: upload.UploadQualityDecision(eligible=True) for it in items}
        ),
    )
    await job_module.run_wikidata_upload_job(job.id)
    await db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.result["link_outcomes"] == []
    assert job.result["outcomes"][0]["status"] == "blocked"
    assert writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,verified,update_existing,expected",
    [
        (["QDraft_MS_10001", "QDraft_MS_10002"], True, True, ["QDraft_MS_10001"]),
        (["QDraft_MS_10002"], True, True, []),
        (["QDraft_MS_10001"], False, False, []),
        (["QDraft_MS_10002"], False, True, ["QDraft_MS_10002"]),
    ],
)
async def test_owner_scope_filters_are_conjunctive(
    sample_run,
    db_session,
    monkeypatch,
    scope,
    verified,
    update_existing,
    expected,
):
    from app.models.item_override import WikidataItemOverride
    from app.models.run_job import RunJob
    from app.pipeline import wikidata_upload_job

    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind="wikidata_upload",
        status="running",
        progress={},
        created_by=sample_run["user_id"],
        params={
            "owner_only": True,
            "upload_target": "dry_run",
            "_wikidata_token": "fixture",
            "local_ids": scope,
            "ai_verified_only": verified,
            "update_existing": update_existing,
        },
    )
    db_session.add_all(
        [
            job,
            WikidataItemOverride(
                run_id=sample_run["run_id"],
                local_id="QDraft_MS_10001",
                ai_verdict={"overall": "full"},
            ),
        ]
    )
    await db_session.commit()

    class FakeUploader:
        def __init__(self, **kwargs):
            pass

        def ensure_owner_only_authenticated(self):
            pass

    async def build(*args, **kwargs):
        return [record_item("10001", "Q1"), record_item("10002", "Q2")]

    async def ledger(*args, **kwargs):
        return {"wikidata:marc:QDraft_MS_10001": "Q1", "wikidata:marc:QDraft_MS_10002": "Q2"}

    async def record_ids(*args, **kwargs):
        return {"10001", "10002"}

    calls = []

    async def write(items, **kwargs):
        calls.append(items[0].local_id)
        return [
            upload.UploadOutcome(
                items[0].local_id,
                "",
                "manuscript",
                items[0].existing_qid,
                "would_update",
                "ok",
                [],
                ownership="own",
            )
        ]

    monkeypatch.setattr("app.routers.wikidata_studio._build_native_items", build)
    monkeypatch.setattr("app.pipeline.wikidata_qid_ledger.load_global_ledger", ledger)
    monkeypatch.setattr(wikidata_upload_job, "_owner_run_record_ids", record_ids)
    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", FakeUploader)
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.prefetch_entities_for_upload", lambda *a, **kw: ({}, {})
    )
    monkeypatch.setattr(upload, "_make_reconciler", lambda: SimpleNamespace(_cache={}))
    monkeypatch.setattr(upload, "upload_items", write)
    monkeypatch.setattr(
        upload,
        "build_upload_corpus_quality_preflight",
        lambda items: upload.UploadCorpusQualityPreflight(
            decisions={it.local_id: upload.UploadQualityDecision(eligible=True) for it in items}
        ),
    )
    await wikidata_upload_job.run_wikidata_upload_job(job.id)
    await db_session.refresh(job)
    assert job.status == "succeeded"
    assert calls == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_owner_live_job_audits_without_updating_ledger(
    sample_run, db_session, monkeypatch, audit_fails
):
    from app.models.run_job import RunJob
    from app.pipeline import wikidata_upload_job
    from converter.wikidata.item_models import WikidataStatement

    source = record_item("10001", "Q123")
    source.statements = [WikidataStatement("P2888", "https://example.invalid/original", "url")]
    second = record_item("10002", "Q456")
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind="wikidata_upload",
        status="running",
        progress={},
        created_by=sample_run["user_id"],
        params={"owner_only": True, "upload_target": "live", "_wikidata_token": "fixture"},
    )
    db_session.add(job)
    await db_session.commit()

    class FakeUploader:
        def __init__(self, **kwargs):
            pass

        def ensure_owner_only_authenticated(self):
            pass

    async def build(*args, **kwargs):
        return [source, second] if audit_fails else [source]

    async def ledger(*args, **kwargs):
        return {"wikidata:marc:QDraft_MS_10001": "Q123", "wikidata:marc:QDraft_MS_10002": "Q456"}

    async def record_ids(*args, **kwargs):
        return {"10001", "10002"}

    writes = []

    async def write(items, **kwargs):
        writes.append(items[0].local_id)
        items[0].statements[0].value = "https://example.invalid/other"
        return [
            upload.UploadOutcome(
                items[0].local_id, "", "manuscript", "Q123", "updated", "ok", [], ownership="own"
            )
        ]

    audits = []

    async def audit(*args, **kwargs):
        audits.append(args)
        if audit_fails:
            raise RuntimeError("audit database unavailable")

    ledger_writes = []

    async def update_ledger(*args, **kwargs):
        ledger_writes.append(args)

    monkeypatch.setattr("app.routers.wikidata_studio._build_native_items", build)
    monkeypatch.setattr("app.pipeline.wikidata_qid_ledger.load_global_ledger", ledger)
    monkeypatch.setattr(wikidata_upload_job, "_owner_run_record_ids", record_ids)
    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", FakeUploader)
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.prefetch_entities_for_upload", lambda *a, **kw: ({}, {})
    )
    monkeypatch.setattr(upload, "_make_reconciler", lambda: SimpleNamespace(_cache={}))
    monkeypatch.setattr(upload, "upload_items", write)
    monkeypatch.setattr(upload, "_record_outcome_audit", audit)
    monkeypatch.setattr(upload, "record_ledger_mapping", update_ledger)
    monkeypatch.setattr(
        upload,
        "build_upload_corpus_quality_preflight",
        lambda items: upload.UploadCorpusQualityPreflight(
            decisions={it.local_id: upload.UploadQualityDecision(eligible=True) for it in items}
        ),
    )
    await wikidata_upload_job.run_wikidata_upload_job(job.id)
    await db_session.refresh(job)
    assert job.status == ("failed" if audit_fails else "succeeded")
    assert len(audits) == 1 and not ledger_writes
    if audit_fails:
        assert writes == ["QDraft_MS_10001"]
        assert job.result["aborted_audit"] is True
        assert job.result["outcomes"][0]["status"] == "updated"
        assert job.result["outcomes"][0]["audit_status"] == "failed"
        assert "verify" in job.error.lower()


@pytest.mark.asyncio
async def test_owner_preview_authenticates_even_when_scope_is_empty(
    sample_run, db_session, monkeypatch
):
    from app.models.run_job import RunJob
    from app.pipeline import wikidata_upload_job

    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind="wikidata_upload",
        status="running",
        progress={},
        params={"owner_only": True, "upload_target": "dry_run", "_wikidata_token": "fixture"},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()

    async def build(*args, **kwargs):
        return []

    class BadUploader:
        def __init__(self, **kwargs):
            pass

        def ensure_owner_only_authenticated(self):
            raise RuntimeError("authentication failed")

    monkeypatch.setattr("app.routers.wikidata_studio._build_native_items", build)
    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", BadUploader)
    await wikidata_upload_job.run_wikidata_upload_job(job.id)
    await db_session.refresh(job)
    assert job.status == "failed"
    assert "authentication failed" in job.error


def test_owner_revision_failures_never_validate(monkeypatch):
    uploader = WikidataUploader(token="fixture", allow_live=True)
    monkeypatch.setattr(
        uploader,
        "ensure_owner_only_authenticated",
        lambda: (_ for _ in ()).throw(RuntimeError("failed")),
    )
    assert not uploader.owner_only_target_valid("Q123", "10001")
    monkeypatch.setattr(uploader, "_owner_only_revision", lambda qid, cn: False)
    assert not uploader.owner_only_target_valid("Q123", "10001")


def test_owner_write_rejects_revision_mismatch(monkeypatch):
    uploader = WikidataUploader(token="fixture", allow_live=True)
    monkeypatch.setattr(uploader, "owner_only_target_valid", lambda qid, cn: True)
    monkeypatch.setattr(uploader, "_owner_only_revision", lambda qid, cn: 42)
    monkeypatch.setattr(uploader, "_init_wbi", lambda: None)
    monkeypatch.setattr(uploader, "_is_our_item", lambda *a, **kw: True)
    monkeypatch.setattr(uploader, "_bot_excluded", lambda qid: False)
    monkeypatch.setattr(uploader, "_rate_limit", lambda: None)
    writes = []
    fake = SimpleNamespace(id="Q123", lastrevid=41, write=lambda **kw: writes.append(kw))
    monkeypatch.setattr(uploader, "_build_wbi_item", lambda it, **kwargs: (fake, 1, []))
    assert (
        uploader.upload_item(record_item("10001", "Q123"), owner_only_qid="Q123").status
        == "blocked"
    )
    assert not writes


def test_owner_write_conflict_does_not_retry(monkeypatch):
    from wikibaseintegrator.wbi_exceptions import MWApiError

    uploader = WikidataUploader(token="fixture", allow_live=True)
    monkeypatch.setattr(uploader, "owner_only_target_valid", lambda qid, cn: True)
    monkeypatch.setattr(uploader, "_owner_only_revision", lambda qid, cn: 42)
    monkeypatch.setattr(uploader, "_init_wbi", lambda: None)
    monkeypatch.setattr(uploader, "_is_our_item", lambda *a, **kw: True)
    monkeypatch.setattr(uploader, "_bot_excluded", lambda qid: False)
    monkeypatch.setattr(uploader, "_rate_limit", lambda: None)
    writes = []

    def write(**kwargs):
        writes.append(kwargs)
        raise MWApiError({"code": "editconflict", "info": "changed"})

    fake = SimpleNamespace(id="Q123", lastrevid=42, write=write)
    monkeypatch.setattr(uploader, "_build_wbi_item", lambda it, **kwargs: (fake, 1, []))
    assert (
        uploader.upload_item(record_item("10001", "Q123"), owner_only_qid="Q123").status
        == "blocked"
    )
    assert len(writes) == 1
    assert writes[0]["baserevid"] == 42


def _remote_statement(value="10001", **overrides):
    return {
        "rank": "normal",
        "mainsnak": {
            "property": "P3959",
            "snaktype": "value",
            "datatype": "external-id",
            "datavalue": {"type": "string", "value": value},
            **overrides,
        },
    }


def _real_owner_uploader(monkeypatch, entity_responses, *, creator="Account"):
    uploader = WikidataUploader(token="fixture", allow_live=True)
    calls = []
    writes = []
    monkeypatch.setattr(uploader, "ensure_owner_only_authenticated", lambda: None)
    monkeypatch.setattr(uploader, "_query_userinfo_rights", lambda: {"name": "Account"})
    monkeypatch.setattr(uploader, "_init_wbi", lambda: None)
    monkeypatch.setattr(uploader, "_is_our_item", lambda *a, **kw: True)
    monkeypatch.setattr(uploader, "_bot_excluded", lambda qid: False)
    monkeypatch.setattr(uploader, "_rate_limit", lambda: None)

    def get(url, **kwargs):
        calls.append((url, kwargs))
        if kwargs["params"]["action"] == "wbgetentities":
            entity = entity_responses[
                min(
                    sum(1 for _, kw in calls if kw["params"]["action"] == "wbgetentities") - 1,
                    len(entity_responses) - 1,
                )
            ]
            body = {"entities": {"Q123": {"id": "Q123", "type": "item", "lastrevid": 42, **entity}}}
        else:
            body = {
                "query": {
                    "pages": {
                        "1": {
                            "pageid": 1,
                            "ns": 0,
                            "title": "Q123",
                            "revisions": [{"user": creator}],
                        }
                    }
                }
            }
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: body)

    uploader._login = SimpleNamespace(get_session=lambda: SimpleNamespace(get=get))
    fake = SimpleNamespace(id="Q123", lastrevid=42)
    fake.write = lambda **kw: writes.append(kw) or fake
    monkeypatch.setattr(uploader, "_build_wbi_item", lambda it, **kwargs: (fake, 1, []))
    return uploader, calls, writes


@pytest.mark.parametrize("rank", ["normal", "preferred", "deprecated", "", None, 0, [], "NORMAL"])
@pytest.mark.parametrize("dry_run", [True, False])
def test_local_p3959_rank_is_checked_in_preview_and_write(monkeypatch, rank, dry_run):
    uploader, _, writes = _real_owner_uploader(
        monkeypatch, [{"claims": {"P3959": [_remote_statement()]}}]
    )
    source = record_item("10001", "Q123")
    source.statements[0].rank = rank

    def prepare(items, *args, **kwargs):
        it = items[0]
        return [
            upload.PreparedItem(
                item=it,
                local_id=it.local_id,
                label="",
                entity_type="manuscript",
                existing_qid="Q123",
                method="prebuilt",
                blocked=False,
                block_status="",
                block_message="",
                ownership="own",
            )
        ]

    monkeypatch.setattr(upload, "_make_reconciler", lambda: object())
    monkeypatch.setattr(upload, "_prepare_for_upload", prepare)
    outcomes = upload._owner_only_upload_sync(
        [source], {source.local_id: "Q123"}, uploader, dry_run, False, {}, None
    )
    allowed = rank in ("normal", "preferred")
    assert outcomes[0].status == (
        "would_update" if allowed and dry_run else "updated" if allowed else "blocked"
    )
    assert len(writes) == (1 if allowed and not dry_run else 0)


@pytest.mark.parametrize(
    "rank", ["normal", "preferred", "deprecated", "", None, 0, [], "NORMAL", "missing"]
)
@pytest.mark.parametrize("dry_run", [True, False])
def test_remote_p3959_rank_is_checked_in_preview_and_write(monkeypatch, rank, dry_run):
    statement = _remote_statement()
    if rank == "missing":
        del statement["rank"]
    else:
        statement["rank"] = rank
    uploader, _, writes = _real_owner_uploader(monkeypatch, [{"claims": {"P3959": [statement]}}])
    source = record_item("10001", "Q123")

    def prepare(items, *args, **kwargs):
        it = items[0]
        return [
            upload.PreparedItem(
                item=it,
                local_id=it.local_id,
                label="",
                entity_type="manuscript",
                existing_qid="Q123",
                method="prebuilt",
                blocked=False,
                block_status="",
                block_message="",
                ownership="own",
            )
        ]

    monkeypatch.setattr(upload, "_make_reconciler", lambda: object())
    monkeypatch.setattr(upload, "_prepare_for_upload", prepare)
    outcomes = upload._owner_only_upload_sync(
        [source], {source.local_id: "Q123"}, uploader, dry_run, False, {}, None
    )
    allowed = rank in ("normal", "preferred")
    assert outcomes[0].status == (
        "would_update" if allowed and dry_run else "updated" if allowed else "blocked"
    )
    assert len(writes) == (1 if allowed and not dry_run else 0)


@pytest.mark.parametrize("rank", ["deprecated", "", None, 0, [], "missing"])
def test_owner_native_conversion_does_not_normalize_invalid_p3959_rank(rank):
    from app.routers.wikidata_studio import studio_dict_to_native_item
    from converter.wikidata.uploader import owner_only_control_number

    statement = {"property_id": "P3959", "value": "10001", "value_type": "external-id"}
    if rank != "missing":
        statement["rank"] = rank
    raw = {
        "local_id": "QDraft_MS_10001",
        "entity_type": "manuscript",
        "records": ["10001"],
        "statements": [statement],
    }
    assert (
        owner_only_control_number(studio_dict_to_native_item(raw, strict_p3959_rank=True)) is None
    )


@pytest.mark.parametrize("rank", ["normal", "preferred"])
def test_owner_native_conversion_accepts_valid_p3959_rank(rank):
    from app.routers.wikidata_studio import studio_dict_to_native_item
    from converter.wikidata.uploader import owner_only_control_number

    raw = {
        "local_id": "QDraft_MS_10001",
        "entity_type": "manuscript",
        "records": ["10001"],
        "statements": [
            {"property_id": "P3959", "value": "10001", "value_type": "external-id", "rank": rank}
        ],
    }
    assert (
        owner_only_control_number(studio_dict_to_native_item(raw, strict_p3959_rank=True))
        == "10001"
    )


def test_rank_changes_before_write_block_the_real_uploader(monkeypatch):
    remote = _remote_statement()
    deprecated = {**remote, "rank": "deprecated"}
    uploader, _, writes = _real_owner_uploader(
        monkeypatch, [{"claims": {"P3959": [remote]}}, {"claims": {"P3959": [deprecated]}}]
    )
    assert (
        uploader.upload_item(record_item("10001", "Q123"), owner_only_qid="Q123").status
        == "blocked"
    )
    assert not writes


def test_local_rank_change_during_build_blocks_the_real_uploader(monkeypatch):
    uploader, _, writes = _real_owner_uploader(
        monkeypatch, [{"claims": {"P3959": [_remote_statement()]}}]
    )
    fake = SimpleNamespace(id="Q123", lastrevid=42, write=lambda **kwargs: writes.append(kwargs))

    def build(it, **kwargs):
        it.statements[0].rank = "deprecated"
        return fake, 1, []

    monkeypatch.setattr(uploader, "_build_wbi_item", build)
    assert (
        uploader.upload_item(record_item("10001", "Q123"), owner_only_qid="Q123").status
        == "blocked"
    )
    assert not writes


@pytest.mark.asyncio
async def test_owner_job_blocks_normalized_malformed_override_rank(
    sample_run, db_session, monkeypatch
):
    from app.models.item_override import WikidataItemOverride
    from app.models.run_job import RunJob
    from app.pipeline import wikidata_upload_job

    source = record_item("10001", "Q123")
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind="wikidata_upload",
        status="running",
        progress={},
        created_by=sample_run["user_id"],
        params={"owner_only": True, "upload_target": "dry_run", "_wikidata_token": "fixture"},
    )
    override = WikidataItemOverride(
        run_id=sample_run["run_id"],
        local_id=source.local_id,
        add_statements=[{"property_id": "P3959", "value": "10001", "rank": None}],
    )
    db_session.add_all([job, override])
    await db_session.commit()

    class FakeUploader:
        def __init__(self, **kwargs):
            pass

        def ensure_owner_only_authenticated(self):
            pass

    async def build(*args, **kwargs):
        return [source]

    async def ledger(*args, **kwargs):
        return {"wikidata:marc:QDraft_MS_10001": "Q123"}

    async def record_ids(*args, **kwargs):
        return {"10001"}

    monkeypatch.setattr("app.routers.wikidata_studio._build_native_items", build)
    monkeypatch.setattr("app.pipeline.wikidata_qid_ledger.load_global_ledger", ledger)
    monkeypatch.setattr(wikidata_upload_job, "_owner_run_record_ids", record_ids)
    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", FakeUploader)
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.prefetch_entities_for_upload", lambda *a, **kw: ({}, {})
    )
    monkeypatch.setattr(upload, "_make_reconciler", lambda: SimpleNamespace(_cache={}))
    monkeypatch.setattr(
        upload,
        "build_upload_corpus_quality_preflight",
        lambda items: upload.UploadCorpusQualityPreflight(
            decisions={it.local_id: upload.UploadQualityDecision(eligible=True) for it in items}
        ),
    )
    await wikidata_upload_job.run_wikidata_upload_job(job.id)
    await db_session.refresh(job)
    assert job.result["outcomes"][0]["status"] == "blocked"


@pytest.mark.parametrize(
    "remote",
    [
        {},
        {"P3959": []},
        {"P3959": [_remote_statement("10002")]},
        {"P3959": [_remote_statement(), _remote_statement()]},
        {"P3959": [_remote_statement(snaktype="somevalue")]},
        {"P3959": [_remote_statement(datatype="string")]},
        {"P3959": [_remote_statement(datavalue={"type": "string", "value": 10001})]},
        {"P3959": [None]},
    ],
)
@pytest.mark.parametrize("dry_run", [True, False])
def test_stale_ledger_remote_identity_blocks_real_uploader(monkeypatch, remote, dry_run):
    uploader, _, writes = _real_owner_uploader(monkeypatch, [{"claims": remote}])
    source = record_item("10001", "Q123")
    outcomes = upload._owner_only_upload_sync(
        [source], {source.local_id: "Q123"}, uploader, dry_run, False, {}, None
    )
    assert outcomes[0].status == "blocked"
    assert not writes


@pytest.mark.parametrize(
    "local_claims",
    [
        [],
        [WikidataStatement("P3959", "10002", "external-id")],
        [WikidataStatement("P3959", "10001", "string")],
        [
            WikidataStatement("P3959", "10001", "external-id"),
            WikidataStatement("P3959", "10001", "external-id"),
        ],
        [WikidataStatement("P3959", None, "external-id")],
    ],
)
@pytest.mark.parametrize("dry_run", [True, False])
def test_edited_local_p3959_blocks_real_uploader(monkeypatch, local_claims, dry_run):
    uploader, calls, writes = _real_owner_uploader(
        monkeypatch, [{"claims": {"P3959": [_remote_statement()]}}]
    )
    source = record_item("10001", "Q123")
    source.statements = local_claims
    outcomes = upload._owner_only_upload_sync(
        [source], {source.local_id: "Q123"}, uploader, dry_run, False, {}, None
    )
    assert outcomes[0].status == "blocked"
    assert not calls and not writes
    assert uploader.upload_item(source, owner_only_qid="Q123").status == "blocked"
    assert not calls and not writes


def test_remote_p3959_change_before_write_blocks_real_uploader(monkeypatch):
    uploader, _, writes = _real_owner_uploader(
        monkeypatch,
        [
            {"claims": {"P3959": [_remote_statement()]}},
            {"claims": {"P3959": [_remote_statement("10002")]}},
        ],
    )
    result = uploader.upload_item(record_item("10001", "Q123"), owner_only_qid="Q123")
    assert result.status == "blocked"
    assert not writes


def test_local_p3959_change_during_build_blocks_real_uploader(monkeypatch):
    uploader, _, writes = _real_owner_uploader(
        monkeypatch, [{"claims": {"P3959": [_remote_statement()]}}]
    )
    fake = SimpleNamespace(id="Q123", lastrevid=42, write=lambda **kwargs: writes.append(kwargs))

    def build(it, **kwargs):
        it.statements[0].value = "10002"
        return fake, 1, []

    monkeypatch.setattr(uploader, "_build_wbi_item", build)
    result = uploader.upload_item(record_item("10001", "Q123"), owner_only_qid="Q123")
    assert result.status == "blocked"
    assert not writes


@pytest.mark.parametrize("changed", [False, True])
def test_preview_rechecks_remote_p3959_after_preparation(monkeypatch, changed):
    entities = [{"claims": {"P3959": [_remote_statement()]}}]
    if changed:
        entities.append({"claims": {"P3959": [_remote_statement("10002")]}})
    uploader, _, writes = _real_owner_uploader(monkeypatch, entities)
    source = record_item("10001", "Q123")

    def prepare(items, *args, **kwargs):
        it = items[0]
        return [
            upload.PreparedItem(
                item=it,
                local_id=it.local_id,
                label="",
                entity_type="manuscript",
                existing_qid="Q123",
                method="prebuilt",
                blocked=False,
                block_status="",
                block_message="",
                ownership="own",
            )
        ]

    monkeypatch.setattr(upload, "_make_reconciler", lambda: object())
    monkeypatch.setattr(upload, "_prepare_for_upload", prepare)
    outcomes = upload._owner_only_upload_sync(
        [source], {source.local_id: "Q123"}, uploader, True, False, {}, None
    )
    assert outcomes[0].status == ("blocked" if changed else "would_update")
    assert not writes


@pytest.mark.asyncio
async def test_strict_audit_failure_propagates_after_rollback():
    import uuid

    from app.services.wikibase_audit import WikibaseAuditContext, record_wikibase_write

    class BrokenDB:
        rolled_back = False

        def add(self, row):
            pass

        async def commit(self):
            raise RuntimeError("database unavailable")

        async def rollback(self):
            self.rolled_back = True

    db = BrokenDB()
    ctx = WikibaseAuditContext(actor_user_id=uuid.uuid4(), channel="wikidata_upload")
    with pytest.raises(RuntimeError, match="database unavailable"):
        await record_wikibase_write(
            db,
            ctx,
            operation="update",
            target_kind="item",
            target_key="record",
            raise_on_error=True,
        )
    assert db.rolled_back


def test_real_uploader_writes_only_after_remote_identity_and_creator_agree(monkeypatch):
    uploader, calls, writes = _real_owner_uploader(
        monkeypatch, [{"claims": {"P3959": [_remote_statement()]}}]
    )
    result = uploader.upload_item(record_item("10001", "Q123"), owner_only_qid="Q123")
    assert result.status == "updated"
    assert len(writes) == 1 and writes[0]["baserevid"] == 42
    assert sum(1 for _, kw in calls if kw["params"]["action"] == "wbgetentities") == 2
    assert all(url == "https://www.wikidata.org/w/api.php" for url, _ in calls)


def test_real_uploader_rejects_foreign_creator_even_with_matching_p3959(monkeypatch):
    uploader, _, writes = _real_owner_uploader(
        monkeypatch, [{"claims": {"P3959": [_remote_statement()]}}], creator="Other"
    )
    assert (
        uploader.upload_item(record_item("10001", "Q123"), owner_only_qid="Q123").status
        == "blocked"
    )
    assert not writes


@pytest.mark.asyncio
async def test_owner_upload_query_rejects_coerced_boolean(sample_run):
    response = await sample_run["client"].post(
        f"/api/runs/{sample_run['run_id']}/wikidata-studio/upload?owner_only=true&dry_run=no"
    )
    assert response.status_code == 400


def test_owner_time_claim_ignores_global_calendar(monkeypatch):
    from wikibaseintegrator.wbi_config import config

    from converter.wikidata.item_models import WikidataStatement

    monkeypatch.setitem(config, "WIKIBASE_URL", "https://example.wikibase.cloud")
    monkeypatch.setitem(config, "CALENDAR_MODEL_QID", "Q1")
    uploader = WikidataUploader(token="fixture", allow_live=True)
    uploader._owner_only_session = object()
    claim = uploader._build_claim(WikidataStatement("P569", "+1200-00-00T00:00:00Z", "time"))
    assert (
        claim.get_json()["mainsnak"]["datavalue"]["value"]["calendarmodel"]
        == "http://www.wikidata.org/entity/Q1985727"
    )


def test_wbi_write_uses_explicit_endpoint_and_revision_despite_global_config(monkeypatch):
    from wikibaseintegrator import wbi_helpers
    from wikibaseintegrator.wbi_config import config

    calls = []
    monkeypatch.setitem(config, "MEDIAWIKI_API_URL", "https://example.wikibase.cloud/w/api.php")
    monkeypatch.setattr(
        wbi_helpers, "mediawiki_api_call", lambda *args, **kwargs: calls.append(kwargs) or {}
    )
    login = SimpleNamespace(
        mediawiki_api_url="https://www.wikidata.org/w/api.php",
        get_edit_token=lambda: "fixture-token",
        get_session=lambda: object(),
    )
    wbi_helpers.edit_entity(
        data={"id": "Q123"},
        id="Q123",
        baserevid=42,
        login=login,
        mediawiki_api_url="https://www.wikidata.org/w/api.php",
        allow_redirects=False,
        max_retries=1,
    )
    assert calls[0]["mediawiki_api_url"] == "https://www.wikidata.org/w/api.php"
    assert calls[0]["data"]["baserevid"] == "42"
    assert calls[0]["data"]["id"] == "Q123"
    assert "new" not in calls[0]["data"]
    assert calls[0]["allow_redirects"] is False


def test_owner_login_session_pins_url_and_refuses_redirects(monkeypatch):
    import requests
    from wikibaseintegrator.wbi_config import config

    seen = []

    def request(session, method, url, **kwargs):
        seen.append((url, kwargs.get("allow_redirects")))
        data = kwargs.get("data") or kwargs.get("params") or {}
        if data.get("type") == "login":
            body = {"query": {"tokens": {"logintoken": "token"}}}
        elif data.get("action") == "login":
            body = {"login": {"result": "Success"}}
        elif data.get("meta") == "tokens":
            body = {"query": {"tokens": {"csrftoken": "token"}}}
        else:
            body = {"query": {"userinfo": {"name": "Account"}}}
        return SimpleNamespace(status_code=200, json=lambda: body, raise_for_status=lambda: None)

    monkeypatch.setattr(requests.Session, "request", request)
    monkeypatch.setitem(config, "MEDIAWIKI_API_URL", "https://example.wikibase.cloud/w/api.php")
    uploader = WikidataUploader(token="Account@Bot:fixture", allow_live=True)
    uploader.ensure_owner_only_authenticated()
    assert seen and all(
        url == "https://www.wikidata.org/w/api.php" and not redirects for url, redirects in seen
    )
    with pytest.raises(RuntimeError, match="non-Wikidata"):
        uploader._login.get_session().get("https://example.wikibase.cloud/w/api.php")


def test_owner_mode_rejects_unisolated_oauth_credentials():
    uploader = WikidataUploader(token="fixture|fixture", allow_live=True)
    with pytest.raises(RuntimeError, match="bot passwords or JWT"):
        uploader.ensure_owner_only_authenticated()
