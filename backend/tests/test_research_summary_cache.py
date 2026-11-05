"""Regression: Linked Data Explorer Overview must never serve a stale zero
summary when the live graph has entities (the recurring bug fixed 2026-06-14).

Two root causes, both covered here:
  1. coherence — a summary with triples>0 but every entity count 0, or no
     manuscripts, is a bad-window read; it must not be served from cache nor
     written to it.
  2. fingerprint — the cache key folds in RdfArtifact.built_at + triple
     count, so an RDF rebuild (content changes, run-id set does not)
     invalidates it.
"""
from __future__ import annotations

import shutil
import uuid

import pytest
import pytest_asyncio

from app.routers.research import _is_coherent_summary, _summary_fingerprint

_HM = "https://w3id.org/mhm/ontology#"
_LRMOO = "http://iflastandards.info/ns/lrm/lrmoo/"

# Real converter classes — one manuscript so query_summary counts 1.
_REAL_TTL = f"""\
@prefix hm:    <{_HM}> .
@prefix lrmoo: <{_LRMOO}> .
@prefix rdfs:  <http://www.w3.org/2000/01/rdf-schema#> .

<urn:ms:1> a lrmoo:F4_Manifestation_Singleton, hm:Bibliographic_Unit ;
    rdfs:label "Test Manuscript" .
"""

_INCOHERENT = {
    "total_manuscripts": 0, "total_works": 0, "total_persons": 0,
    "total_places": 0, "persons_by_role": {"scribe": 0, "owner": 0, "author": 0},
    "triples": 9999,  # bad-window: triples but no entities
}

_PARTIAL_INCOHERENT = {
    "total_manuscripts": 0, "total_works": 210, "total_persons": 0,
    "total_places": 0, "persons_by_role": {"scribe": 0, "owner": 0, "author": 0},
    "triples": 7826,
}


# ── unit: coherence predicate ────────────────────────────────────────────

class TestIsCoherentSummary:
    def test_triples_but_zero_entities_is_incoherent(self) -> None:
        assert _is_coherent_summary(_INCOHERENT) is False

    def test_triples_but_zero_manuscripts_is_incoherent(self) -> None:
        assert _is_coherent_summary(_PARTIAL_INCOHERENT) is False

    def test_real_counts_are_coherent(self) -> None:
        assert _is_coherent_summary(
            {"total_manuscripts": 5, "total_works": 2, "total_persons": 9,
             "total_places": 1, "triples": 500}
        ) is True

    def test_empty_graph_is_coherent(self) -> None:
        # 0 triples + 0 entities is fine (the 404 path handles it upstream).
        assert _is_coherent_summary(
            {"total_manuscripts": 0, "total_works": 0, "total_persons": 0,
             "total_places": 0, "triples": 0}
        ) is True

    def test_partial_entities_with_manuscripts_coherent(self) -> None:
        # Places/persons can legitimately be absent in a tiny corpus; manuscripts cannot.
        assert _is_coherent_summary(
            {"total_manuscripts": 1, "total_works": 0, "total_persons": 0,
             "total_places": 0, "triples": 100}
        ) is True

    def test_large_graph_zero_works_is_incoherent(self) -> None:
        assert _is_coherent_summary(
            {"total_manuscripts": 68, "total_works": 0, "total_persons": 2,
             "total_places": 0, "triples": 15176}
        ) is False

    def test_non_dict_is_incoherent(self) -> None:
        assert _is_coherent_summary(None) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_summary_fingerprint_includes_algorithm_version(db_session) -> None:
    from app.routers.research import _SUMMARY_ALGORITHM_VERSION

    fp = await _summary_fingerprint(
        [str(uuid.uuid4())], db_session, studio_fps={}, wikibase_url="",
    )
    assert f"summary:{_SUMMARY_ALGORITHM_VERSION}" in fp


# ── integration: poisoned cache is rejected, recomputed, overwritten ──────

@pytest_asyncio.fixture
async def project_with_real_rdf(sample_run):
    from app.pipeline.rdf_build import rdf_output_path_for_run
    from app.pipeline.research_graph import _CACHE

    run_id = str(sample_run["run_id"])
    ttl_path = rdf_output_path_for_run(run_id)
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_REAL_TTL, encoding="utf-8")
    _CACHE.clear()
    try:
        yield sample_run
    finally:
        shutil.rmtree(ttl_path.parent, ignore_errors=True)
        _CACHE.clear()


