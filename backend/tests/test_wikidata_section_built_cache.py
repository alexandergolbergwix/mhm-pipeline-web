"""Section exports must not replay edits that the real builder already applied."""

import csv
import json
from copy import deepcopy

import pytest
from rdflib import Graph, Literal, URIRef
from sqlalchemy import select

from app.models.item_override import WikidataItemOverride
from app.models.run import RunRecord
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.pipeline.wikidata_studio import build_items_for_run, local_id_for_item
from app.pipeline.wikidata_studio_batches import compute_build_fingerprint_streamed


@pytest.mark.asyncio
async def test_built_cache_overrides_are_applied_once_across_export_formats(
    sample_run, db_session, monkeypatch,
):
    from app.pipeline import wikidata_studio

    run_id = sample_run["run_id"]
    record = (await db_session.execute(
        select(RunRecord).where(RunRecord.run_id == run_id),
    )).scalar_one()
    base = await build_items_for_run(
        marc_records=[dict(record.marc)], approved_matches=[], entities_by_cn={},
        return_native=True,
    )
    manuscript = next(item for item in base["native_items"] if item.entity_type == "manuscript")
    local_id = local_id_for_item(manuscript)
    assert len(manuscript.statements) >= 3
    reference = {"property": "P854", "value": "https://example.org/edited", "type": "url"}
    override = {
        "labels": {"en": "Curator label"}, "remove_statements": [0],
        "statement_edits": {"1": {"references": [reference], "reference_blocks": [[reference]]}},
        "add_statements": [{
            "property_id": "P407", "value": "Q1860", "value_type": "item",
            "references": [reference], "reference_blocks": [[reference]],
        }],
    }
    db_session.add(WikidataItemOverride(run_id=run_id, local_id=local_id, **override))
    await db_session.flush()
    built = await build_items_for_run(
        marc_records=[dict(record.marc)], approved_matches=[], entities_by_cn={},
        overrides={local_id: override}, return_native=True,
    )
    native_index = next(
        index for index, item in enumerate(built["native_items"])
        if local_id_for_item(item) == local_id
    )
    built_item = deepcopy(built["items"][native_index])
    built_item["local_id"] = local_id
    assert built_item["statements"][0]["reference_blocks"][0][0]["value"] == reference["value"]
    assert sum(statement["property_id"] == "P407" for statement in built_item["statements"]) == 1
    built_item["claims"] = [{"property": "P31", "value": "Q999"}]
    fingerprint = await compute_build_fingerprint_streamed(db_session, run_id, approved_only=True)
    db_session.add(WikidataStudioCache(
        run_id=run_id, approved_only=True, source="legacy", input_fingerprint=fingerprint,
        result_items=[built_item], quickstatements="", summary=built["summary"], record_count=1,
    ))
    await db_session.commit()

    async def fail_build(*args, **kwargs):
        raise AssertionError("A matching built cache must not rebuild")

    monkeypatch.setattr(wikidata_studio, "build_items_for_run", fail_build)
    client = sample_run["client"]
    url = f"/api/runs/{run_id}/wikidata-studio/export"
    result = await client.get(f"{url}?format=json")
    assert result.status_code == 200
    exported = result.json()["items"][0]
    assert exported["labels"]["en"] == "Curator label"
    assert exported["statements"] == built_item["statements"]
    assert exported["claims"] == built_item["statements"]
    csv_result = await client.get(f"{url}?format=csv")
    assert csv_result.status_code == 200
    row = next(csv.DictReader(csv_result.text.splitlines()))
    assert json.loads(row["statements_json"]) == exported["statements"]
    ttl = await client.get(f"{url}?format=ttl")
    assert ttl.status_code == 200
    graph = Graph().parse(data=ttl.text, format="turtle")
    assert (None, URIRef("http://www.wikidata.org/prop/direct/P31"),
            URIRef("http://www.wikidata.org/entity/Q999")) not in graph
    subject = URIRef(f"http://www.wikidata.org/entity/{local_id}")
    for statement in built_item["statements"]:
        prop = statement.get("property") or statement.get("property_id")
        value = statement.get("value")
        if not prop or not value:
            continue
        obj = (URIRef(f"http://www.wikidata.org/entity/{value}")
               if isinstance(value, str) and value.startswith("Q") else Literal(value))
        assert (subject, URIRef(f"http://www.wikidata.org/prop/direct/{prop}"), obj) in graph
    assert built_item["statements"][0]["reference_blocks"][0][0]["value"] == reference["value"]


@pytest.mark.asyncio
async def test_empty_statements_do_not_revive_stale_claims(sample_run, db_session):
    run_id = sample_run["run_id"]
    fingerprint = await compute_build_fingerprint_streamed(db_session, run_id, approved_only=True)
    db_session.add(WikidataStudioCache(
        run_id=run_id, approved_only=True, source="legacy", input_fingerprint=fingerprint,
        result_items=[{
            "local_id": "work:empty", "entity_type": "work", "statements": [],
            "claims": [{"property": "P31", "value": "Q999"}],
        }],
        quickstatements="", summary={}, record_count=1,
    ))
    await db_session.commit()
    client = sample_run["client"]
    url = f"/api/runs/{run_id}/wikidata-studio/export"
    response = await client.get(f"{url}?format=json")
    assert response.status_code == 200
    assert response.json()["items"][0]["statements"] == []
    assert response.json()["items"][0]["claims"] == []
    response = await client.get(f"{url}?format=csv")
    row = next(csv.DictReader(response.text.splitlines()))
    assert row["claims_count"] == "0" and row["statements_json"] == ""
    response = await client.get(f"{url}?format=ttl")
    graph = Graph().parse(data=response.text, format="turtle")
    assert (None, URIRef("http://www.wikidata.org/prop/direct/P31"),
            URIRef("http://www.wikidata.org/entity/Q999")) not in graph


@pytest.mark.asyncio
async def test_claims_only_cache_has_the_same_facts_in_all_formats(sample_run, db_session):
    run_id = sample_run["run_id"]
    fingerprint = await compute_build_fingerprint_streamed(db_session, run_id, approved_only=True)
    claims = [{"property_id": "P31", "value": "Q5", "value_type": "item"}]
    db_session.add(WikidataStudioCache(
        run_id=run_id, approved_only=True, source="legacy", input_fingerprint=fingerprint,
        result_items=[{"local_id": "work:claims", "entity_type": "work", "claims": claims}],
        quickstatements="", summary={}, record_count=1,
    ))
    await db_session.commit()
    client = sample_run["client"]
    url = f"/api/runs/{run_id}/wikidata-studio/export"
    response = await client.get(f"{url}?format=json")
    assert response.status_code == 200 and response.json()["items"][0]["claims"] == claims
    response = await client.get(f"{url}?format=csv")
    row = next(csv.DictReader(response.text.splitlines()))
    assert json.loads(row["statements_json"]) == claims
    response = await client.get(f"{url}?format=ttl")
    graph = Graph().parse(data=response.text, format="turtle")
    assert (URIRef("http://www.wikidata.org/entity/work:claims"),
            URIRef("http://www.wikidata.org/prop/direct/P31"),
            URIRef("http://www.wikidata.org/entity/Q5")) in graph
