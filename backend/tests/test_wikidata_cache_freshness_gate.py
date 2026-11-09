"""The write paths require the full current Studio build fingerprint."""

from types import SimpleNamespace
from datetime import timedelta
from contextlib import asynccontextmanager
import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.hmo_canonical_entity import HmoCanonicalEntity
from app.models.item_override import WikidataItemOverride
from app.models.run import RunRecord
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.pipeline import wikidata_studio_batches
from app.pipeline.hmo_canonical import normalize_live_entity
from app.pipeline.hmo_canonical_wikidata import canonical_wikidata_fingerprint
from app.pipeline.wikidata_studio_batches import compute_build_fingerprint_streamed
from app.publication.runtime import PublicationRuntime, PublicationSourceError, StudioCacheProjectionSource
from app.publication.types import ProfileRef
from app.routers import wikidata_studio as router
from app.schemas.publication import PreparePublicationRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["legacy", "canonical"])
async def test_old_valid_looking_cache_is_blocked_and_current_cache_passes(
    sample_run, db_session, monkeypatch, source,
):
    run_id = sample_run["run_id"]
    snapshot = {
        "local_id": "Work_1", "source_uri": "https://example.org/Work_1",
        "wikibase_id": "Q123", "entity_type": "F1_Work",
        "labels": {"en": "Example work"}, "control_numbers": ["990001"],
    }
    db_session.add(HmoCanonicalEntity(
        run_id=run_id, local_id="Work_1", source_uri=snapshot["source_uri"],
        wikibase_id="Q123", entity_type="F1_Work", source_fingerprint="a" * 64,
        snapshot=snapshot,
    ))
    await db_session.flush()
    enrichment = await compute_build_fingerprint_streamed(db_session, run_id, approved_only=True)
    current = (canonical_wikidata_fingerprint([normalize_live_entity(snapshot)],
               enrichment_fingerprint=enrichment) if source == "canonical" else enrichment)
    with monkeypatch.context() as old_schema:
        old_schema.setattr(wikidata_studio_batches, "WIKIDATA_STUDIO_BUILD_SCHEMA", "old-schema")
        old_enrichment = await compute_build_fingerprint_streamed(db_session, run_id, approved_only=True)
    old = (canonical_wikidata_fingerprint([normalize_live_entity(snapshot)],
           enrichment_fingerprint=old_enrichment) if source == "canonical" else old_enrichment)
    assert old != current
    item = {
        "local_id": "work:1", "entity_type": "work", "labels": {"en": "Work"},
        "descriptions": {}, "aliases": {}, "validation_issues": [],
        "statements": [{"property": "P31", "value": "Q5", "value_type": "item",
                        "references": [{"property": "P248", "value": "Q1", "type": "item"}]}],
    }
    cache = WikidataStudioCache(
        run_id=run_id, source=source, approved_only=True, input_fingerprint=old,
        result_items=[item], quickstatements="", summary={
            "total_items": 1, "manuscripts": 0, "persons": 0, "works": 1, "statements": 1,
            "source_items_sha256": router._studio_items_digest([item]),
            "overrides_applied": True,
        }, record_count=1,
    )
    db_session.add(cache)
    await db_session.commit()

    client = sample_run["client"]
    response = await client.post(f"/api/runs/{run_id}/wikidata-studio/upload?source={source}")
    assert response.status_code == 409 and "rebuild" in response.text.lower()
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", lambda job_id: None)
    response = await client.post(f"/api/runs/{run_id}/wikidata-publications/prepare", json={
        "profile_id": "mhm-wikidata", "profile_version": "1", "target": "test",
        "source": {"kind": "run", "projection_source": source, "approved_only": True},
    })
    assert response.status_code == 200
    request = PreparePublicationRequest.model_validate({
        "profile_id": "mhm-wikidata", "profile_version": "1", "target": "test",
        "source": {"kind": "run", "projection_source": source, "approved_only": True},
    })
    from app.publication.credentials import configured_publication_gateway_factory

    runtime = PublicationRuntime(session=db_session, gateway_factory=configured_publication_gateway_factory)
    with pytest.raises(PublicationSourceError, match="[Rr]ebuild"):
        await runtime.prepare(run_id=run_id, actor_id=str(sample_run["user_id"]), request=request)

    async def access(*args, **kwargs):
        return None

    async def no_adoption(*args, **kwargs):
        return None

    monkeypatch.setattr(router, "_lookup_run_with_access", access)
    monkeypatch.setattr(router, "_apply_cached_qid_adoption_to_native", no_adoption)
    auth = SimpleNamespace(user=SimpleNamespace(id=sample_run["user_id"]))
    with pytest.raises(HTTPException) as error:
        await router._build_native_items(db_session, run_id, auth, approved_only=True, source=source)
    assert error.value.status_code == 409
    assert "rebuild" in str(error.value.detail).lower()
    projection = StudioCacheProjectionSource(db_session)
    with pytest.raises(PublicationSourceError, match="[Rr]ebuild"):
        await projection.current_snapshot(run_id=run_id, source=source, approved_only=True)

    cache.input_fingerprint = current
    saved_summary = dict(cache.summary)
    cache.summary = {key: value for key, value in cache.summary.items() if key != "source_items_sha256"}
    await db_session.commit()
    with pytest.raises(PublicationSourceError, match="[Rr]ebuild"):
        await projection.current_snapshot(run_id=run_id, source=source, approved_only=True)
    cache.summary = saved_summary
    await db_session.commit()
    native = await router._build_native_items(
        db_session, run_id, auth, approved_only=True, source=source, adopt_probe=False,
    )
    assert len(native) == 1
    reference = await projection.current_snapshot(run_id=run_id, source=source, approved_only=True)
    pages = [page async for page in projection.project(reference, ProfileRef("mhm-wikidata", "1"))]
    assert pages[0][0].entity_key == "work:1"
    prepared = await runtime.prepare(run_id=run_id, actor_id=str(sample_run["user_id"]), request=request)
    assert prepared.publication is not None

    async def merged(*args, **kwargs):
        return [item]

    async def no_build(*args, **kwargs):
        raise AssertionError("GET must not start a build")

    monkeypatch.setattr(router, "fetch_merged_wikidata_items", merged)
    monkeypatch.setattr("app.pipeline.run_job_service.create_job", no_build)
    response = await client.get(f"/api/runs/{run_id}/wikidata-studio?source={source}")
    assert response.status_code == 200
    assert response.json()["cache_stale"] is False

    cache.built_at = cache.built_at + timedelta(seconds=1)
    await db_session.commit()
    assert not await projection.is_current(str(run_id), reference)
    reference = await projection.current_snapshot(run_id=run_id, source=source, approved_only=True)

    cache.result_items = [{**item, "labels": {"en": "Old overwritten result"}}]
    await db_session.commit()
    assert not await projection.is_current(str(run_id), reference)
    with pytest.raises(HTTPException) as error:
        await router._build_native_items(db_session, run_id, auth, approved_only=True, source=source)
    assert error.value.status_code == 409
    cache.result_items = [item]
    await db_session.commit()

    cache.summary = {**cache.summary, "selected_rebuild": {"selected": 1}}
    await db_session.commit()
    assert not await projection.is_current(str(run_id), reference)
    with pytest.raises(HTTPException) as error:
        await router._build_native_items(db_session, run_id, auth, approved_only=True, source=source)
    assert error.value.status_code == 409
    with pytest.raises(PublicationSourceError, match="[Rr]ebuild"):
        await projection.current_snapshot(run_id=run_id, source=source, approved_only=True)

    cache.summary = {key: value for key, value in cache.summary.items() if key != "selected_rebuild"}
    record = (await db_session.execute(select(RunRecord).where(RunRecord.run_id == run_id))).scalars().first()
    record.marc = {**record.marc, "title": "Changed source"}
    await db_session.commit()
    assert not await projection.is_current(str(run_id), reference)
    with pytest.raises(PublicationSourceError, match="changed before preparation"):
        _ = [page async for page in projection.project(reference, ProfileRef("mhm-wikidata", "1"))]


