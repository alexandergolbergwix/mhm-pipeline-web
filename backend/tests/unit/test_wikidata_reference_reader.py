import httpx
import pytest

from app.pipeline.wikidata_reference_reader import ReferenceItemReader


def entity(revision=42):
    return {
        "id": "Q123",
        "type": "item",
        "lastrevid": revision,
        **{key: {} for key in ("labels", "descriptions", "aliases", "claims", "sitelinks")},
    }


def reader_for(handler):
    def route(request):
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == "www.wikidata.org"
        assert not {"authorization", "cookie"} & set(request.headers)
        assert not {"token", "maxlag"} & set(request.url.params)
        return handler(request)

    return ReferenceItemReader(
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(route)),
    )


@pytest.mark.asyncio
async def test_primary_uses_only_public_item_read():
    calls = []

    def route(request):
        calls.append(request.url.params["action"])
        return httpx.Response(200, json={"entities": {"Q123": entity()}})

    assert await reader_for(route).fetch("Q123") == entity()
    assert calls == ["wbgetentities"]


@pytest.mark.parametrize("document_revision", [42, 41])
@pytest.mark.asyncio
async def test_fallback_pins_current_revision_and_rejects_other_revisions(document_revision):
    calls = []

    def route(request):
        action = request.url.params.get("action")
        calls.append(action or "EntityData")
        if action == "wbgetentities":
            return httpx.Response(503)
        if action == "query":
            assert request.url.params["titles"] == "Q123"
            return httpx.Response(
                200,
                headers={"Set-Cookie": "read-test=fixture; Path=/"},
                json={"query": {"pages": [
                    {"title": "Q123", "ns": 0, "revisions": [{"revid": 42}]},
                ]}},
            )
        assert request.url.path == "/wiki/Special:EntityData/Q123.json"
        assert request.url.params["revision"] == "42"
        return httpx.Response(200, json={"entities": {"Q123": entity(document_revision)}})

    if document_revision == 42:
        assert await reader_for(route).fetch("Q123") == entity()
    else:
        with pytest.raises(ValueError, match="does not match"):
            await reader_for(route).fetch("Q123")
    assert calls == ["wbgetentities", "query", "EntityData"]


@pytest.mark.parametrize(
    ("status", "headers", "body"),
    [
        (429, {}, {}),
        (503, {"Retry-After": "30"}, {}),
        (200, {}, {"error": {"code": "maxlag"}}),
        (200, {}, {"entities": {"Q123": {**entity(), "id": "Q456"}}}),
        (200, {}, {"entities": {"Q123": {"id": "Q123", "lastrevid": 42}}}),
    ],
)
@pytest.mark.asyncio
async def test_refusal_or_incomplete_entity_does_not_trigger_fallback(status, headers, body):
    calls = []

    def route(request):
        calls.append(request.url)
        return httpx.Response(status, headers=headers, json=body)

    with pytest.raises(ValueError):
        await reader_for(route).fetch("Q123")
    assert len(calls) == 1
