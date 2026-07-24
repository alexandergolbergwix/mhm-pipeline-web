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
        f"/api/runs/{run_id}/hmo-studio/build-items?refresh_authority=false"
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
        f"/api/runs/{run_id}/hmo-studio/build-items?refresh_authority=false"
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
            WikibaseEntityMapping(
                ontology_uri="https://w3id.org/mhm/ontology#hmo_source_uri",
                entity_kind="property",
                wikibase_id="P99",
                run_id=None,
                label="HMO source URI",
                datatype="string",
            ),
        ]
    )
    await db_session.commit()

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/build-items?refresh_authority=false"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["from_cache"] is False
    assert body["entity_count"] == 1
    assert body["entities"][0]["class_qid"] == "Q1"

    # Second call is a cache hit.
    response2 = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/build-items?refresh_authority=false"
    )
    assert response2.json()["from_cache"] is True

    response3 = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/build-items?force_rebuild=true&refresh_authority=false",
    )
    assert response3.status_code == 200
    assert response3.json()["from_cache"] is False

    ttl_path.unlink()


@pytest.mark.asyncio
async def test_build_items_refresh_authority_rebuilds_rdf(
    sample_run, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refresh_authority=true must rebuild RDF even when a TTL already exists."""
    run_id = sample_run["run_id"]
    ttl_path = rdf_output_path_for_run(str(run_id))
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_TTL, encoding="utf-8")

    calls: list[str] = []

    async def _fake_re_enrich(*_a, **_k):
        calls.append("re_enrich")
        return {"updated": 0}

    async def _fake_build_rdf(**_kwargs):
        calls.append("rdf")
        from datetime import datetime, timezone

        from app.pipeline.rdf_build import RdfBuildResult

        now = datetime.now(timezone.utc)
        ttl_path.write_text(_TTL, encoding="utf-8")
        return RdfBuildResult(
            triples_count=3,
            manuscripts_count=1,
            output_path=ttl_path,
            started_at=now,
            finished_at=now,
        )

    async def _fake_build_items(*_a, **kwargs):
        calls.append(f"items:{kwargs.get('force_rebuild')}")
        from app.pipeline import hmo_item_build

        return hmo_item_build.HmoItemBuildResult(
            entities=[],
            entity_count=0,
            deferred_link_count=0,
            skipped_statement_count=0,
            from_cache=False,
        )

    monkeypatch.setattr("app.pipeline.authority_re_enrich.re_enrich_run", _fake_re_enrich)
    monkeypatch.setattr(
        "app.pipeline.authority.get_default_matcher",
        lambda: object(),
    )
    monkeypatch.setattr("app.routers.hmo_studio.build_rdf_graph", _fake_build_rdf)
    monkeypatch.setattr(
        "app.pipeline.hmo_item_build.build_items_for_run",
        _fake_build_items,
    )

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/build-items?refresh_authority=true",
    )
    assert response.status_code == 200, response.text
    assert calls == ["re_enrich", "rdf", "items:True"]

    ttl_path.unlink(missing_ok=True)
