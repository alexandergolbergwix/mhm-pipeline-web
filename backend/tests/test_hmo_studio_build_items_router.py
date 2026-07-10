"""Router test for POST /runs/{run_id}/hmo-studio/build-items (Phase 4
— see dev-docs/hmo-wikibase-studio-plan.md).
"""

from __future__ import annotations

import pytest

from app.models.wikibase_entity_mapping import WikibaseEntityMapping
from app.pipeline.rdf_build import rdf_output_path_for_run

_TTL = """
@prefix hm: <https://w3id.org/mhm/ontology#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

hm:MS1 rdf:type hm:Codicological_Unit ;
    rdfs:label "Test MS"@en ;
    hm:has_date_of_creation "1500" .
"""


@pytest.mark.asyncio
async def test_build_items_requires_rdf_first(sample_run) -> None:
    run_id = sample_run["run_id"]

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/build-items"
    )
    assert response.status_code == 409
    assert "RDF graph" in response.json()["detail"]


@pytest.mark.asyncio
async def test_build_items_409_when_schema_not_bootstrapped(sample_run) -> None:
    run_id = sample_run["run_id"]
    ttl_path = rdf_output_path_for_run(str(run_id))
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_TTL, encoding="utf-8")

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/build-items"
    )
    assert response.status_code == 409
    assert "schema bootstrap" in response.json()["detail"]

    ttl_path.unlink()


@pytest.mark.asyncio
async def test_build_items_succeeds_once_schema_is_mapped(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    ttl_path = rdf_output_path_for_run(str(run_id))
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_TTL, encoding="utf-8")

    db_session.add_all(
        [
            WikibaseEntityMapping(
                ontology_uri="https://w3id.org/mhm/ontology#Codicological_Unit",
                entity_kind="class",
                wikibase_id="Q1",
                run_id=None,
                label="Codicological Unit",
            ),
            WikibaseEntityMapping(
                ontology_uri="https://w3id.org/mhm/ontology#has_date_of_creation",
                entity_kind="property",
                wikibase_id="P1",
                run_id=None,
                label="has date of creation",
                datatype="time",
            ),
        ]
    )
    await db_session.commit()

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/build-items"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["from_cache"] is False
    assert body["entity_count"] == 1
    assert body["entities"][0]["class_qid"] == "Q1"

    # Second call is a cache hit.
    response2 = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/build-items"
    )
    assert response2.json()["from_cache"] is True

    response3 = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/build-items?force_rebuild=true",
    )
    assert response3.status_code == 200
    assert response3.json()["from_cache"] is False

    ttl_path.unlink()
