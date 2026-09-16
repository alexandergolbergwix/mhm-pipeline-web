"""Cache-aware HMO item build (Rule W-238).

A "Build items" click must not rebuild RDF + items when the authority
refresh changed nothing — the item fingerprint cache is the point. Only
actual match-row mutations (or an explicit Rebuild (skip cache)) force
the rebuild.
"""

from __future__ import annotations

import pytest

from app.pipeline import hmo_item_build_exec as exec_module
from tests.unit.test_hmo_item_build_progress import _stub_build_pipeline


def _record_row(sample_run):
    from app.models.run import RunRecord

    return RunRecord(
        run_id=sample_run["run_id"],
        control_number="990001800310205171",
        marc={"_control_number": "990001800310205171", "title": "t"},
    )


@pytest.mark.asyncio
async def test_unchanged_enrichment_skips_rdf_rebuild(
    sample_run, db_session, monkeypatch, tmp_path,
) -> None:
    await _stub_build_pipeline(
        monkeypatch, tmp_path,
        re_enrich=_re_enrich(content_changed=False),
    )
    db_session.add(_record_row(sample_run))
    await db_session.commit()

    rdf_calls: list[dict] = []

    async def fake_build_rdf(**kwargs):
        rdf_calls.append(kwargs)
        return type("R", (), {"triples_count": 1, "manuscripts_count": 1})()

    monkeypatch.setattr(exec_module, "build_rdf_graph", fake_build_rdf)

    item_calls: list[dict] = []

    async def fake_build_items(*_a, **kwargs):
        item_calls.append(kwargs)
        return type(
            "R", (),
            {"from_cache": True, "entity_count": 3, "deferred_link_count": 0,
             "skipped_statement_count": 0},
        )()

    monkeypatch.setattr(exec_module.hmo_item_build, "build_items_for_run", fake_build_items)

    result = await exec_module.execute_hmo_item_build(
        db_session, sample_run["run_id"],
        force_rebuild=False, refresh_authority=True,
        on_progress=None,
    )
    assert rdf_calls == [], "unchanged enrichment must not rebuild the RDF graph"
    assert item_calls and item_calls[0].get("force_rebuild") is False
    assert result.rebuilt_rdf is False


@pytest.mark.asyncio
async def test_changed_enrichment_rebuilds_rdf(
    sample_run, db_session, monkeypatch, tmp_path,
) -> None:
    await _stub_build_pipeline(
        monkeypatch, tmp_path,
        re_enrich=_re_enrich(content_changed=True),
    )
    db_session.add(_record_row(sample_run))
    await db_session.commit()

    rdf_calls: list[dict] = []

    async def fake_build_rdf(**kwargs):
        rdf_calls.append(kwargs)
        return type("R", (), {"triples_count": 1, "manuscripts_count": 1})()

    monkeypatch.setattr(exec_module, "build_rdf_graph", fake_build_rdf)

    async def fake_build_items(*_a, **kwargs):
        return type(
            "R", (),
            {"from_cache": False, "entity_count": 3, "deferred_link_count": 0,
             "skipped_statement_count": 0},
        )()

    monkeypatch.setattr(exec_module.hmo_item_build, "build_items_for_run", fake_build_items)

    result = await exec_module.execute_hmo_item_build(
        db_session, sample_run["run_id"],
        force_rebuild=False, refresh_authority=True,
        on_progress=None,
    )
    assert rdf_calls, "changed enrichment must rebuild the RDF graph"
    assert result.rebuilt_rdf is True


def _re_enrich(*, content_changed: bool):
    async def _fake(*_a, **_k):
        return {
            "checked": 1, "updated": 1 if content_changed else 0,
            "newly_matched": 0, "orphans_removed": 0,
            "cross_linked": 0, "wikidata_crosschecked": 0,
            "content_changed": content_changed,
        }
    return _fake
