"""Measurement must preserve the same curator state as a production build."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event

from app.models.hmo_canonical_entity import HmoCanonicalEntity
from app.models.item_override import WikidataItemOverride
from app.pipeline.wikidata_item_merge import override_row_to_dict


@pytest.mark.asyncio
async def test_canonical_measurement_retains_complete_curator_state_without_database_writes(
    db_session, sample_run, _engine, monkeypatch, tmp_path,
):
    from app.pipeline import hmo_canonical_wikidata, wikidata_studio
    from scripts.local_measure_verify import WikidataChannel

    run_id = sample_run["run_id"]
    override = WikidataItemOverride(
        run_id=run_id, local_id="person:reviewed",
        labels={"en": "Reviewed name"}, approved=False,
        accepted_foreign_qid="Q123", accept_foreign_modify=False,
        person_identity_choice={"action": "reference", "target_qid": "Q123"},
        source_review_choices=[{"candidate_key": "source:work", "target_qid": "Q456"}],
    )
    db_session.add(override)
    db_session.add(HmoCanonicalEntity(
        run_id=run_id, local_id="person:reviewed", source_uri="https://example.org/person",
        wikibase_id="Q1", source_fingerprint="0" * 64,
        snapshot={"local_id": "person:reviewed", "source_uri": "https://example.org/person",
                  "wikibase_id": "Q1"},
    ))
    await db_session.commit()
    expected = deepcopy(override_row_to_dict(override))
    monkeypatch.setattr(
        wikidata_studio, "build_items_for_run",
        AsyncMock(return_value={"items": [], "native_items": []}),
    )
    observed = {}

    def canonical(snapshots, **kwargs):
        observed.update(deepcopy(kwargs["overrides"]))
        return {"items": [], "native_items": []}

    monkeypatch.setattr(hmo_canonical_wikidata, "build_canonical_studio_result", canonical)
    mutations = []

    def watch(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            mutations.append(statement)

    event.listen(_engine.sync_engine, "before_cursor_execute", watch)
    try:
        channel = WikidataChannel()
        channel.audit_projection = "canonical"
        await channel.build_measurement(run_id, tmp_path)
    finally:
        event.remove(_engine.sync_engine, "before_cursor_execute", watch)
    assert observed["person:reviewed"] == expected
    assert observed["person:reviewed"]["approved"] is False
    assert observed["person:reviewed"]["accepted_foreign_qid"] == "Q123"
    assert not mutations
