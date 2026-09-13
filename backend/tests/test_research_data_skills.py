"""Data skills: extract small slices from saved dataset artifacts."""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio

_COLUMNS = ["subject", "predicate", "object"]
_ROWS = [
    ["hm:ms1", "rdfs:label", "Ms. Jerusalem 8°0012"],
    ["hm:ms2", "rdfs:label", "Ms. Jerusalem 4°0099"],
    ["hm:ms1", "hm:has_work", "hm:w1"],
    ["hm:ms2", "hm:has_work", "hm:w2"],
    ["hm:w1", "hm:year", "1520"],
    ["hm:w2", "hm:year", "1731"],
]


async def _setup(sample_run, db_session):
    """Mint a grant and store a dataset artifact on the thread."""
    session = await sample_run["client"].post(
        "/api/research-agent/sessions",
        json={"run_id": str(sample_run["run_id"])},
    )
    assert session.status_code == 200, session.text
    body = session.json()
    headers = {"Authorization": f"Bearer {body['tool_grant']}"}
    put = await sample_run["client"].put(
        f"/api/research-agent/threads/{body['thread_id']}/artifacts/sparql-results",
        json={
            "kind": "sparql",
            "title": "SPARQL results (hmo)",
            "content": {"columns": _COLUMNS, "rows": _ROWS},
        },
    )
    assert put.status_code == 200, put.text
    return body, headers


async def _call(sample_run, headers, name, arguments):
    resp = await sample_run["client"].post(
        "/api/research-agent/tools",
        headers=headers,
        json={"name": name, "arguments": arguments},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


async def test_data_info(sample_run):
    body, headers = await _setup(sample_run, None)
    result = await _call(sample_run, headers, "data_info", {"artifact_key": "sparql-results"})
    assert result["row_count"] == 6
    assert result["columns"] == _COLUMNS
    assert len(result["sample"]) == 5


async def test_data_select_eq_and_contains(sample_run):
    body, headers = await _setup(sample_run, None)
    eq = await _call(sample_run, headers, "data_select", {
        "artifact_key": "sparql-results", "column": "predicate", "op": "eq", "value": "hm:has_work",
    })
    assert eq["match_count"] == 2
    contains = await _call(sample_run, headers, "data_select", {
        "artifact_key": "sparql-results", "column": "object", "op": "contains", "value": "jerusalem",
    })
    assert contains["match_count"] == 2


async def test_data_distinct_counts(sample_run):
    body, headers = await _setup(sample_run, None)
    result = await _call(sample_run, headers, "data_distinct", {
        "artifact_key": "sparql-results", "column": "predicate",
    })
    counts = {v["value"]: v["count"] for v in result["values"]}
    assert counts == {"rdfs:label": 2, "hm:has_work": 2, "hm:year": 2}
    assert result["distinct_count"] == 3


async def test_data_search_across_cells(sample_run):
    body, headers = await _setup(sample_run, None)
    result = await _call(sample_run, headers, "data_search", {
        "artifact_key": "sparql-results", "needle": "jerusalem",
    })
    assert result["match_count"] == 2


async def test_data_agg_numeric(sample_run):
    body, headers = await _setup(sample_run, None)
    total = await _call(sample_run, headers, "data_agg", {
        "artifact_key": "sparql-results", "column": "object", "fn": "sum",
    })
    assert total["result"] == 3251.0
    count = await _call(sample_run, headers, "data_agg", {
        "artifact_key": "sparql-results", "column": "predicate", "fn": "count",
    })
    assert count["result"] == 6


async def test_data_unknown_artifact_is_404(sample_run):
    session = await sample_run["client"].post(
        "/api/research-agent/sessions",
        json={"run_id": str(sample_run["run_id"])},
    )
    headers = {"Authorization": f"Bearer {session.json()['tool_grant']}"}
    resp = await sample_run["client"].post(
        "/api/research-agent/tools",
        headers=headers,
        json={"name": "data_info", "arguments": {"artifact_key": "nope"}},
    )
    assert resp.status_code == 404
    assert "No dataset artifact" in resp.json()["detail"]


async def test_data_row_cap(sample_run):
    body, headers = await _setup(sample_run, None)
    result = await _call(sample_run, headers, "data_select", {
        "artifact_key": "sparql-results", "column": "predicate", "op": "contains", "value": ":", "limit": 100,
    })
    assert len(result["rows"]) <= 50