@pytest.mark.asyncio
async def test_stale_incoherent_cache_is_not_served(project_with_real_rdf, db_session):
    from app.pipeline.inference_cache import write_to_inference_cache
    from app.routers.research import _run_ids_for_project, _summary_fingerprint
    from app.routers.wikidata_studio import studio_fingerprints_for_project
    from app.settings import get_settings

    project_id = project_with_real_rdf["project_id"]
    _, client = project_with_real_rdf["user_id"], project_with_real_rdf["client"]

    # Poison the cache under the EXACT key the endpoint will read.
    run_ids = await _run_ids_for_project(project_id, db_session)
    wikibase_url = getattr(get_settings(), "wikibase_sparql_url", "") or ""
    studio_fps = await studio_fingerprints_for_project(run_ids, db_session)
    fp = await _summary_fingerprint(
        run_ids, db_session, studio_fps=studio_fps, wikibase_url=wikibase_url,
    )
    cache_key = {"project": str(project_id), "fp": fp}
    await write_to_inference_cache(
        db_session, kind="research.summary", query_summary=cache_key,
        result=_INCOHERENT, user_id=project_with_real_rdf["user_id"],
    )

    resp = await client.get(f"/api/projects/{project_id}/research/summary")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The stale 0/0/0/0 was rejected; the live graph's real count is served.
    assert body["total_manuscripts"] == 1
    assert _is_coherent_summary(body)


@pytest.mark.asyncio
async def test_coherent_result_is_served_and_cached(project_with_real_rdf, db_session):
    project_id = project_with_real_rdf["project_id"]
    client = project_with_real_rdf["client"]

    resp = await client.get(f"/api/projects/{project_id}/research/summary")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_manuscripts"] == 1

    # Second call (now cache-warm) still coherent.
    resp2 = await client.get(f"/api/projects/{project_id}/research/summary")
    assert resp2.json()["total_manuscripts"] == 1


@pytest.mark.asyncio
async def test_upload_ledger_change_invalidates_a_warm_summary(project_with_real_rdf, db_session):
    from app.models.wikidata_studio_cache import WikidataStudioCache
    from app.pipeline.wikidata_qid_ledger import record_ledger_mapping

    project_id = project_with_real_rdf["project_id"]
    client = project_with_real_rdf["client"]
    db_session.add(WikidataStudioCache(
        run_id=project_with_real_rdf["run_id"], approved_only=True, source="canonical",
        input_fingerprint="a" * 64,
        result_items=[{
            "entity_type": "manuscript", "local_id": "QDraft_MS_990001",
            "labels": {"en": "Test Manuscript"}, "statements": [],
        }],
    ))
    await db_session.commit()
    first = await client.get(f"/api/projects/{project_id}/research/summary")
    assert first.status_code == 200
    assert first.json()["by_type"]["manuscript"]["by_source"]["wikidata"] == 0

    await record_ledger_mapping(db_session, "wikidata:marc:QDraft_MS_990001", "Q123")
    second = await client.get(f"/api/projects/{project_id}/research/summary")
    assert second.status_code == 200
    assert second.json()["by_type"]["manuscript"]["by_source"]["wikidata"] == 1


@pytest.mark.asyncio
async def test_all_studio_cache_variants_contribute_fingerprints(sample_run, db_session):
    from app.models.wikidata_studio_cache import WikidataStudioCache
    from app.routers.wikidata_studio import studio_fingerprints_for_project

    for source, approved_only, token in (
        ("canonical", True, "a"), ("canonical", False, "b"),
        ("legacy", True, "c"), ("legacy", False, "d"),
    ):
        db_session.add(WikidataStudioCache(
            run_id=sample_run["run_id"], source=source, approved_only=approved_only,
            input_fingerprint=token * 64,
        ))
    await db_session.commit()
    result = await studio_fingerprints_for_project([str(sample_run["run_id"])], db_session)
    fingerprint = result[str(sample_run["run_id"])]
    for token in "abcd":
        assert token * 64 in fingerprint
