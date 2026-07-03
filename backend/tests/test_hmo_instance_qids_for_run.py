"""Tests for wikidata_studio.hmo_instance_qids_for_run (Phase 6 — see
dev-docs/hmo-wikibase-studio-plan.md).
"""

from __future__ import annotations

import uuid

import pytest

from app.models.wikibase_entity_mapping import WikibaseEntityMapping
from app.pipeline import wikidata_studio
from converter.config.namespaces import HM
from converter.transformer.uri_generator import UriGenerator


@pytest.mark.asyncio
async def test_returns_empty_dict_for_no_control_numbers(db_session) -> None:
    result = await wikidata_studio.hmo_instance_qids_for_run(db_session, uuid.uuid4(), [])
    assert result == {}


@pytest.mark.asyncio
async def test_maps_control_number_to_qid_for_uploaded_manuscript(db_session) -> None:
    run_id = uuid.uuid4()
    other_run_id = uuid.uuid4()
    cn = "990000000000000001"
    uri = str(UriGenerator(namespace=str(HM)).manuscript_uri(cn))

    db_session.add_all(
        [
            WikibaseEntityMapping(
                ontology_uri=uri, entity_kind="instance", wikibase_id="Q42",
                run_id=run_id, label="Test MS",
            ),
            # Same manuscript URI uploaded under a different run must not leak in.
            WikibaseEntityMapping(
                ontology_uri=uri, entity_kind="instance", wikibase_id="Q999",
                run_id=other_run_id, label="Test MS",
            ),
        ]
    )
    await db_session.commit()

    result = await wikidata_studio.hmo_instance_qids_for_run(db_session, run_id, [cn, "990002"])

    assert result == {cn: "Q42"}
