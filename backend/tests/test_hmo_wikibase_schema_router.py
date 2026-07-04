"""Router tests for the global HMO Wikibase schema bootstrap endpoints
(Phase 3 — see dev-docs/hmo-wikibase-studio-plan.md).

Confirms: unauthenticated calls are rejected, dry-run never touches the
writer (so it needs no credentials), and a live call without stored bot
credentials is rejected with a friendly 400 pointing at Settings.
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
    assert body["bot_username_set"] is False
    assert body["bot_password_set"] is False


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
async def test_live_bootstrap_without_credentials_is_rejected(sample_run) -> None:
    client = sample_run["client"]
    response = await client.post(
        "/api/hmo-wikibase-schema/bootstrap",
        json={"dry_run": False, "run_id": str(sample_run["run_id"])},
    )
    assert response.status_code == 400
    assert "wikibase_cloud_bot_username" in response.json()["detail"]
    assert "wikibase_cloud_bot_password" in response.json()["detail"]


@pytest.mark.asyncio
async def test_live_bootstrap_with_credentials_spawns_a_job(
    sample_run, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This only checks the HTTP-response contract (job created, plaintext
    # creds never echoed back) — never actually run the background task,
    # which would make a real network call to mhm-hmo.wikibase.cloud.
    from app.pipeline import run_job_service

    monkeypatch.setattr(run_job_service, "spawn_job", lambda job_id: None)

    client = sample_run["client"]
    from app.services.wikibase_credentials import WikibaseCredentialVerifyResult

    # Store via the real Settings endpoint so the wrapped secret's KEK
    # matches what the session presents (prepare_job_params unwraps it).
    monkeypatch.setattr(
        "app.routers.api_keys.verify_wikibase_bot_credentials_sync",
        lambda _creds: WikibaseCredentialVerifyResult(
            ok=True, message="Login successful", login_name="bot@hmo",
        ),
    )
    for key_name, value in (
        ("wikibase_cloud_bot_username", "bot@hmo"),
        ("wikibase_cloud_bot_password", "s3cret"),
    ):
        resp = await client.put(f"/api/me/api-keys/{key_name}", json={"value": value})
        assert resp.status_code == 200, resp.text

    response = await client.post(
        "/api/hmo-wikibase-schema/bootstrap",
        json={"dry_run": False, "run_id": str(sample_run["run_id"])},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "hmo_schema_bootstrap"
    assert body["status"] in ("queued", "running", "succeeded", "failed")
    assert "wikibase_cloud_bot_username" not in (body.get("params") or {})
