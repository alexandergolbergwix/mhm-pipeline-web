"""Reference block transport across Studio, Publication, and QuickStatements."""

import json
from copy import deepcopy
from dataclasses import dataclass, field

import pytest

from app.pipeline.wikidata_studio import (
    _serialise_item,
    finish_native_items,
    merge_shard_items,
    native_item_from_payload,
    native_item_payload,
    studio_cache_has_stale_validation,
)
from app.publication.digests import canonical_digest, freeze_json, thaw_json
from app.publication.runtime import (
    PublicationSourceError,
    StudioCacheProjectionSource,
    _resolve_statement_references,
)
from app.publication.types import ProfileRef, PublicationEntity
from app.publication.wikidata_gateway import CurrentWikidataBoundary, _SafeWikidataGatewaySession
from app.routers.wikidata_studio import studio_dict_to_native_item
from converter.wikidata import item_models
from converter.wikidata.quickstatements import QuickStatementsExporter


@dataclass
class StatementWithBlocks(item_models.WikidataStatement):
    reference_blocks: list[list[dict[str, object]]] = field(default_factory=list)


@pytest.fixture
def model_with_blocks(monkeypatch):
    monkeypatch.setattr(item_models, "WikidataStatement", StatementWithBlocks)


def _statement():
    blocks = [
        [{"property": "P248", "value": "Q1", "type": "item"},
         {"property": "P813", "value": "+2026-01-01T00:00:00Z",
          "type": "time", "precision": 11}],
        [{"property": "P854", "value": "https://example.org", "type": "url"}],
    ]
    return {
        "property_id": "P31", "value": "Q5", "value_type": "item",
        "references": deepcopy(sum(blocks, [])),
        "reference_blocks": blocks,
    }


def test_studio_and_shard_roundtrip(model_with_blocks):
    raw = {"entity_type": "manuscript", "statements": [_statement()]}
    native = studio_dict_to_native_item(raw)
    statement = native.statements[0]
    assert statement.reference_blocks == raw["statements"][0]["reference_blocks"]
    assert statement.references == sum(statement.reference_blocks, [])
    payload = native_item_payload(native)
    assert native_item_payload(native_item_from_payload(json.loads(json.dumps(payload)))) == payload
    assert _serialise_item(native)["statements"][0]["reference_blocks"][1][0]["value"] == "https://example.org"
    assert raw["statements"][0]["references"][0]["value"] == "Q1"


@pytest.mark.asyncio
async def test_gateway_plan_preserves_sealed_document(model_with_blocks):
    raw = {"statements": [_statement()]}
    sealed = freeze_json(deepcopy(raw))
    entity = PublicationEntity(
        release_id="release", entity_key="item", entity_type="manuscript",
        entity_digest=canonical_digest(raw), document=sealed,
        evidence_refs=(), identity_assertions=(), local_references=(),
    )
    session = _SafeWikidataGatewaySession(None)
    mutation = await session.compile_mutation(entity, action="create", target_qid=None)
    native = CurrentWikidataBoundary._native_item(entity)
    assert native.statements[0].reference_blocks == raw["statements"][0]["reference_blocks"]
    assert thaw_json(mutation.document) == raw
    assert thaw_json(entity.document) == raw


@pytest.mark.asyncio
async def test_gateway_plan_rejects_malformed_blocks():
    raw = {"statements": [{**_statement(), "reference_blocks": ["bad"]}]}
    entity = PublicationEntity(
        release_id="release", entity_key="item", entity_type="manuscript",
        entity_digest=canonical_digest(raw), document=freeze_json(raw),
        evidence_refs=(), identity_assertions=(), local_references=(),
    )
    session = _SafeWikidataGatewaySession(None)
    with pytest.raises(ValueError, match="reference_blocks"):
        await session.compile_mutation(entity, action="create", target_qid=None)
    assert thaw_json(entity.document) == raw


