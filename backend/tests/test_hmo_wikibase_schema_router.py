"""Router tests for the global HMO Wikibase schema bootstrap endpoints
(Phase 3 — see dev-docs/hmo-wikibase-studio-plan.md).

Confirms: unauthenticated calls are rejected, dry-run never touches the
writer (so it needs no credentials), and a live call without server OAuth
is rejected with a 503 pointing at the deployment admin.
"""

from __future__ import annotations

import pytest

from converter.wikibase.ontology_schema_reader import OntologyClassEntry, OntologySchema


def _tiny_schema() -> OntologySchema:
    return OntologySchema(
        classes=[
            OntologyClassEntry(
                uri="http://example.org#Manuscript",
                local_name="Manuscript",
                label="Manuscript",
                description="A physical manuscript.",
            ),
        ],
        properties=[],
    )


@pytest.fixture(autouse=True)
def no_server_wikibase_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI/dev shells may export Heroku OAuth vars — tests expect unconfigured."""
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_ID", "")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_SECRET", "")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN", "")
    from app.settings import get_settings

    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def tiny_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "converter.wikibase.ontology_schema_reader.read_hmo_schema", lambda: _tiny_schema()
    )


@pytest.mark.asyncio
async def test_status_requires_auth(async_client) -> None:
    response = await async_client.get("/api/hmo-wikibase-schema/status")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_status_reports_ontology_counts(auth_user) -> None:
    _user, client = auth_user
    response = await client.get("/api/hmo-wikibase-schema/status")
    assert response.status_code == 200
    body = response.json()
    assert body["total_classes"] == 1
    assert body["total_properties"] == 0
    assert body["mapped_classes"] == 0
    assert body["wikibase_configured"] is False
    assert body["wikibase_base_url"] == "https://mhm-hmo.wikibase.cloud"
    assert body["wikibase_write_user"] == "mhm-pipeline-web"


@pytest.mark.asyncio
async def test_dry_run_bootstrap_needs_no_credentials(auth_user) -> None:
    _user, client = auth_user
    response = await client.post(
        "/api/hmo-wikibase-schema/bootstrap", json={"dry_run": True}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["created"] == 0
    assert body["would_create"] == 1
    assert len(body["entries"]) == 1
    assert body["entries"][0]["status"] == "would_create"


@pytest.mark.asyncio
async def test_live_bootstrap_without_run_id_is_rejected(auth_user) -> None:
    _user, client = auth_user
    response = await client.post(
        "/api/hmo-wikibase-schema/bootstrap", json={"dry_run": False}
    )
    assert response.status_code == 400
    assert "run_id" in response.json()["detail"]


@pytest.mark.asyncio
async def test_live_bootstrap_without_server_oauth_is_rejected(sample_run) -> None:
    client = sample_run["client"]
    response = await client.post(
        "/api/hmo-wikibase-schema/bootstrap",
        json={"dry_run": False, "run_id": str(sample_run["run_id"])},
    )
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_last_bootstrap_report_returns_entries(auth_user) -> None:
    _user, client = auth_user
    preview = await client.post(
        "/api/hmo-wikibase-schema/bootstrap", json={"dry_run": True},
    )
    assert preview.status_code == 200
    response = await client.get("/api/hmo-wikibase-schema/bootstrap/last-report")
    assert response.status_code == 200
    body = response.json()
    assert len(body["entries"]) >= 1


@pytest.mark.asyncio
async def test_cached_verdicts_empty_before_any_verification(auth_user) -> None:
    _user, client = auth_user
    preview = await client.post(
        "/api/hmo-wikibase-schema/bootstrap", json={"dry_run": True},
    )
    assert preview.status_code == 200

    response = await client.get("/api/hmo-wikibase-schema/ai-verify/cached-verdicts")
    assert response.status_code == 200
    assert response.json() == {}


@pytest.mark.asyncio
async def test_cached_verdicts_survive_across_requests(auth_user, db_session) -> None:
    """A verdict written to the inference cache (as the verify stream does
    at session end) must be visible from a fresh GET — this is what lets
    the schema panel show verdict pills again after a hard refresh."""
    _user, client = auth_user
    preview = await client.post(
        "/api/hmo-wikibase-schema/bootstrap", json={"dry_run": True},
    )
    assert preview.status_code == 200
    entry = preview.json()["entries"][0]

    from app.pipeline.hmo_schema_verify import schema_entry_local_id, schema_verdict_query_summary
    from app.pipeline.inference_cache import write_to_inference_cache
    from app.pipeline.ai_verifier import GEMINI_MODEL

    local_id = schema_entry_local_id(entry)
    await write_to_inference_cache(
        db_session,
        kind="ai_verdict",
        query_summary=schema_verdict_query_summary(
            entry, GEMINI_MODEL, evaluator="hmo_wikibase_schema",
        ),
        result={
            "verdict": {"overall": "pass", "reasoning": "looks right"},
            "judge_id": GEMINI_MODEL,
            "judged_at": "2026-07-04T00:00:00Z",
            "cache_key": "abc123",
            "evaluator": "hmo_wikibase_schema",
        },
    )
    await db_session.commit()

    response = await client.get("/api/hmo-wikibase-schema/ai-verify/cached-verdicts")
    assert response.status_code == 200
    body = response.json()
    assert local_id in body
    assert body[local_id]["overall"] == "pass"
    assert body[local_id]["reasoning"] == "looks right"


@pytest.mark.asyncio
async def test_schema_verify_actions_list(sample_run) -> None:
    client = sample_run["client"]
    response = await client.get(
        "/api/hmo-wikibase-schema/ai-verify/actions?scope_kind=selection",
    )
    assert response.status_code == 200
    body = response.json()
    assert any(a["id"] == "audit_schema_entry" for a in body)


@pytest.mark.asyncio
async def test_live_bootstrap_with_server_oauth_spawns_a_job(
    sample_run, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.pipeline import run_job_service

    monkeypatch.setattr(run_job_service, "spawn_job", lambda job_id: None)
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_ID", "test-client")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_SECRET", "test-secret")
    from app.settings import get_settings

    get_settings.cache_clear()

    client = sample_run["client"]
    response = await client.post(
        "/api/hmo-wikibase-schema/bootstrap",
        json={"dry_run": False, "run_id": str(sample_run["run_id"])},
    )
    get_settings.cache_clear()
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "hmo_schema_bootstrap"
    assert body["status"] in ("queued", "running", "succeeded", "failed")
    assert "_wikibase_bot_username" not in (body.get("params") or {})
