"""Rebuild-selected applies curator overrides once, in the canonical stage."""

from types import SimpleNamespace

import pytest

from app.models.item_override import WikidataItemOverride
from app.models.wikidata_studio_cache import WikidataStudioCache


@pytest.mark.asyncio
async def test_rebuild_selected_canonical_passes_overrides_only_to_canonical_stage(
    sample_run, db_session, monkeypatch,
):
    from app.pipeline import hmo_canonical_wikidata, wikidata_studio, wikidata_studio_batches
    from app.routers import wikidata_studio as router

    run_id = sample_run["run_id"]
    cn = "990000000000000001"
    cached_item = {
        "local_id": "manuscript:1", "entity_type": "manuscript", "records": [cn],
        "labels": {"en": "Old"}, "statements": [],
    }
    db_session.add(WikidataStudioCache(
        run_id=run_id, approved_only=True, source="canonical", input_fingerprint="a" * 64,
        result_items=[cached_item], quickstatements="", summary={}, record_count=1,
    ))
    db_session.add(WikidataItemOverride(
        run_id=run_id, local_id="manuscript:1", labels={"en": "Edited"},
        remove_statements=[0], approved=True,
    ))
    await db_session.commit()
    calls: dict[str, dict] = {}

    async def load_slice(db, run_id, record_ids, **kwargs):
        return {"marc_records": [], "approved_matches": [], "entities_by_cn": {}}

    async def no_hmo_qids(*args, **kwargs):
        return {}

    async def canonical_entities(db, run_id):
        return [SimpleNamespace(control_numbers=[cn])]

    def no_context(**kwargs):
        return None

    async def legacy_build(**kwargs):
        calls["legacy"] = kwargs
        return {"native_items": []}

    def canonical_build(entities, **kwargs):
        calls["canonical"] = kwargs
        return {"items": [{
            "local_id": "manuscript:1", "entity_type": "manuscript", "records": [cn],
            "labels": {"en": "Edited"}, "statements": [],
        }]}

    monkeypatch.setattr(wikidata_studio_batches, "load_wikidata_build_slice", load_slice)
    monkeypatch.setattr(wikidata_studio, "hmo_instance_qids_for_run", no_hmo_qids)
    monkeypatch.setattr(router, "_canonical_entities_for_run", canonical_entities)
    monkeypatch.setattr(router, "canonical_studio_context", no_context)
    monkeypatch.setattr(wikidata_studio, "build_items_for_run", legacy_build)
    monkeypatch.setattr(hmo_canonical_wikidata, "build_canonical_studio_result", canonical_build)

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/wikidata-studio/rebuild-selected",
        json={"local_ids": ["manuscript:1"], "source": "canonical"},
    )

    assert response.status_code == 200, response.text
    assert "overrides" not in calls["legacy"]
    overrides = calls["canonical"]["overrides"]
    assert set(overrides) == {"manuscript:1"}
    assert overrides["manuscript:1"]["remove_statements"] == [0]