def test_deferred_rehydration_and_resolution(model_with_blocks):
    statement = _statement()
    statement["reference_blocks"][1][0] = {
        "property": "P248", "value": "__LOCAL:work:1", "type": "item",
    }
    statement["references"] = deepcopy(sum(statement["reference_blocks"], []))
    resolved = _resolve_statement_references([statement], {"work:1": "Q42"}, {})
    native = native_item_from_payload({"statements": resolved})
    assert native.statements[0].reference_blocks[1][0]["value"] == "Q42"
    assert statement["reference_blocks"][1][0]["value"] == "__LOCAL:work:1"


def test_quickstatements_marks_only_first_snak_of_each_new_block(model_with_blocks):
    item = studio_dict_to_native_item({"existing_qid": "Q42", "statements": [_statement()]})
    line = QuickStatementsExporter().export_item(item)
    assert line == (
        'Q42\tP31\tQ5\tS248\tQ1\tS813\t+2026-01-01T00:00:00Z/11'
        '\t!S854\t"https://example.org"'
    )


@pytest.mark.parametrize("bad", [None, {}, ["snak"], [["snak"]], [[{1: "bad"}]]])
def test_bad_blocks_fail_closed(model_with_blocks, bad):
    statement = _statement()
    statement["reference_blocks"] = bad
    with pytest.raises(ValueError, match="reference_blocks"):
        studio_dict_to_native_item({"statements": [statement]})
    with pytest.raises(ValueError, match="reference_blocks"):
        native_item_from_payload({"statements": [statement]})


def test_missing_model_contract_fails_closed(monkeypatch):
    @dataclass
    class OldStatement:
        references: list = field(default_factory=list)

    from converter.wikidata.quickstatements import statement_reference_kwargs

    with pytest.raises(ValueError, match="does not support"):
        statement_reference_kwargs(_statement(), OldStatement)


def test_legacy_references_are_one_block(model_with_blocks):
    statement = _statement()
    statement["reference_blocks"] = []
    statement["references"] = [{"property": "P248", "value": "Q999", "type": "item"}]
    item = studio_dict_to_native_item({"existing_qid": "Q42", "statements": [statement]})
    assert QuickStatementsExporter().export_item(item) == "Q42\tP31\tQ5\tS248\tQ999"


def test_quickstatements_omits_ambiguous_legacy_sources():
    statement = _statement()
    statement["reference_blocks"] = []
    item = studio_dict_to_native_item({"existing_qid": "Q42", "statements": [statement]})
    output = QuickStatementsExporter().export_item(item)
    assert "Item omitted: Q42" in output
    assert "Statement omitted: ambiguous flat reference provenance" in output


def test_quickstatements_rejects_empty_or_unresolved_blocks(model_with_blocks):
    statement = _statement()
    statement["reference_blocks"] = [[], [{"property": "P248", "value": "Q1", "type": "item"}]]
    statement["references"] = deepcopy(sum(statement["reference_blocks"], []))
    item = studio_dict_to_native_item({"existing_qid": "Q42", "statements": [statement]})
    output = QuickStatementsExporter().export_item(item)
    assert "Item omitted: Q42" in output
    assert "invalid or unsupported reference" in output
    item.statements[0].reference_blocks = [[
        {"property": "P248", "value": "__LOCAL:x", "type": "item"},
    ]]
    item.statements[0].references = deepcopy(item.statements[0].reference_blocks[0])
    output = QuickStatementsExporter().export_item(item)
    assert "Statement omitted: unresolved local reference" in output
    item.statements[0].reference_blocks = [[
        {"property": "P248", "value": "12", "value_type": "quantity"},
    ]]
    item.statements[0].references = deepcopy(item.statements[0].reference_blocks[0])
    output = QuickStatementsExporter().export_item(item)
    assert "Item omitted: Q42" in output
    assert "invalid or unsupported reference" in output


