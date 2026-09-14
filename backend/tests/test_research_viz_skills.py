"""Visualization + export skills: movement map and PDF download."""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


async def _mint(sample_run) -> dict:
    session = await sample_run["client"].post(
        "/api/research-agent/sessions",
        json={"run_id": str(sample_run["run_id"])},
    )
    assert session.status_code == 200, session.text
    return session.json()


async def _headers(sample_run) -> dict:
    session = await _mint(sample_run)
    return session, {"Authorization": f"Bearer {session['tool_grant']}"}


async def test_show_movement_map_corpus(sample_run, db_session):
    body, headers = await _headers(sample_run)
    resp = await sample_run["client"].post(
        "/api/research-agent/tools",
        headers=headers,
        json={"name": "show_movement_map", "arguments": {}},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["artifact_key"] == "movement-map"
    assert result["manuscript_count"] >= 1
    assert "Movement map" in result["note"]


async def test_show_movement_map_one_manuscript(sample_run):
    body, headers = await _headers(sample_run)
    resp = await sample_run["client"].post(
        "/api/research-agent/tools",
        headers=headers,
        json={"name": "show_movement_map", "arguments": {"cn": "990000000000000001"}},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["cn"] == "990000000000000001"
    assert isinstance(result["stop_count"], int)


async def test_show_movement_map_unknown_cn_is_404(sample_run):
    body, headers = await _headers(sample_run)
    resp = await sample_run["client"].post(
        "/api/research-agent/tools",
        headers=headers,
        json={"name": "show_movement_map", "arguments": {"cn": "does-not-exist"}},
    )
    assert resp.status_code == 404


async def test_show_link_types_groups_properties(sample_run):
    body, headers = await _headers(sample_run)
    seed = await sample_run["client"].put(
        f"/api/research-agent/threads/{body['thread_id']}/artifacts/wikidata-items",
        json={
            "kind": "sparql",
            "title": "Wikidata items (live claims)",
            "content": {
                "columns": ["qid", "label", "property", "value", "target_is_ours"],
                "rows": [
                    ["Q1111", "Ms", "P31", "Q87167", ""],
                    ["Q1111", "Ms", "P1476", "title", ""],
                    ["Q2222", "Work", "P50", "Q127398", "ours"],
                    ["Q3333", "Tradition", "lrmoo:R4_embodies", "work", ""],
                    ["Q3333", "Tradition", "https://w3id.org/mhm/ontology#witnesses", "ms", ""],
                    ["Q4444", "Ms2", "cidoc:P72_has_language", "hebrew", ""],
                ],
            },
        },
    )
    assert seed.status_code == 200, seed.text
    resp = await sample_run["client"].post(
        "/api/research-agent/tools",
        headers=headers,
        json={"name": "show_link_types", "arguments": {}},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["artifact_key"] == "link-types"
    assert result["claim_rows"] == 6
    assert result["distinct_properties"] == 6
    assert result["items_counted"] == 4
    assert result["internal_links"] == 1
    group_names = {g["name"] for g in result["groups"]}
    assert {"Wikidata properties", "FRBRoo / LRMoo (IFLA)", "MHM ontology", "CIDOC CRM"} <= group_names
    top = {d["label"]: d for d in result["top_links"]}
    assert "P31 — instance of" in top and "mhm:witnesses" in top
    assert top["P50 — author"]["internal"] == 1

    # The chart artifact was persisted on the thread.
    versions = await sample_run["client"].post(
        "/api/research-agent/tools",
        headers=headers,
        json={"name": "canvas_list_versions", "arguments": {"artifact_key": "link-types"}},
    )
    assert versions.status_code == 200, versions.text


async def test_show_link_types_without_property_column_is_404(sample_run):
    body, headers = await _headers(sample_run)
    seed = await sample_run["client"].put(
        f"/api/research-agent/threads/{body['thread_id']}/artifacts/wikidata-items",
        json={
            "kind": "sparql",
            "title": "other",
            "content": {"columns": ["a"], "rows": [["x"]]},
        },
    )
    assert seed.status_code == 200, seed.text
    resp = await sample_run["client"].post(
        "/api/research-agent/tools",
        headers=headers,
        json={"name": "show_link_types", "arguments": {}},
    )
    assert resp.status_code == 404


async def test_show_wikidata_places_maps_place_claims(sample_run):
    body, headers = await _headers(sample_run)
    seed = await sample_run["client"].put(
        f"/api/research-agent/threads/{body['thread_id']}/artifacts/wikidata-items",
        json={
            "kind": "sparql",
            "title": "Wikidata items (live claims)",
            "content": {
                "columns": ["qid", "label", "property", "value", "target_is_ours"],
                "rows": [
                    ["Q141175480", "אב הרחמים", "P17", "Q801", ""],
                    ["Q141175483", "יקום פורקן", "P17", "Q38", ""],
                ],
            },
        },
    )
    assert seed.status_code == 200, seed.text

    wdqs = {
        "results": {"bindings": [
            {
                "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q141175480"},
                "itemLabel": {"value": "אב הרחמים"},
                "prop": {"type": "uri", "value": "http://www.wikidata.org/prop/direct/P17"},
                "propLabel": {"value": "country"},
                "place": {"type": "uri", "value": "http://www.wikidata.org/entity/Q801"},
                "placeLabel": {"value": "Israel"},
                "coord": {"type": "literal", "value": "Point(34.9 31.5)"},
            },
            {  # duplicate item/place pair — deduped
                "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q141175480"},
                "itemLabel": {"value": "אב הרחמים"},
                "prop": {"type": "uri", "value": "http://www.wikidata.org/prop/direct/P17"},
                "propLabel": {"value": "country"},
                "place": {"type": "uri", "value": "http://www.wikidata.org/entity/Q801"},
                "placeLabel": {"value": "Israel"},
                "coord": {"type": "literal", "value": "Point(34.9 31.5)"},
            },
            {  # no coordinates — dropped
                "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q141175483"},
                "itemLabel": {"value": "יקום פורקן"},
                "prop": {"type": "uri", "value": "http://www.wikidata.org/prop/direct/P17"},
                "propLabel": {"value": "country"},
                "place": {"type": "uri", "value": "http://www.wikidata.org/entity/Q38"},
                "placeLabel": {"value": "Italy"},
                "coord": {"type": "literal", "value": "Point(nothing)"},
            },
        ]},
    }

    from unittest.mock import AsyncMock, patch

    import app.services.research_agent.tools as tools_module

    with patch.object(tools_module, "_wikidata_sparql_query", new=AsyncMock(return_value=wdqs)):
        resp = await sample_run["client"].post(
            "/api/research-agent/tools",
            headers=headers,
            json={"name": "show_wikidata_places", "arguments": {}},
        )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["artifact_key"] == "wikidata-places"
    assert result["point_count"] == 1
    assert result["distinct_places"] == 1
    assert result["items_with_places"] == 1
    assert result["top_places"][0]["place"] == "Israel"
    assert "P625" in result["note"]


