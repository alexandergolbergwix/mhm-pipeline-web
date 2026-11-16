"""An alternate public index supplies candidates without public write authority."""

import json
from datetime import UTC, datetime

import pytest
import requests
from rdflib.plugins.sparql.parser import parseQuery
from sqlalchemy import select

from app.models.wikidata_mirror import WikidataMirrorEntity, WikidataMirrorScope
from app.pipeline import wikidata_mirror_qlever as qlever
from app.pipeline.wikidata_mirror_sync import MirrorReadUnavailableError, discover_candidates

pytestmark = pytest.mark.usefixtures("db_session")


def person(identifier):
    return {"entity_type": "person", "statements": [{"property_id": "P214", "value": identifier}]}


def result(*terms):
    return {"head": {"vars": ["item"]}, "results": {"bindings": [
        {"item": {"type": "uri", "value": term}} for term in terms
    ]}}


def envelope(qid="Q123", *, capped=False):
    return {"observed_at": datetime.now(UTC).isoformat(), "response": {
        "query": {"search": [{"title": qid}]}, "limit_reached": capped,
        "excluded_non_item_rows": 0,
    }}


def test_seed_batches_are_bounded_and_keep_untrusted_names_as_literals():
    name = 'שם " } SERVICE <https://example.org/> { ?s ?p ?o } #'
    items = [person(str(index)) for index in range(102)] + [
        {"entity_type": "person", "labels": {"he": name}}
    ]
    batches = qlever.discovery_batches(items)
    identifiers = [batch for batch in batches if batch.predicate.endswith("/P214")]
    assert [len(batch.values) for batch in identifiers] == [50, 50, 2]
    for batch in batches:
        query = batch.sparql()
        assert parseQuery(query)[1].name == "SelectQuery"
        assert query.endswith("LIMIT 1001")
        assert not any(word in query for word in ["ORDER BY", "DISTINCT", "FILTER", "UNION"])
        assert "Alexander" not in query
    name_batches = [batch for batch in batches if batch not in identifiers]
    assert len(name_batches) == 3
    assert all(json.dumps(name, ensure_ascii=False) + "@he" in batch.sparql()
               for batch in name_batches)


def test_response_keeps_only_wikidata_item_uris_and_counts_raw_limit():
    body = result("http://www.wikidata.org/entity/Q123",
                  "https://www.wikidata.org/entity/L1",
                  "https://example.org/entity/Q999")
    body["results"]["bindings"] *= 334
    decoded = qlever.decode_response(body)
    assert decoded["query"]["search"] == [{"title": "Q123"}]
    assert decoded["limit_reached"] is True
    assert decoded["excluded_non_item_rows"] == 668


@pytest.mark.parametrize("body", [
    {}, {"error": "failed"}, {"head": {"vars": ["other"]}, "results": {"bindings": []}},
    {"head": {"vars": ["item"]}, "results": {"bindings": [{}]}},
    {"head": {"vars": ["item"]}, "results": {"bindings": [
        {"item": {"type": "literal", "value": "Q123"}}
    ]}},
])
def test_incomplete_results_cannot_supply_candidates(body):
    with pytest.raises(ValueError):
        qlever.decode_response(body)


def test_transport_has_one_fixed_public_select_and_no_credential(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return result("http://www.wikidata.org/entity/Q123")

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, **kwargs):
            calls.append((url, kwargs, self.trust_env))
            return Response()

    monkeypatch.setattr(qlever.requests, "Session", Session)
    monkeypatch.setattr(qlever.time, "sleep", lambda seconds: None)
    batch = qlever.discovery_batches([person("12345")])[0]
    assert qlever.request_batch(batch)["response"]["query"]["search"] == [{"title": "Q123"}]
    assert len(calls) == 1
    url, options, trust_env = calls[0]
    assert url == "https://qlever.dev/api/wikidata"
    assert options["data"] == {"query": batch.sparql()}
    assert options["allow_redirects"] is False
    assert not trust_env and "auth" not in options and "cookies" not in options
    assert set(options["headers"]) == {"Accept", "User-Agent"}
    with pytest.raises(ValueError):
        qlever.request_batch(qlever.SeedBatch("https://example.org/", ("unsafe",)))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_capped_query_stays_partial_and_resume_retains_its_candidates(
    monkeypatch, _sessionmaker,
):
    calls = []

    async def read(factory, batch, **kwargs):
        calls.append(batch)
        return envelope(capped=True)

    monkeypatch.setattr(qlever, "cached_read", read)
    first = await discover_candidates(_sessionmaker, [person("12345")], provider="qlever")
    second = await discover_candidates(_sessionmaker, [person("12345")], provider="qlever")
    assert first["qids"] == second["qids"] == ["Q123"]
    assert first["partial_queries"] == second["partial_queries"] == 1
    assert first["coverage"] == "candidate_search_only" and second["queries"] == 0
    assert len(calls) == 1
    async with _sessionmaker() as db:
        scopes = (await db.execute(select(WikidataMirrorScope))).scalars().all()
        assert [(scope.source, scope.status) for scope in scopes] == [
            ("qlever_search", "query_capped")
        ]
        assert scopes[0].progress["negative_identity_proof"] is False
        assert not (await db.execute(select(WikidataMirrorEntity))).scalars().all()


@pytest.mark.asyncio
async def test_error_stops_requests_and_resume_retains_saved_positive(monkeypatch, _sessionmaker):
    calls = []
    fail = True

    async def read(factory, batch, **kwargs):
        calls.append(batch)
        if fail and len(calls) == 2:
            raise requests.HTTPError("provider refusal")
        return envelope()

    monkeypatch.setattr(qlever, "cached_read", read)
    items = [{"entity_type": "person", "labels": {"he": "שם"}}]
    with pytest.raises(MirrorReadUnavailableError):
        await discover_candidates(_sessionmaker, items, provider="qlever")
    assert len(calls) == 2
    fail = False
    resumed = await discover_candidates(_sessionmaker, items, provider="qlever", max_queries=1)
    assert resumed["qids"] == ["Q123"] and resumed["partial_queries"] == 1
    assert len(calls) == 3 and calls[2] == calls[1]


@pytest.mark.asyncio
async def test_qlever_cache_uses_its_own_endpoint_and_releases_transaction(
    monkeypatch, _sessionmaker,
):
    async def cache(db, *, kind, endpoint, payload, fetch, skip_cache):
        assert kind == "wikidata.mirror_search"
        assert endpoint == qlever.API
        assert payload["schema"] == "mirror-qlever-v1"
        await db.execute(select(WikidataMirrorScope))
        response = await fetch()
        assert not db.in_transaction()
        return response

    monkeypatch.setattr(qlever, "cache_http_call", cache)
    monkeypatch.setattr(qlever, "request_batch", lambda batch: envelope())
    response = await qlever.cached_read(_sessionmaker, qlever.discovery_batches([person("1")])[0])
    assert response["response"]["query"]["search"] == [{"title": "Q123"}]