@pytest.mark.asyncio
async def test_projection_defers_whole_statement_with_reference_target(sample_run, db_session):
    from tests.test_publication_router import _add_canonical_cache, _canonical_item

    source_item = _canonical_item("work:1", "Work")
    target_item = _canonical_item("work:2", "Target")
    statement = _statement()
    statement["reference_blocks"][1][0] = {
        "property": "P248", "value": "__LOCAL:work:2", "type": "item",
    }
    statement["references"] = deepcopy(sum(statement["reference_blocks"], []))
    source_item["statements"] = [statement]
    await _add_canonical_cache(db_session, sample_run["run_id"], items=[source_item, target_item])
    source = StudioCacheProjectionSource(db_session)
    snapshot = await source.current_snapshot(
        run_id=sample_run["run_id"], source="canonical", approved_only=True,
    )
    entities = [
        entity async for page in source.project(snapshot, ProfileRef("mhm-wikidata", "1-nodes"))
        for entity in page
    ]
    projected = next(entity for entity in entities if entity.entity_key == "work:1")
    assert projected.document["statements"] == []
    deferred = projected.document["deferred_statements"][0]
    assert deferred["reference_blocks"] == statement["reference_blocks"]
    assert deferred["references"] == sum(statement["reference_blocks"], [])
    assert source_item["statements"] == [statement]


@pytest.mark.asyncio
async def test_projection_rejects_malformed_blocks(sample_run, db_session):
    from tests.test_publication_router import _add_canonical_cache, _canonical_item

    source_item = _canonical_item("work:1", "Work")
    statement = _statement()
    statement["reference_blocks"] = {"bad": "shape"}
    source_item["statements"] = [statement]
    await _add_canonical_cache(db_session, sample_run["run_id"], items=[source_item])
    source = StudioCacheProjectionSource(db_session)
    snapshot = await source.current_snapshot(
        run_id=sample_run["run_id"], source="canonical", approved_only=True,
    )
    with pytest.raises(PublicationSourceError, match="reference_blocks"):
        _ = [page async for page in source.project(snapshot, ProfileRef("mhm-wikidata", "1"))]


def test_mismatched_flat_references_fail_closed(model_with_blocks):
    statement = _statement()
    statement["references"].append({"property": "P248", "value": "Q999", "type": "item"})
    with pytest.raises(ValueError, match="do not match"):
        studio_dict_to_native_item({"statements": [statement]})
    with pytest.raises(ValueError, match="do not match"):
        native_item_from_payload({"statements": [statement]})


def test_flat_semantics_ignore_display_labels_and_duplicates():
    from converter.wikidata.quickstatements import reference_data

    snak = {"property": "P248", "value": "Q1", "type": "item"}
    flat = [{**snak, "property_label": "stated in", "value_label": "source"}]
    references, blocks = reference_data(flat, [[snak], [snak]])
    assert references == [snak] and blocks == [[snak], [snak]]


def test_shared_snaks_roundtrip_as_one_flat_entry_and_unchanged_blocks():
    shared = {"property": "P248", "value": "Q1", "type": "item"}
    same_shared = {
        "property_id": "P248", "value": "Q1", "value_type": "wikibase-item",
        "value_label": "source",
    }
    first = [shared, {"property": "P854", "value": "https://example.org/one", "type": "url"}]
    second = [same_shared, {"property": "P854", "value": "https://example.org/two", "type": "url"}]
    statement = {
        "property_id": "P31", "value": "Q5", "value_type": "item",
        "references": [*first, second[1]], "reference_blocks": [first, second],
    }
    raw = {"local_id": "ms:1", "entity_type": "manuscript", "statements": [statement]}
    item = studio_dict_to_native_item(raw)
    payload = native_item_payload(item)
    assert payload["statements"][0]["references"] == [*first, second[1]]
    assert payload["statements"][0]["reference_blocks"] == [first, second]
    assert native_item_payload(native_item_from_payload(json.loads(json.dumps(payload)))) == payload
    assert raw["statements"][0] == statement


def test_cache_staleness_exposes_unproven_source_references():
    statement = _statement()
    assert not studio_cache_has_stale_validation([{"statements": [statement]}])
    legacy = deepcopy(statement)
    legacy["reference_blocks"] = []
    assert studio_cache_has_stale_validation([{"statements": [legacy]}])
    legacy["references"] = [legacy["references"][0]]
    assert not studio_cache_has_stale_validation([{"statements": [legacy]}])
    statement["references"].append({"property": "P248", "value": "Q2", "type": "item"})
    assert studio_cache_has_stale_validation([{"statements": [statement]}])


