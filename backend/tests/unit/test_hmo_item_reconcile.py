"""Tests for the live-Wikibase exists-check reconciliation pipeline.

Mirrors Rule W-30's fail-closed philosophy: a SPARQL lookup failure must
raise (never silently read as "doesn't exist").
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.models.wikibase_entity_mapping import WikibaseEntityMapping
from app.pipeline import hmo_item_reconcile as reconcile_module
from app.pipeline.hmo_item_reconcile import (
    HMO_SOURCE_URI,
    ReconciliationUnavailableError,
    reconcile_item,
)


async def _seed_source_uri_mapping(db_session, *, pid: str = "P42") -> None:
    db_session.add(
        WikibaseEntityMapping(
            ontology_uri=HMO_SOURCE_URI,
            entity_kind="property",
            wikibase_id=pid,
            run_id=None,
            label="HMO source URI",
            datatype="string",
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_returns_not_found_when_schema_mapping_is_missing(db_session) -> None:
    outcome = await reconcile_item(db_session, "http://example.org/MS1")

    assert outcome.found is False
    assert outcome.wikibase_id is None
    assert "not mapped" in outcome.message


@pytest.mark.asyncio
async def test_found_when_sparql_returns_a_binding(db_session, monkeypatch) -> None:
    await _seed_source_uri_mapping(db_session)
    monkeypatch.setattr(
        reconcile_module.get_settings(), "wikibase_sparql_url",
        "https://mhm-hmo.wikibase.cloud/query/sparql",
        raising=False,
    )
    fake_response = {
        "results": {
            "bindings": [
                {"item": {"value": "https://mhm-hmo.wikibase.cloud/entity/Q123"}},
            ],
        },
    }
    with patch.object(
        reconcile_module, "run_wikibase_sparql", AsyncMock(return_value=fake_response),
    ):
        outcome = await reconcile_item(db_session, "http://example.org/MS1")

    assert outcome.found is True
    assert outcome.wikibase_id == "Q123"


@pytest.mark.asyncio
async def test_not_found_when_sparql_returns_no_bindings(db_session, monkeypatch) -> None:
    await _seed_source_uri_mapping(db_session)
    monkeypatch.setattr(
        reconcile_module.get_settings(), "wikibase_sparql_url",
        "https://mhm-hmo.wikibase.cloud/query/sparql",
        raising=False,
    )
    with patch.object(
        reconcile_module, "run_wikibase_sparql",
        AsyncMock(return_value={"results": {"bindings": []}}),
    ):
        outcome = await reconcile_item(db_session, "http://example.org/MS1")

    assert outcome.found is False
    assert outcome.wikibase_id is None


@pytest.mark.asyncio
async def test_raises_on_network_error_instead_of_returning_not_found(
    db_session, monkeypatch,
) -> None:
    await _seed_source_uri_mapping(db_session)
    monkeypatch.setattr(
        reconcile_module.get_settings(), "wikibase_sparql_url",
        "https://mhm-hmo.wikibase.cloud/query/sparql",
        raising=False,
    )
    with patch.object(
        reconcile_module, "run_wikibase_sparql",
        AsyncMock(side_effect=httpx.ConnectTimeout("timed out")),
    ):
        with pytest.raises(ReconciliationUnavailableError):
            await reconcile_item(db_session, "http://example.org/MS1")


@pytest.mark.asyncio
async def test_raises_when_sparql_endpoint_not_configured(db_session, monkeypatch) -> None:
    await _seed_source_uri_mapping(db_session)
    monkeypatch.setattr(
        reconcile_module.get_settings(), "wikibase_sparql_url", "", raising=False,
    )

    with pytest.raises(ReconciliationUnavailableError):
        await reconcile_item(db_session, "http://example.org/MS1")
