"""Tests for the per-run HMO item build cache (Phase 4 — see
dev-docs/hmo-wikibase-studio-plan.md).

Pins the fingerprint contract: an unchanged TTL + unchanged schema
mapping is a cache hit; either changing invalidates it. Uses a tiny
synthetic TTL fixture rather than the real ontology/RDF so the test
stays fast and focused on the caching logic.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.wikibase_entity_mapping import WikibaseEntityMapping
from app.pipeline import hmo_item_build as pipeline

_TTL = """
@prefix hm: <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

hm:MS1 rdf:type hm:Codicological_Unit ;
    rdfs:label "Test MS"@en ;
    hm:has_date_of_creation "1500" .
"""


async def _seed_schema_mappings(db_session) -> None:
    db_session.add_all(
        [
            WikibaseEntityMapping(
                ontology_uri="http://www.ontology.org.il/HebrewManuscripts/2025-12-06#Codicological_Unit",
                entity_kind="class",
                wikibase_id="Q1",
                run_id=None,
                label="Codicological Unit",
            ),
            WikibaseEntityMapping(
                ontology_uri="http://www.ontology.org.il/HebrewManuscripts/2025-12-06#has_date_of_creation",
                entity_kind="property",
                wikibase_id="P1",
                run_id=None,
                label="has date of creation",
                datatype="time",
            ),
        ]
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_build_resolves_and_caches(db_session, tmp_path) -> None:
    await _seed_schema_mappings(db_session)
    ttl_path = tmp_path / "manuscripts.ttl"
    ttl_path.write_text(_TTL, encoding="utf-8")
    run_id = uuid.uuid4()

    result = await pipeline.build_items_for_run(db_session, run_id, ttl_path)

    assert result.from_cache is False
    assert result.entity_count == 1
    assert result.entities[0].class_qid == "Q1"
    assert result.entities[0].claims[0].property_id == "P1"


@pytest.mark.asyncio
async def test_second_call_is_a_cache_hit(db_session, tmp_path) -> None:
    await _seed_schema_mappings(db_session)
    ttl_path = tmp_path / "manuscripts.ttl"
    ttl_path.write_text(_TTL, encoding="utf-8")
    run_id = uuid.uuid4()

    first = await pipeline.build_items_for_run(db_session, run_id, ttl_path)
    second = await pipeline.build_items_for_run(db_session, run_id, ttl_path)

    assert first.from_cache is False
    assert second.from_cache is True
    assert second.entity_count == first.entity_count


@pytest.mark.asyncio
async def test_schema_bootstrap_invalidates_cache(db_session, tmp_path) -> None:
    await _seed_schema_mappings(db_session)
    ttl_path = tmp_path / "manuscripts.ttl"
    ttl_path.write_text(_TTL, encoding="utf-8")
    run_id = uuid.uuid4()

    first = await pipeline.build_items_for_run(db_session, run_id, ttl_path)
    assert first.from_cache is False

    # Simulate a new schema bootstrap creating another property — bumps
    # the schema-mapping version even though the TTL is unchanged.
    db_session.add(
        WikibaseEntityMapping(
            ontology_uri="http://www.ontology.org.il/HebrewManuscripts/2025-12-06#has_material",
            entity_kind="property",
            wikibase_id="P2",
            run_id=None,
            label="has material",
            datatype="string",
        )
    )
    await db_session.commit()

    second = await pipeline.build_items_for_run(db_session, run_id, ttl_path)
    assert second.from_cache is False


@pytest.mark.asyncio
async def test_ttl_change_invalidates_cache(db_session, tmp_path) -> None:
    await _seed_schema_mappings(db_session)
    ttl_path = tmp_path / "manuscripts.ttl"
    ttl_path.write_text(_TTL, encoding="utf-8")
    run_id = uuid.uuid4()

    first = await pipeline.build_items_for_run(db_session, run_id, ttl_path)
    assert first.from_cache is False

    ttl_path.write_text(_TTL + '\nhm:MS1 hm:has_date_of_creation "1600" .\n', encoding="utf-8")

    second = await pipeline.build_items_for_run(db_session, run_id, ttl_path)
    assert second.from_cache is False


@pytest.mark.asyncio
async def test_force_rebuild_bypasses_cache(db_session, tmp_path) -> None:
    await _seed_schema_mappings(db_session)
    ttl_path = tmp_path / "manuscripts.ttl"
    ttl_path.write_text(_TTL, encoding="utf-8")
    run_id = uuid.uuid4()

    await pipeline.build_items_for_run(db_session, run_id, ttl_path)
    forced = await pipeline.build_items_for_run(
        db_session, run_id, ttl_path, force_rebuild=True
    )

    assert forced.from_cache is False
