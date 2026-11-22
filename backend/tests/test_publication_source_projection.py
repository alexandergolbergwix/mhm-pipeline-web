"""Source evidence stays attached to the sealed Publication projection."""

from copy import deepcopy

import pytest

from app.publication.digests import entity_digest
from app.publication.runtime import PublicationSourceError, StudioCacheProjectionSource
from app.publication.types import ProfileRef
from tests.test_publication_router import _add_canonical_cache, _canonical_item, _prepare_direct


@pytest.mark.asyncio
async def test_gila_release_seals_only_the_audited_existing_target(sample_run, db_session):
    first = _canonical_item("work:first", "First work")
    second = _canonical_item("work:second", "Second work")
    first["existing_qid"] = "Q42"
    first["publication_gila_repair_qid"] = "Q900"
    second["existing_qid"] = "Q43"
    await _add_canonical_cache(db_session, sample_run["run_id"], items=[first, second])
    source = StudioCacheProjectionSource(db_session)
    source.selected_existing = {"work:first": "Q42"}
    snapshot = await source.current_snapshot(
        run_id=sample_run["run_id"], source="canonical", approved_only=True,
    )
    pages = [page async for page in source.project(snapshot, ProfileRef("mhm-wikidata", "1"))]
    assert [entity.entity_key for page in pages for entity in page] == ["work:first"]
    assert pages[0][0].document["publication_gila_repair_qid"] == "Q42"
    source.selected_existing = {"work:first": "Q43"}
    with pytest.raises(PublicationSourceError, match="target changed"):
        _ = [page async for page in source.project(snapshot, ProfileRef("mhm-wikidata", "1"))]


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["1", "1-nodes"])
async def test_projected_source_evidence_survives_automatic_projection(
    sample_run, db_session, profile,
):
    original = _canonical_item("work:1", "Work")
    original["records"] = ["990000000000000001"]
    original["work_candidate_evidence"] = [{
        "identity_scope": "record", "source_field": "500", "author_name": "Author",
    }]
    await _add_canonical_cache(db_session, sample_run["run_id"], items=[original])
    source = StudioCacheProjectionSource(db_session)
    source.automatic_documents = {"work:1": deepcopy(original)}
    snapshot = await source.current_snapshot(
        run_id=sample_run["run_id"], source="canonical", approved_only=True,
    )
    entities = [
        entity
        async for page in source.project(snapshot, ProfileRef("mhm-wikidata", profile))
        for entity in page
    ]
    entity = entities[0]
    assert entity.document["records"] == original["records"]
    assert entity.document["work_candidate_evidence"] == original["work_candidate_evidence"]
    assert entity.evidence_refs == ("record:990000000000000001",)
    changed = deepcopy(original)
    changed["records"] = ["990000000000000002"]
    source.automatic_documents = {"work:1": changed}
    with pytest.raises(PublicationSourceError, match="cannot replace work source evidence"):
        _ = [page async for page in source.project(snapshot, ProfileRef("mhm-wikidata", profile))]
    assert entity_digest(entity)


@pytest.mark.asyncio
async def test_duplicate_work_qids_do_not_fold_distinct_source_evidence(sample_run, db_session):
    first = _canonical_item("work:1", "First work")
    second = _canonical_item("work:2", "Second work")
    first["existing_qid"] = second["existing_qid"] = "Q42"
    second["records"] = ["990000000000000002"]
    await _add_canonical_cache(db_session, sample_run["run_id"], items=[first, second])
    source = StudioCacheProjectionSource(db_session)
    snapshot = await source.current_snapshot(
        run_id=sample_run["run_id"], source="canonical", approved_only=True,
    )
    with pytest.raises(PublicationSourceError, match="Duplicate work QIDs"):
        _ = [page async for page in source.project(snapshot, ProfileRef("mhm-wikidata", "1"))]


