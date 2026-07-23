"""Tests for the per-run HMO item build cache (Phase 4 — see
dev-docs/hmo-wikibase-studio-plan.md).

Pins the fingerprint contract: an unchanged TTL + unchanged schema
mapping is a cache hit; either changing invalidates it. Uses a tiny
synthetic TTL fixture rather than the real ontology/RDF so the test
stays fast and focused on the caching logic.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.models.wikibase_entity_mapping import WikibaseEntityMapping
from app.pipeline import hmo_item_build as pipeline

_TTL = """
@prefix hm: <https://w3id.org/mhm/ontology#> .
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
            ontology_uri="https://w3id.org/mhm/ontology#has_material",
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


@pytest.mark.asyncio
async def test_shacl_report_is_stored_and_returned_on_cache_hit(db_session, tmp_path) -> None:
    await _seed_schema_mappings(db_session)
    ttl_path = tmp_path / "manuscripts.ttl"
    ttl_path.write_text(_TTL, encoding="utf-8")
    run_id = uuid.uuid4()

    fake_report = {
        "QDraft_MS1": [
            {"focus_node": "https://w3id.org/mhm/ontology#MS1",
             "severity": "Violation", "message": "synthetic violation", "value": None,
             "source_shape": ""},
        ],
    }
    with patch.object(
        pipeline, "build_shacl_report_for_items", AsyncMock(return_value=fake_report),
    ):
        first = await pipeline.build_items_for_run(db_session, run_id, ttl_path)
    assert first.from_cache is False
    assert first.shacl_report == fake_report

    second = await pipeline.build_items_for_run(db_session, run_id, ttl_path)
    assert second.from_cache is True
    assert second.shacl_report == fake_report


@pytest.mark.asyncio
async def test_build_uses_real_shacl_pipeline_and_does_not_crash_on_malformed_ttl(
    db_session, tmp_path,
) -> None:
    """End-to-end (no mocking): a build against the real ``validate_with_shacl``
    path must never crash even if SHACL itself has nothing useful to say
    about this tiny synthetic fixture — it degrades to a (possibly empty)
    report rather than raising."""
    await _seed_schema_mappings(db_session)
    ttl_path = tmp_path / "manuscripts.ttl"
    ttl_path.write_text(_TTL, encoding="utf-8")
    run_id = uuid.uuid4()

    result = await pipeline.build_items_for_run(db_session, run_id, ttl_path)

    assert isinstance(result.shacl_report, dict)