def test_upload_native_guard_blocks_unproven_source_references():
    from fastapi import HTTPException

    from app.routers.wikidata_studio import _require_upload_reference_blocks

    statement = _statement()
    good = studio_dict_to_native_item({"statements": [statement]})
    _require_upload_reference_blocks([good])
    statement["reference_blocks"] = []
    old = studio_dict_to_native_item({"statements": [statement]})
    with pytest.raises(HTTPException, match="Reference provenance") as failure:
        _require_upload_reference_blocks([old])
    assert failure.value.status_code == 409


@pytest.mark.asyncio
async def test_cached_native_loader_blocks_unproven_references(monkeypatch):
    import uuid
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.routers import wikidata_studio as router

    statement = _statement()
    statement["reference_blocks"] = []
    cached = SimpleNamespace(input_fingerprint="f" * 64, summary={}, result_items=[{
        "local_id": "work:1", "entity_type": "work", "statements": [statement],
    }])

    class DB:
        async def execute(self, query):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    async def lookup(*args, **kwargs):
        return None

    async def get_cache(*args, **kwargs):
        return cached

    async def current(*args, **kwargs):
        return True

    monkeypatch.setattr(router, "_lookup_run_with_access", lookup)
    monkeypatch.setattr(router, "_get_studio_cache_row", get_cache)
    monkeypatch.setattr(router, "_studio_cache_is_current", current)
    with pytest.raises(HTTPException, match="Reference provenance") as failure:
        await router._build_native_items(
            DB(), uuid.uuid4(), None, approved_only=True, source="legacy", adopt_probe=False,
        )
    assert failure.value.status_code == 409


def test_stale_cache_does_not_serve_old_quickstatements():
    import uuid

    from app.models.wikidata_studio_cache import WikidataStudioCache
    from app.routers.wikidata_studio import _studio_response_from_cache

    cached = WikidataStudioCache(
        run_id=uuid.uuid4(), approved_only=True, source="legacy", input_fingerprint="a" * 64,
        result_items=[], quickstatements="Q1\tP31\tQ5\tS248\tQ1",
        summary={"total_items": 0, "manuscripts": 0, "persons": 0, "works": 0, "statements": 0},
        approved_match_count=0, pending_match_count=0, used_match_count=0, record_count=0,
    )
    response = _studio_response_from_cache(
        cached, [], approved_only=True, entity_type=None, q=None, upload_outcome=None,
        sort="label", sort_dir="asc", page=1, page_size=50, cache_stale=True,
    )
    assert response.cache_stale
    assert response.quickstatements == ""


@pytest.mark.asyncio
async def test_stale_reference_findings_reach_review_and_export(sample_run, db_session):
    from app.models.item_override import WikidataItemOverride
    from tests.test_publication_router import _add_canonical_cache, _canonical_item

    statement = _statement()
    statement["reference_blocks"] = []
    stale = _canonical_item("work:1", "Stale")
    stale["statements"] = [statement]
    overridden = _canonical_item("work:2", "Override")
    await _add_canonical_cache(db_session, sample_run["run_id"], items=[stale, overridden])
    db_session.add(WikidataItemOverride(
        run_id=sample_run["run_id"], local_id="work:2", add_statements=[statement],
    ))
    await db_session.commit()
    client = sample_run["client"]
    url = f"/api/runs/{sample_run['run_id']}/wikidata-studio/items"
    page = await client.get(f"{url}/page?source=canonical")
    assert page.status_code == 200
    for item in page.json()["items"]:
        assert any(issue["code"] == "REFERENCE_PROVENANCE_UNVERIFIED"
                   for issue in item["validation_issues"])
        assert item["has_blocking_validation"]
    detail = await client.get(f"{url}/work:1?source=canonical")
    assert detail.status_code == 200
    assert any(issue["code"] == "REFERENCE_PROVENANCE_UNVERIFIED"
               for issue in detail.json()["validation_issues"])
    export = await client.get(f"{url}/export?source=canonical&format=json")
    assert export.status_code == 200
    assert all(any(issue["code"] == "REFERENCE_PROVENANCE_UNVERIFIED"
                   for issue in item["validation_issues"]) for item in export.json()["items"])


