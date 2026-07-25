"""HMO Studio must restore the run's TTL from Postgres before checking
whether the RDF graph is present.

Regression for a bug found 2026-07-04: ``rdf.py`` and ``corpus.py`` /
``research.py`` / ``linked_data_explorer.py`` already restored the TTL
from :class:`app.models.rdf_artifact.RdfArtifact` when the local disk
cache was wiped by a Heroku deploy/dyno restart, but ``hmo_studio.py``
never did — so every HMO Studio endpoint (``status``, ``build-manifests``,
``coverage``, ``build-items``) reported "no RDF graph" and the IIIF
Manifests / Wikibase items panels showed 0 even though the run's RDF had
already been built and was safely stored in Postgres.
"""

from __future__ import annotations

import pytest

from app.models.rdf_artifact import RdfArtifact
from app.pipeline.rdf_build import rdf_output_path_for_run

_TTL = """
@prefix hm: <https://w3id.org/mhm/ontology#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

hm:MS1 rdf:type hm:Codicological_Unit ;
    rdfs:label "Test MS"@en .
"""


async def _seed_artifact_only(db_session, run_id) -> None:
    """Simulate a cold dyno: DB has the artefact, local disk does not."""
    ttl_path = rdf_output_path_for_run(str(run_id))
    assert not ttl_path.exists(), "precondition: no local file"
    db_session.add(RdfArtifact(
        run_id=run_id, ttl_content=_TTL, triples_count=2, manuscripts_count=1,
    ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_studio_status_restores_ttl_from_postgres(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    await _seed_artifact_only(db_session, run_id)

    response = await sample_run["client"].get(f"/api/runs/{run_id}/hmo-studio/status")
    assert response.status_code == 200, response.text
    assert response.json()["rdf_present"] is True

    ttl_path = rdf_output_path_for_run(str(run_id))
    assert ttl_path.exists(), "status should have re-seeded the local TTL cache"
    ttl_path.unlink()


@pytest.mark.asyncio
async def test_build_manifests_restores_ttl_from_postgres(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    await _seed_artifact_only(db_session, run_id)

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/build-manifests",
    )
    # A 409 here would mean the TTL was never restored from Postgres.
    # Manifest build now enqueues a background job (201).
    assert response.status_code == 201, response.text
    assert response.json()["kind"] == "hmo_manifest_build"

    from app.pipeline import hmo_studio as hmo_pipeline

    manifest_dir = hmo_pipeline.manifest_dir_for_run(str(run_id))
    for f in manifest_dir.glob("MS_*.json"):
        f.unlink()
    rdf_output_path_for_run(str(run_id)).unlink()


@pytest.mark.asyncio
async def test_build_manifests_ignores_authority_conflicts(
    sample_run, db_session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IIIF build is RDF-only; Rule W-95 authority gate is for Wikibase item upload."""
    import uuid as uuid_mod

    from app.models.run import AuthorityMatch
    from app.pipeline import hmo_studio as hmo_pipeline
    from app.pipeline.hmo_manifest_build_job import run_hmo_manifest_build_job

    run_id = sample_run["run_id"]
    ttl = """
@prefix hm: <https://w3id.org/mhm/ontology#> .
@prefix lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

hm:MS_990001234 a lrmoo:F4_Manifestation_Singleton, hm:Bibliographic_Unit ;
    rdfs:label "Test manuscript"@en ;
    hm:external_identifier_nli "990001234"^^xsd:string .
"""
    db_session.add(RdfArtifact(
        run_id=run_id, ttl_content=ttl, triples_count=4, manuscripts_count=1,
    ))
    # Colliding approved Mazal IDs would 409 item upload — must not block IIIF.
    for text in ("Person A", "Person B"):
        db_session.add(AuthorityMatch(
            run_id=run_id,
            control_number="MS1",
            entity_text=text,
            entity_kind="person",
            role="author",
            matched_name=text,
            approved=True,
            mazal_id="987007111111105171",
            wikidata_qid="",
            viaf_id="",
            confidence="high",
            source="mazal",
            payload={"sources": ["mazal"]},
        ))
    await db_session.commit()

    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", lambda *_a, **_k: None)

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/build-manifests",
    )
    assert response.status_code == 201, response.text
    job_id = uuid_mod.UUID(response.json()["id"])
    await run_hmo_manifest_build_job(job_id)

    manifests = list(hmo_pipeline.manifest_dir_for_run(str(run_id)).glob("MS_*.json"))
    assert len(manifests) >= 1

    for f in manifests:
        f.unlink()
    path = rdf_output_path_for_run(str(run_id))
    if path.exists():
        path.unlink()


@pytest.mark.asyncio
async def test_build_items_restores_ttl_from_postgres(
    sample_run, db_session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build-items must not 409 with "no RDF graph" when the graph is
    only in Postgres — it should restore it first, same as coverage."""
    run_id = sample_run["run_id"]
    await _seed_artifact_only(db_session, run_id)

    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", lambda *_a, **_k: None)

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/build-items?refresh_authority=false",
    )
    assert response.status_code == 201, response.text
    assert response.json()["kind"] == "hmo_item_build"

    rdf_output_path_for_run(str(run_id)).unlink()


@pytest.mark.asyncio
async def test_coverage_restores_ttl_from_postgres_before_enqueueing(
    sample_run, db_session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cold dyno with only a Postgres-backed TTL must still enqueue
    the background coverage job (not 409 with "build RDF first")."""
    run_id = sample_run["run_id"]
    await _seed_artifact_only(db_session, run_id)
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", lambda *_a, **_k: None)

    response = await sample_run["client"].get(f"/api/runs/{run_id}/hmo-studio/coverage")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "hmo_coverage_in_progress", detail

    rdf_output_path_for_run(str(run_id)).unlink()
