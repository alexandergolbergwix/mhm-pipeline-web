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