def test_fresh_manuscript_sources_are_either_explicit_or_blocked():
    from app.pipeline.wikidata_studio import _build_native_sync, validate_upload_reference_blocks
    from tests.unit.test_wikidata_studio_works import _fake_marc_record

    manuscript = next(
        item for item in _build_native_sync([_fake_marc_record("990000000000000001")])
        if item.entity_type == "manuscript"
    )
    sourced = [statement for statement in manuscript.statements if len(statement.references) > 1]
    assert sourced
    if any(not statement.reference_blocks for statement in sourced):
        assert studio_cache_has_stale_validation([native_item_payload(manuscript)])
        with pytest.raises(ValueError, match="Ambiguous flat references"):
            validate_upload_reference_blocks([manuscript])
    else:
        validate_upload_reference_blocks([manuscript])


@pytest.mark.parametrize("value,precision", [
    ("+2026-01-01T00:00:00Z\nQ42\tP31\tQ5", 11),
    ("+2026-01-01T00:00:00Z", "11\nQ42\tP31\tQ5"),
    ("+2026-02-30T00:00:00Z", 11),
    ("+2026-01-01T00:00:00Z", True),
    ("+2026-01-01T00:00:00Z", 15),
])
def test_reference_time_rejects_unsafe_values(model_with_blocks, value, precision):
    snak = {"property": "P813", "value": value, "type": "time", "precision": precision}
    statement = {**_statement(), "references": [snak], "reference_blocks": [[snak]]}
    item = studio_dict_to_native_item({"existing_qid": "Q42", "statements": [statement]})
    output = QuickStatementsExporter().export_item(item)
    assert "Item omitted: Q42" in output
    assert "invalid or unsupported reference" in output
    assert "\tP31\t" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("snak", [
    {"property": "bad", "value": "Q1", "type": "item"},
    {"property": "P248", "value": "not-a-qid", "type": "item"},
    {"property": "P854", "value": "javascript:alert(1)", "type": "url"},
    {"property": "P813", "value": "+2026-01-01T00:00:00Z\nBAD", "type": "time"},
])
async def test_plan_rejects_invalid_reference(snak):
    statement = {**_statement(), "references": [snak], "reference_blocks": [[snak]]}
    raw = {"statements": [statement]}
    entity = PublicationEntity(
        release_id="release", entity_key="item", entity_type="manuscript",
        entity_digest=canonical_digest(raw), document=freeze_json(raw),
        evidence_refs=(), identity_assertions=(), local_references=(),
    )
    with pytest.raises(ValueError):
        await _SafeWikidataGatewaySession(None).compile_mutation(
            entity, action="create", target_qid=None,
        )


@pytest.mark.asyncio
async def test_plan_blocks_ambiguous_legacy_flat_references():
    statement = _statement()
    statement["reference_blocks"] = []
    raw = {"statements": [statement]}
    entity = PublicationEntity(
        release_id="release", entity_key="item", entity_type="manuscript",
        entity_digest=canonical_digest(raw), document=freeze_json(raw),
        evidence_refs=(), identity_assertions=(), local_references=(),
    )
    with pytest.raises(ValueError, match="Ambiguous flat references"):
        await _SafeWikidataGatewaySession(None).compile_mutation(
            entity, action="create", target_qid=None,
        )


@pytest.mark.asyncio
async def test_review_rejects_invalid_references_in_existing_release():
    from app.publication import EntitySelection, ReviewCommand, SummaryQuery
    from app.publication.testing import create_in_memory_publication_module
    from app.publication.types import PublicationEntityInput
    from tests.unit.test_publication_module import _prepare_request

    snak = {"property": "P248", "value": "bad-qid", "type": "item"}
    statement = {**_statement(), "references": [snak], "reference_blocks": [[snak]]}
    module = create_in_memory_publication_module(entities_by_snapshot={"snapshot-7": (
        PublicationEntityInput("work:1", "work", {"statements": [statement]}),
    )})
    operation = await module.prepare(_prepare_request())
    before = await module.read(SummaryQuery(operation.publication_id))
    with pytest.raises(ValueError, match="reference item"):
        await module.advance(operation.publication_id, ReviewCommand(
            release_id=before.release_id, expected_release_digest=before.release_digest,
            selection=EntitySelection.all(), decision="approve", actor_id="curator",
            reason="review", idempotency_key="review-invalid-reference",
        ))
    after = await module.read(SummaryQuery(operation.publication_id))
    assert after.state == "prepared"
    assert after.release_digest == before.release_digest
    assert after.approval_set_id is None


