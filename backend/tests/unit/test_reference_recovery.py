from copy import deepcopy
from urllib.parse import parse_qs

import httpx
import pytest

from app.publication.reference_recovery import (
    LagProbeResult,
    classify_reference_snapshot,
    probe_reference_lag,
)
from tests.unit.test_reference_repair_queue import Gateway


@pytest.mark.parametrize(
    ("status", "payload", "retry_after", "result"),
    [
        (200, {"query": {"general": {}}}, None, LagProbeResult("ready")),
        (200, {"error": {"code": "maxlag"}}, "90", LagProbeResult("delayed", 90)),
        (200, {"error": {"code": "maxlag", "lag": 7.9}}, "5", LagProbeResult("delayed", 5, 7.9)),
        (200, {"error": {"code": "maxlag", "lag": "private"}}, None, LagProbeResult("delayed")),
        (200, {"errors": [{"code": "maxlag"}]}, None, LagProbeResult("unavailable")),
        (200, {"query": {"general": []}}, None, LagProbeResult("unavailable")),
        (200, {"query": {"general": {}}}, "90", LagProbeResult("unavailable", 90)),
        (429, {"query": {"general": {}}}, "120", LagProbeResult("unavailable", 120)),
        (503, {}, "95", LagProbeResult("unavailable", 95)),
    ],
)
@pytest.mark.asyncio
async def test_probe_uses_the_read_only_action_and_requires_a_complete_result(
    status, payload, retry_after, result,
):
    def handle(request):
        assert request.method == "POST"
        assert request.url.host == "www.wikidata.org"
        assert "Authorization" not in request.headers
        fields = parse_qs(request.content.decode())
        assert fields["action"] == ["query"]
        assert fields["maxlag"] == ["5"]
        assert not {"id", "data", "token"} & fields.keys()
        headers = {"Retry-After": retry_after} if retry_after else {}
        return httpx.Response(status, json=payload, headers=headers)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        assert await probe_reference_lag(client) == result


@pytest.mark.asyncio
async def test_queue_lag_probe_uses_its_edit_limit_for_the_request_and_schedule():
    def handle(request):
        fields = parse_qs(request.content.decode())
        assert fields["action"] == ["query"]
        assert fields["maxlag"] == ["20"]
        return httpx.Response(200, json={"error": {"code": "maxlag", "lag": 20.4}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await probe_reference_lag(client, maxlag=20)

    assert result == LagProbeResult("delayed", lag_seconds=20.4, maxlag=20)
    assert result.next_probe_delay() == 30


def test_snapshot_status_cannot_confirm_a_missing_or_mismatched_item():
    item = deepcopy(Gateway().entities["Q123"])
    item.update(descriptions={}, aliases={}, sitelinks={})
    assert classify_reference_snapshot("Q123", 10, item) == "candidates"
    item["claims"]["P217"][0]["references"].pop()
    assert classify_reference_snapshot("Q123", 10, item) == "seen_clean"
    assert classify_reference_snapshot("Q123", 11, item) == "unchecked"
    assert classify_reference_snapshot("Q456", 10, item) == "unchecked"
    item["redirects"] = {"from": "Q123", "to": "Q456"}
    assert classify_reference_snapshot("Q123", 10, item) == "unchecked"
