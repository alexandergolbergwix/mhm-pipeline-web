"""Unit tests for app.export.formatters streaming helpers."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from uuid import UUID

import pytest
import rdflib

from app.export.formatters import items_to_rdf_graph, json_array_stream


async def _drain(agen) -> bytes:
    return b"".join([chunk async for chunk in agen])


async def _aiter(items) -> AsyncIterator[dict]:
    for item in items:
        yield item


async def test_json_array_stream_async_items_matches_full_dumps() -> None:
    body = await _drain(json_array_stream(
        {"run_id": "r1", "scope": "all"},
        "entities",
        _aiter([{"id": 1, "name": "א"}, {"id": 2, "name": "b"}]),
    ))
    assert json.loads(body) == {
        "run_id": "r1", "scope": "all",
        "entities": [{"id": 1, "name": "א"}, {"id": 2, "name": "b"}],
    }


async def test_json_array_stream_sync_and_empty_items() -> None:
    body = await _drain(json_array_stream({"run_id": "r2"}, "items", [{"x": 1}]))
    assert json.loads(body) == {"run_id": "r2", "items": [{"x": 1}]}

    empty = await _drain(json_array_stream({"a": 1}, "items", []))
    assert json.loads(empty) == {"a": 1, "items": []}


async def test_json_array_stream_defaults_uuid_and_datetime() -> None:
    when = datetime(2026, 9, 19, 12, 0, 0)
    body = await _drain(json_array_stream(
        {}, "items",
        [{"id": UUID("12345678-1234-5678-1234-567812345678"), "at": when}],
    ))
    assert json.loads(body)["items"][0] == {
        "id": "12345678-1234-5678-1234-567812345678",
        "at": "2026-09-19T12:00:00",
    }


async def test_json_array_stream_first_chunk_before_items() -> None:
    """The header prefix must be yielded before touching a slow item source."""

    async def slow():
        raise AssertionError("items must not be consumed for the first chunk")
        yield {}  # pragma: no cover

    gen = json_array_stream({"run_id": "r"}, "entities", slow())
    first = await gen.__anext__()
    assert json.dumps({"run_id": "r"})[:-1].encode() in first


_BASE = "http://www.wikidata.org/entity/"
_WDT = rdflib.Namespace("http://www.wikidata.org/prop/direct/")
_P = rdflib.Namespace("http://www.wikidata.org/prop/")
_PS = rdflib.Namespace("http://www.wikidata.org/prop/statement/")
_PQ = rdflib.Namespace("http://www.wikidata.org/prop/qualifier/")
_PR = rdflib.Namespace("http://www.wikidata.org/prop/reference/")
_WIKIBASE = rdflib.Namespace("http://wikiba.se/ontology#")
_PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")


def _deprecated_p31_and_sourced_item() -> dict:
    return {
        "qid": "Q1001",
        "labels": {"en": "Codex"},
        "statements": [
            {
                "property": "P31", "value": "Q5", "rank": "deprecated",
                "references": [{"property": "P854", "value": "https://example.org/old"}],
            },
            {
                "property": "P1684", "value": "Sefer", "rank": "normal",
                "qualifiers": [{"property": "P304", "value": "12a"}],
                "references": [{"snaks": [
                    {"property": "P854", "value": "https://example.org/ms"},
                ]}],
            },
        ],
    }


def test_deprecated_p31_is_not_asserted_as_a_truthy_triple() -> None:
    graph = items_to_rdf_graph([_deprecated_p31_and_sourced_item()], _BASE)
    subject = rdflib.URIRef(_BASE + "Q1001")
    statement = graph.value(subject, _P.P31)
    assert (subject, _WDT.P31, rdflib.URIRef(_BASE + "Q5")) not in graph
    assert (statement, _WIKIBASE.rank, _WIKIBASE.DeprecatedRank) in graph
    assert (statement, _PS.P31, rdflib.URIRef(_BASE + "Q5")) in graph


def test_sourced_statement_keeps_rank_qualifier_and_reference() -> None:
    graph = items_to_rdf_graph([_deprecated_p31_and_sourced_item()], _BASE)
    subject = rdflib.URIRef(_BASE + "Q1001")
    statement = graph.value(subject, _P.P1684)
    assert (subject, _WDT.P1684, rdflib.Literal("Sefer")) in graph
    assert (statement, _WIKIBASE.rank, _WIKIBASE.NormalRank) in graph
    assert (statement, _PQ.P304, rdflib.Literal("12a")) in graph
    reference = graph.value(statement, _PROV.wasDerivedFrom)
    assert reference is not None
    assert (reference, _PR.P854, rdflib.Literal("https://example.org/ms")) in graph


@pytest.mark.parametrize("statement", [
    {"property": "P31", "value": "Q5", "rank": "bogus"},
    {"property": "P31x", "value": "Q5"},
    {"property": "P31", "value": "Q5", "qualifiers": [{"property": "P304"}]},
    {"property": "P31", "value": "Q5", "references": [{"snaks": [
        {"property": "P854", "value": ""},
    ]}]},
])
def test_unrepresentable_statement_fails_closed(statement: dict) -> None:
    with pytest.raises(ValueError):
        items_to_rdf_graph([{"qid": "Q1", "statements": [statement]}], _BASE)


async def test_rdf_graph_or_422_maps_unrepresentable_items_to_http_error() -> None:
    from fastapi import HTTPException  # noqa: PLC0415

    from app.routers.section_export import _rdf_graph_or_422  # noqa: PLC0415

    with pytest.raises(HTTPException) as raised:
        await _rdf_graph_or_422(
            [{"qid": "Q1", "statements": [{"property": "P31", "value": "Q5", "rank": "x"}]}],
            _BASE,
        )
    assert raised.value.status_code == 422