@pytest.mark.asyncio
async def test_test_ledger_local_reference_carries_sealed_target(sample_run, db_session):
    from app.pipeline.wikidata_qid_ledger import record_ledger_mapping
    from app.publication import ProfileRef
    from app.publication.runtime import StudioCacheProjectionSource
    from tests.test_publication_router import _add_canonical_cache, _canonical_item

    target = _canonical_item("work:ledger", "Target work")
    referrer = _canonical_item("work:ref", "Referring work")
    referrer["statements"] += [
        {"property": "P50", "value": "__LOCAL:work:ledger", "value_type": "item"},
        {"property": "P50", "value": "__LOCAL:work:unverified", "value_type": "item"},
    ]
    await _add_canonical_cache(db_session, sample_run["run_id"], items=[target, referrer])
    await record_ledger_mapping(db_session, "wikidata-test:work:work:ledger", "Q9001")
    source = StudioCacheProjectionSource(db_session)
    source.ledger_is_test = True
    snapshot = await source.current_snapshot(
        run_id=sample_run["run_id"], source="canonical", approved_only=True,
    )
    projected = {
        entity.entity_key: entity
        async for page in source.project(snapshot, ProfileRef("mhm-wikidata", "1"))
        for entity in page
    }
    document = projected["work:ref"].document
    assert document["statements"][1]["value"] == {
        "id": "Q9001", "value_namespace": "local", "value_local_id": "work:ledger",
    }
    assert document["statements"][2]["value"] == "__LOCAL:work:unverified"
    assert document["publication_local_targets"] == {
        "work:ledger": {"site": "test.wikidata.org", "qid": "Q9001"},
    }


@pytest.mark.asyncio
async def test_release_persists_source_evidence(sample_run, db_session):
    from sqlalchemy import select

    from app.models.publication import PublicationEntityRow

    original = _canonical_item("work:1", "Work")
    original["records"] = ["990000000000000001"]
    original["work_candidate_evidence"] = [{"identity_scope": "record", "source_field": "500"}]
    await _add_canonical_cache(db_session, sample_run["run_id"], items=[original])
    publication = await _prepare_direct(
        db_session, sample_run["run_id"], sample_run["user_id"], target="test",
    )
    row = (await db_session.execute(select(PublicationEntityRow).where(
        PublicationEntityRow.entity_key == "work:1",
    ))).scalar_one()
    assert str(row.release_id) == publication.current_release.release_id
    assert row.document["records"] == original["records"]
    assert row.document["work_candidate_evidence"] == original["work_candidate_evidence"]
    assert row.entity_digest


@pytest.mark.asyncio
async def test_release_seals_source_qualified_work_link(sample_run, db_session):
    from sqlalchemy import select

    from app.models.publication import PublicationEntityRow
    from app.pipeline.wikidata_gila_work_evidence import work_source, work_statement
    from app.publication.credentials import configured_publication_gateway_factory
    from app.publication.runtime import PublicationRuntime
    from app.schemas.publication import PreparePublicationRequest

    cn = "990000000000000001"
    local_id, manuscript_qid, work_qid = "QDraft_MS_" + cn, "Q42", "Q43"
    source_case = {
        "qid": manuscript_qid,
        "control_number": cn,
        "source_field": "505",
        "expected_qualifiers": {"P1932": "Work title", "P958": "1r–2v"},
    }
    raw = {"001": cn, "contents": [{
        "title": "Work title", "folio_range": "1r–2v", "source_field": "505",
    }]}

    def snak(value):
        return {"snaktype": "value", "datavalue": {"value": value}}

    manuscript = {
        "id": manuscript_qid,
        "claims": {"P3959": [{"mainsnak": snak(cn)}]},
    }
    work = {"id": work_qid, "claims": {
        "P31": [{"mainsnak": snak({"id": "Q47461344"})}],
        "P1476": [{
            "mainsnak": snak({"text": "Work title"}),
            "references": [{"snaks": {"P3959": [snak(cn)]}}],
        }],
    }}
    occurrence = work_source(raw, source_case)
    assert occurrence
    statement = work_statement(manuscript, work, source_case, occurrence)
    assert statement
    item = _canonical_item(local_id, "Manuscript")
    item.update(entity_type="manuscript", existing_qid=manuscript_qid, records=[cn])
    await _add_canonical_cache(db_session, sample_run["run_id"], items=[item])
    runtime = PublicationRuntime(
        session=db_session, gateway_factory=configured_publication_gateway_factory,
    )
    response = await runtime.prepare(
        run_id=sample_run["run_id"],
        actor_id=str(sample_run["user_id"]),
        request=PreparePublicationRequest.model_validate({
            "profile_id": "mhm-wikidata", "profile_version": "1-nodes", "target": "live",
            "source": {"kind": "run", "projection_source": "canonical", "approved_only": True},
        }),
        selected_existing={local_id: manuscript_qid},
        repair_statements={local_id: []},
        repair_revisions={local_id: 1234},
        repair_evidence_statements={local_id: [statement]},
    )
    assert response.publication is not None
    row = await db_session.scalar(select(PublicationEntityRow).where(
        PublicationEntityRow.entity_key == local_id,
    ))
    assert row is not None
    assert row.document["publication_gila_repair_qid"] == manuscript_qid
    assert row.document["publication_gila_audited_revision"] == 1234
    assert row.document["statements"] == [statement]
    assert row.document["deferred_statements"] == []