async def test_wikidata_pack_runs_all_steps_and_reports_errors(sample_run):
    body, headers = await _headers(sample_run)
    seed = await sample_run["client"].put(
        f"/api/research-agent/threads/{body['thread_id']}/artifacts/wikidata-items",
        json={
            "kind": "sparql",
            "title": "Wikidata items (live claims)",
            "content": {
                "columns": ["qid", "label", "property", "value", "target_is_ours"],
                "rows": [["Q141175480", "אב הרחמים", "P17", "Q801", ""]],
            },
        },
    )
    assert seed.status_code == 200, seed.text

    wdqs = {
        "results": {"bindings": [{
            "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q141175480"},
            "itemLabel": {"value": "אב הרחמים"},
            "prop": {"type": "uri", "value": "http://www.wikidata.org/prop/direct/P17"},
            "propLabel": {"value": "country"},
            "place": {"type": "uri", "value": "http://www.wikidata.org/entity/Q801"},
            "placeLabel": {"value": "Israel"},
            "coord": {"type": "literal", "value": "Point(34.9 31.5)"},
        }]},
    }

    from unittest.mock import AsyncMock, patch

    import app.services.research_agent.tools as tools_module

    with patch.object(tools_module, "_wikidata_sparql_query", new=AsyncMock(return_value=wdqs)):
        resp = await sample_run["client"].post(
            "/api/research-agent/tools",
            headers=headers,
            json={"name": "wikidata_pack", "arguments": {}},
        )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["pack"] == "wikidata"
    # fetch 404s (no 'wikidata-uploads' artifact in this fresh thread) but
    # the pack still delivers the chart + map steps.
    assert "wikidata_fetch_items" in result["errors"]
    assert "show_link_types" in result["steps"]
    assert "show_wikidata_places" in result["steps"]


async def test_canvas_describe_lists_artifacts_and_dataset_schemas(sample_run):
    body, headers = await _headers(sample_run)
    seed = await sample_run["client"].put(
        f"/api/research-agent/threads/{body['thread_id']}/artifacts/wikidata-items",
        json={
            "kind": "sparql",
            "title": "Wikidata items (live claims)",
            "content": {
                "columns": ["qid", "label", "property", "value", "target_is_ours"],
                "rows": [["Q1", "x", "P17", "Q801", ""], ["Q2", "y", "P17", "Q38", ""]],
            },
        },
    )
    assert seed.status_code == 200, seed.text
    resp = await sample_run["client"].post(
        "/api/research-agent/tools",
        headers=headers,
        json={"name": "canvas_describe", "arguments": {}},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    entry = next(a for a in result["artifacts"] if a["artifact_key"] == "wikidata-items")
    assert entry["kind"] == "sparql"
    assert entry["row_count"] == 2
    assert "property" in entry["columns"]


async def test_export_pdf_and_download(sample_run):
    body, headers = await _headers(sample_run)
    put = await sample_run["client"].put(
        f"/api/research-agent/threads/{body['thread_id']}/artifacts/report",
        json={
            "kind": "markdown",
            "title": "Provenance report — דוח",
            "content": {"text": "# Provenance\n\nProduced in **Córdoba** (1120).\n\nHebrew: בעלי בית."},
        },
    )
    assert put.status_code == 200, put.text
    resp = await sample_run["client"].post(
        "/api/research-agent/tools",
        headers=headers,
        json={"name": "export_pdf", "arguments": {"artifact_key": "report"}},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["download_path"].endswith("format=pdf")

    download = await sample_run["client"].get(result["download_path"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/pdf")
    assert download.content[:5] == b"%PDF-"


async def test_export_pdf_unknown_artifact_is_404(sample_run):
    body, headers = await _headers(sample_run)
    resp = await sample_run["client"].post(
        "/api/research-agent/tools",
        headers=headers,
        json={"name": "export_pdf", "arguments": {"artifact_key": "nope"}},
    )
    assert resp.status_code == 404


async def test_download_link_supports_pdf(sample_run):
    body, headers = await _headers(sample_run)
    resp = await sample_run["client"].post(
        "/api/research-agent/tools",
        headers=headers,
        json={
            "name": "create_download_link",
            "arguments": {"artifact_key": "report", "format": "pdf"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["download_path"].endswith("format=pdf")