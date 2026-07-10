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
    resolve_source_uri_pid,
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
async def test_query_uses_instance_property_uri_and_both_namespaces(
    db_session, monkeypatch,
) -> None:
    """Rule W-56: the reconcile query must NOT use ``wdt:`` (that resolves to
    Wikidata on wikibase.cloud) and must match old + new ontology namespaces."""
    await _seed_source_uri_mapping(db_session)
    monkeypatch.setattr(
        reconcile_module.get_settings(), "wikibase_sparql_url",
        "https://mhm-hmo.wikibase.cloud/query/sparql", raising=False,
    )
    monkeypatch.setattr(
        reconcile_module.get_settings(), "wikibase_cloud_base_url",
        "https://mhm-hmo.wikibase.cloud", raising=False,
    )
    captured: dict[str, str] = {}

    async def _capture(url, query):
        captured["query"] = query
        return {"results": {"bindings": []}}

    with patch.object(reconcile_module, "run_wikibase_sparql", _capture):
        await reconcile_item(
            db_session,
            "https://w3id.org/mhm/ontology#CU_990000403370205171_main",
        )
    q = captured["query"]
    assert "wdt:" not in q
    assert "/prop/direct/P" in q
    assert "https://w3id.org/mhm/ontology#CU_990000403370205171_main" in q
    assert (
        "http://www.ontology.org.il/HebrewManuscripts/2025-12-06#"
        "CU_990000403370205171_main" in q
    )


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


@pytest.mark.asyncio
async def test_pid_lookup_transaction_is_closed_before_the_sparql_call(
    db_session, monkeypatch,
) -> None:
    """A caller that doesn't pre-resolve ``pid`` must never sit in an open
    transaction across the slow external SPARQL call — a bulk upload
    chaining this straight into a retrying Wikibase Cloud write could
    otherwise exceed app.db's 2-minute idle-in-transaction backstop."""
    await _seed_source_uri_mapping(db_session)
    monkeypatch.setattr(
        reconcile_module.get_settings(), "wikibase_sparql_url",
        "https://mhm-hmo.wikibase.cloud/query/sparql",
        raising=False,
    )

    async def _fake_sparql(_url, _query):
        assert not db_session.in_transaction(), (
            "reconcile_item must commit its pid lookup before making the "
            "SPARQL call, not hold the transaction open across it"
        )
        return {"results": {"bindings": []}}

    with patch.object(reconcile_module, "run_wikibase_sparql", _fake_sparql):
        await reconcile_item(db_session, "http://example.org/MS1")


@pytest.mark.asyncio
async def test_pre_resolved_pid_skips_the_redundant_lookup(db_session, monkeypatch) -> None:
    """A caller looping over many entities (the item-upload job) resolves
    the pid once via resolve_source_uri_pid and passes it through — this
    must skip the per-item DB round trip entirely."""
    await _seed_source_uri_mapping(db_session, pid="P42")
    pid = await resolve_source_uri_pid(db_session)
    assert pid == "P42"

    monkeypatch.setattr(
        reconcile_module.get_settings(), "wikibase_sparql_url",
        "https://mhm-hmo.wikibase.cloud/query/sparql",
        raising=False,
    )
    with patch.object(
        reconcile_module, "_source_uri_property_pid",
        AsyncMock(side_effect=AssertionError("must not re-query pid when it was pre-resolved")),
    ), patch.object(
        reconcile_module, "run_wikibase_sparql",
        AsyncMock(return_value={"results": {"bindings": []}}),
    ):
        outcome = await reconcile_item(db_session, "http://example.org/MS1", pid=pid)

    assert outcome.found is False