@pytest.mark.asyncio
async def test_full_build_stores_a_digest_and_advisory_mining_preserves_it(sample_run, db_session):
    run_id = sample_run["run_id"]
    fingerprint = await compute_build_fingerprint_streamed(db_session, run_id, approved_only=True)
    items = [{"local_id": "work:1", "entity_type": "work", "statements": []}]
    await router._upsert_studio_cache(
        db_session, run_id=run_id, approved_only=True, source="legacy", fingerprint=fingerprint,
        items=items, quickstatements="", summary={}, approved_match_count=0,
        pending_match_count=0, used_match_count=0, record_count=1, existing=None,
    )
    cache = await router._get_studio_cache_row(db_session, run_id, True, "legacy")
    assert cache.summary["source_items_sha256"] == router._studio_items_digest(items)
    cache.result_items = [{**items[0], "_llm_proposals": {"status": "advisory"}}]
    await db_session.commit()
    assert await router._studio_cache_is_current(
        db_session, run_id, source="legacy", approved_only=True,
        fingerprint=cache.input_fingerprint, summary=cache.summary,
    )


@pytest.mark.asyncio
async def test_freshness_timeout_refuses_upload_instead_of_reusing_cache(sample_run, db_session, monkeypatch):
    async def slow(*args, **kwargs):
        await asyncio.sleep(1)
        return True

    monkeypatch.setattr(router, "_check_studio_cache_current", slow)
    monkeypatch.setattr(router, "_STUDIO_FRESHNESS_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(HTTPException, match="Cannot verify Studio source freshness") as error:
        await router._studio_cache_is_current(
            db_session, sample_run["run_id"], source="legacy", approved_only=True,
            fingerprint="f" * 64, summary={},
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_upload_uses_built_statement_overrides_once(sample_run, db_session, monkeypatch):
    run_id = sample_run["run_id"]
    db_session.add(WikidataItemOverride(
        run_id=run_id, local_id="work:1", remove_statements=[0],
        statement_edits={"1": {"value": "Q42"}},
        add_statements=[{"property": "P407", "value": "Q1860", "value_type": "item"}],
    ))
    await db_session.flush()
    fingerprint = await compute_build_fingerprint_streamed(db_session, run_id, approved_only=True)
    built = [{"local_id": "work:1", "entity_type": "work", "labels": {"en": "Work"},
              "statements": [{"property": "P31", "value": "Q42", "value_type": "item"},
                             {"property": "P407", "value": "Q1860", "value_type": "item"}]}]
    await router._upsert_studio_cache(
        db_session, run_id=run_id, approved_only=True, source="legacy", fingerprint=fingerprint,
        items=built, quickstatements="", summary={}, approved_match_count=0,
        pending_match_count=0, used_match_count=0, record_count=1, existing=None,
    )
    async def no_adoption(*args, **kwargs):
        return None
    monkeypatch.setattr(router, "_apply_cached_qid_adoption_to_native", no_adoption)
    auth = SimpleNamespace(user=SimpleNamespace(id=sample_run["user_id"]))
    result = await router._build_native_items(
        db_session, run_id, auth, approved_only=True, source="legacy", adopt_probe=False,
    )
    assert [(s.property_id, s.value) for s in result[0].statements] == [("P31", "Q42"), ("P407", "Q1860")]
    from app.pipeline.wikidata_item_views import fetch_merged_wikidata_items

    displayed = await fetch_merged_wikidata_items(db_session, run_id, approved_only=True, source="legacy")
    assert [(s["property"], s["value"]) for s in displayed[0]["statements"]] == [
        ("P31", "Q42"), ("P407", "Q1860"),
    ]


@pytest.mark.asyncio
async def test_built_override_p50_must_target_a_person(sample_run, db_session, monkeypatch):
    run_id = sample_run["run_id"]
    ref = {"property": "P248", "value": "Q1", "type": "item"}
    items = [
        {"local_id": "work:1", "entity_type": "work", "labels": {"en": "Work"}, "statements": [
            {"property": "P50", "value": "__LOCAL:work:2", "value_type": "item",
             "references": [ref], "reference_blocks": [[ref]]},
        ]},
        {"local_id": "work:2", "entity_type": "work", "labels": {"en": "Not a person"}, "statements": []},
    ]
    db_session.add(WikidataItemOverride(run_id=run_id, local_id="work:1", add_statements=items[0]["statements"]))
    await db_session.flush()
    fingerprint = await compute_build_fingerprint_streamed(db_session, run_id, approved_only=True)
    await router._upsert_studio_cache(
        db_session, run_id=run_id, approved_only=True, source="legacy", fingerprint=fingerprint,
        items=items, quickstatements="", summary={}, approved_match_count=0,
        pending_match_count=0, used_match_count=0, record_count=1, existing=None,
    )
    async def no_adoption(*args, **kwargs):
        return None
    monkeypatch.setattr(router, "_apply_cached_qid_adoption_to_native", no_adoption)
    auth = SimpleNamespace(user=SimpleNamespace(id=sample_run["user_id"]))
    with pytest.raises(HTTPException, match="INVALID_AUTHOR_TARGET"):
        await router._build_native_items(db_session, run_id, auth, approved_only=True, source="legacy")
    projection = StudioCacheProjectionSource(db_session)
    reference = await projection.current_snapshot(run_id=run_id, source="legacy", approved_only=True)
    with pytest.raises(PublicationSourceError, match="INVALID_AUTHOR_TARGET"):
        _ = [page async for page in projection.project(reference, ProfileRef("mhm-wikidata", "1"))]


@pytest.mark.asyncio
async def test_upload_requires_the_requested_cache_without_a_rebuild(sample_run, db_session, monkeypatch):
    async def no_build(*args, **kwargs):
        raise AssertionError("Upload must not build on a cache miss")

    monkeypatch.setattr(router.wikidata_studio, "build_items_for_run", no_build)
    auth = SimpleNamespace(user=SimpleNamespace(id=sample_run["user_id"]))
    with pytest.raises(HTTPException) as error:
        await router._build_native_items(
            db_session, sample_run["run_id"], auth, approved_only=True, source="legacy",
        )
    assert error.value.status_code == 409
    assert "rebuild" in str(error.value.detail).lower()
    fingerprint = await compute_build_fingerprint_streamed(db_session, sample_run["run_id"], approved_only=True)
    db_session.add(WikidataStudioCache(
        run_id=sample_run["run_id"], source="legacy", approved_only=True,
        input_fingerprint=fingerprint, result_items=[{"local_id": "work:1", "entity_type": "work"}],
        quickstatements="", summary={}, record_count=1,
    ))
    await db_session.commit()
    with pytest.raises(HTTPException) as error:
        await router._build_native_items(
            db_session, sample_run["run_id"], auth, approved_only=False, source="legacy",
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_shard_plan_rebuilds_when_cache_provenance_or_digest_is_stale(sample_run, db_session, monkeypatch):
    from app.models.run_job import RunJob
    from app.pipeline import wikidata_studio_build_shard as shard

    run_id = sample_run["run_id"]
    fingerprint = await compute_build_fingerprint_streamed(db_session, run_id, approved_only=True)
    items = [{"local_id": "work:1", "entity_type": "work", "statements": []}]
    await router._upsert_studio_cache(
        db_session, run_id=run_id, approved_only=True, source="legacy", fingerprint=fingerprint,
        items=items, quickstatements="", summary={}, approved_match_count=0,
        pending_match_count=0, used_match_count=0, record_count=1, existing=None,
    )
    job = RunJob(project_id=sample_run["project_id"], run_id=run_id, kind="wikidata_studio_build",
                 status="running", params={"source": "legacy", "approved_only": True})
    db_session.add(job)
    await db_session.commit()

    @asynccontextmanager
    async def session():
        yield db_session

    monkeypatch.setattr(shard, "session_scope", session)
    plan = await shard.load_wikidata_shard_plan(job.id)
    assert plan.cached_summary is not None and plan.slices == []
    cache = await router._get_studio_cache_row(db_session, run_id, True, "legacy")
    cache.summary = {**cache.summary, "source_items_sha256": "0" * 64}
    await db_session.commit()
    plan = await shard.load_wikidata_shard_plan(job.id)
    assert plan.cached_summary is None and plan.slices
    cache.summary = {**cache.summary, "source_items_sha256": router._studio_items_digest(items),
                     "selected_rebuild": {"selected": 1}}
    await db_session.commit()
    plan = await shard.load_wikidata_shard_plan(job.id)
    assert plan.cached_summary is None and plan.slices


@pytest.mark.asyncio
async def test_shard_merge_applies_index_overrides_once(sample_run, db_session, monkeypatch):
    from app.pipeline import wikidata_studio, wikidata_studio_build_shard as shard
    from converter.wikidata.item_models import WikidataItem, WikidataStatement

    run_id = sample_run["run_id"]
    db_session.add(WikidataItemOverride(
        run_id=run_id, local_id="work:1", remove_statements=[0],
        statement_edits={"1": {"value": "Q42"}},
        add_statements=[{"property": "P407", "value": "Q1860", "value_type": "item"}],
    ))
    await db_session.commit()
    item = WikidataItem(
        local_id="work:1", entity_type="work", labels={"en": "Work"},
        statements=[WikidataStatement("P31", "Q5", "item"), WikidataStatement("P31", "Q6", "item")],
    )
    plan = shard.WikidataShardPlan(job_id=run_id, run_id=run_id, total=1, fingerprint="f" * 64,
                                   approved_only=True, source="legacy", force_rebuild=True, run_user_id=None)

    @asynccontextmanager
    async def session():
        yield db_session

    async def results():
        yield {"control_numbers": ["1"], "items": [wikidata_studio.native_item_payload(item)]}

    async def no_cancel(*args, **kwargs):
        return False

    saved = []
    async def persist(_plan, native, result, **kwargs):
        saved.extend(native)

    monkeypatch.setattr(shard, "session_scope", session)
    monkeypatch.setattr(shard, "is_cancel_requested", no_cancel)
    monkeypatch.setattr(shard, "_update_build_progress", no_cancel)
    monkeypatch.setattr(shard, "_persist_build_result", persist)
    monkeypatch.setattr(wikidata_studio, "finish_native_items", lambda items: {})
    await shard.consume_wikidata_shard_results(plan, results())
    assert [(statement.property_id, statement.value) for statement in saved[0].statements] == [
        ("P31", "Q42"), ("P407", "Q1860"),
    ]


@pytest.mark.asyncio
async def test_get_without_cache_does_not_start_a_build(sample_run, monkeypatch):
    async def fail(*args, **kwargs):
        raise AssertionError("GET must not start a build")

    monkeypatch.setattr("app.pipeline.run_job_service.create_job", fail)
    response = await sample_run["client"].get(
        f"/api/runs/{sample_run['run_id']}/wikidata-studio",
    )
    assert response.status_code == 409
    assert "rebuild" in response.text.lower()
    response = await sample_run["client"].get(
        f"/api/runs/{sample_run['run_id']}/wikidata-studio?force_rebuild=true",
    )
    assert response.status_code == 409