@pytest.mark.asyncio
async def test_plan_blocks_invalid_references_from_old_approval(monkeypatch):
    import app.publication.core as core
    from app.publication import DryRunCommand, EntitySelection, ReviewCommand, SummaryQuery
    from app.publication.gateway import FakeWikidataGateway
    from app.publication.testing import create_in_memory_publication_module
    from app.publication.types import PublicationEntityInput
    from tests.unit.test_publication_module import _prepare_request

    snak = {"property": "P248", "value": "bad-qid", "type": "item"}
    statement = {**_statement(), "references": [snak], "reference_blocks": [[snak]]}
    gateway = FakeWikidataGateway()
    module = create_in_memory_publication_module(
        entities_by_snapshot={"snapshot-7": (
            PublicationEntityInput("work:1", "work", {"statements": [statement]}),
        )}, gateway=gateway,
    )
    operation = await module.prepare(_prepare_request())
    before = await module.read(SummaryQuery(operation.publication_id))
    with monkeypatch.context() as patch:
        patch.setattr(core, "validate_publication_references", lambda document: None)
        await module.advance(operation.publication_id, ReviewCommand(
            release_id=before.release_id, expected_release_digest=before.release_digest,
            selection=EntitySelection.all(), decision="approve", actor_id="curator",
            reason="review", idempotency_key="old-review",
        ))
    reviewed = await module.read(SummaryQuery(operation.publication_id))
    await module.advance(operation.publication_id, DryRunCommand(
        approval_set_id=reviewed.approval_set_id,
        expected_approval_digest=reviewed.approval_digest,
        credential_ref="credential:test", actor_id="curator", idempotency_key="plan-invalid",
    ))
    planned = await module.read(SummaryQuery(operation.publication_id))
    assert planned.plan_blocked_count == 1
    assert planned.state == "dry_run_ready"
    assert gateway.reconcile_sizes == []


@pytest.mark.asyncio
async def test_old_passing_plan_cannot_publish_invalid_references(monkeypatch):
    import app.publication.core as core
    from app.publication import (
        DryRunCommand,
        EntitySelection,
        PublishCommand,
        ReviewCommand,
        SummaryQuery,
    )
    from app.publication.gateway import FakeWikidataGateway, TargetObservation
    from app.publication.testing import create_in_memory_publication_module
    from app.publication.types import PublicationEntityInput
    from tests.unit.test_publication_module import _prepare_request

    snak = {"property": "P248", "value": "bad-qid", "type": "item"}
    statement = {**_statement(), "references": [snak], "reference_blocks": [[snak]]}
    gateway = FakeWikidataGateway(observations={"work:1": TargetObservation.absent("work:1")})
    module = create_in_memory_publication_module(
        entities_by_snapshot={"snapshot-7": (
            PublicationEntityInput("work:1", "work", {"statements": [statement]}),
        )}, gateway=gateway,
    )
    operation = await module.prepare(_prepare_request())
    before = await module.read(SummaryQuery(operation.publication_id))
    with monkeypatch.context() as patch:
        patch.setattr(core, "validate_publication_references", lambda document: None)
        await module.advance(operation.publication_id, ReviewCommand(
            release_id=before.release_id, expected_release_digest=before.release_digest,
            selection=EntitySelection.all(), decision="approve", actor_id="curator",
            reason="review", idempotency_key="old-review",
        ))
        reviewed = await module.read(SummaryQuery(operation.publication_id))
        await module.advance(operation.publication_id, DryRunCommand(
            approval_set_id=reviewed.approval_set_id,
            expected_approval_digest=reviewed.approval_digest,
            credential_ref="credential:test", actor_id="curator", idempotency_key="old-plan",
        ))
    planned = await module.read(SummaryQuery(operation.publication_id))
    assert planned.state == "publishable"
    with pytest.raises(ValueError, match="reference item"):
        await module.advance(operation.publication_id, PublishCommand(
            plan_id=planned.plan_id, dry_run_receipt_id=planned.dry_run_receipt_id,
            expected_receipt_digest=planned.dry_run_receipt_digest,
            credential_ref="credential:test", actor_id="curator", idempotency_key="publish",
        ))
    after = await module.read(SummaryQuery(operation.publication_id))
    assert after.execution_id is None
    assert after.release_digest == before.release_digest


def test_shard_merge_refuses_later_source_statements():
    statement = _statement()
    first = studio_dict_to_native_item({"local_id": "work:1", "entity_type": "work",
                                        "statements": [statement]})
    later = deepcopy(first)
    later.statements[0].reference_blocks[1][0]["value"] = "https://example.org/other"
    with pytest.raises(ValueError, match="references do not match reference_blocks"):
        merge_shard_items([[first], [later]])


def test_deferred_author_fallback_uses_only_main_value():
    from app.pipeline.wikidata_upload_job import _deferred_author_fallback
    from converter.wikidata.item_models import WikidataStatement

    ref = {"property": "P248", "value": "__LOCAL:person:source", "type": "item"}
    statement = WikidataStatement("P50", "Q42", "item", references=[ref], reference_blocks=[[ref]])
    assert _deferred_author_fallback(statement, statement, {"person:source": "Source"}) is None
    author = WikidataStatement("P50", "__LOCAL:person:author", "item")
    fallback = _deferred_author_fallback(author, author, {"person:author": "Author"})
    assert fallback.property_id == "P2093" and fallback.value == "Author"
    author.references = [ref]
    author.reference_blocks = [[ref]]
    assert _deferred_author_fallback(author, author, {"person:author": "Author"}) is None


def test_build_keeps_statement_with_deferred_reference(monkeypatch):
    import app.pipeline.wikidata_canonical_enrichment as enrichment
    import app.pipeline.wikidata_live_native_hygiene as hygiene
    import app.pipeline.wikidata_local_refs as local_refs
    import converter.wikidata.item_validator as validator

    monkeypatch.setattr(enrichment, "normalize_work_author_claims", lambda items: None)
    monkeypatch.setattr(hygiene, "sanitize_studio_items_for_live", lambda items: None)
    monkeypatch.setattr(
        local_refs, "resolve_local_references", lambda items: {"degraded": 0, "dropped": 0},
    )
    monkeypatch.setattr(local_refs, "drop_orphan_significant_person_claims", lambda items: None)
    monkeypatch.setattr(local_refs, "drop_redundant_unknown_text_exemplars", lambda items: None)
    monkeypatch.setattr(validator, "validate_item", lambda item: [])
    ref = {"property": "P248", "value": "__LOCAL:work:1", "type": "item"}
    statement = {**_statement(), "references": [ref], "reference_blocks": [[ref]]}
    item = studio_dict_to_native_item({"entity_type": "manuscript", "statements": [statement]})
    result = finish_native_items([item])
    assert "Statement omitted: unresolved local reference" in result["quickstatements"]
    assert result["items"][0]["statements"][0]["reference_blocks"][0][0]["value"] == ref["value"]


def test_readiness_audit_checks_source_blocks():
    from scripts.audit_test_wikidata_upload import (
        audit_one,
        native_from_studio,
        unresolved_local_refs,
    )

    ref = {"property": "P248", "value": "__LOCAL:person:source", "type": "item"}
    statement = {**_statement(), "references": [ref], "reference_blocks": [[ref]]}
    studio = {"local_id": "work:1", "entity_type": "work", "statements": [statement]}
    native = native_from_studio(studio)
    assert native.statements[0].reference_blocks == [[ref]]
    assert unresolved_local_refs(native.statements) == ["__LOCAL:person:source"]
    row = audit_one(
        {"local_id": "work:1", "status": "created", "qid": "Q1"}, studio,
        {"labels": {"en": {"value": "Example"}}, "claims": {"P31": [{}]}}, None,
    )
    assert "test_reference_blocks_unverified" in row["blockers"]
    assert row["ready_for_live"] is False
