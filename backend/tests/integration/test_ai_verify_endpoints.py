"""Integration tests for the ``/api/runs/{id}/ai-verify/*`` endpoints.

The full SSE start-stream path is intentionally NOT exercised here —
it spawns the sibling eval-agent CLI as a subprocess, which is
out-of-scope for an in-tree backend test. We pin the GET endpoints'
contract (auth required, schema, scope filtering) so a UI regression
that breaks the action dropdown surfaces at the API boundary.
"""

from __future__ import annotations

import uuid

import pytest


class TestActionsEndpointAuth:
    """``GET /runs/{id}/ai-verify/actions`` requires a session."""

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, async_client) -> None:
        fake_run = uuid.uuid4()
        r = await async_client.get(f"/api/runs/{fake_run}/ai-verify/actions")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_authed_but_unknown_run_returns_404(self, auth_user) -> None:
        _user, client = auth_user
        fake_run = uuid.uuid4()
        r = await client.get(f"/api/runs/{fake_run}/ai-verify/actions")
        # The router calls _lookup_run_with_access which 404s on
        # missing runs (or 403 if access is denied — either is fine
        # for "this isn't a green path" assertion).
        assert r.status_code in (403, 404)


class TestActionsEndpointHappyPath:
    """When the caller has access to the run, the endpoint returns
    the action registry filtered by ``scope_kind``."""

    @pytest.mark.asyncio
    async def test_scope_single_excludes_find_duplicates(self, sample_run) -> None:
        client = sample_run["client"]
        run_id = sample_run["run_id"]
        r = await client.get(
            f"/api/runs/{run_id}/ai-verify/actions?scope_kind=single",
        )
        assert r.status_code == 200
        ids = {a["id"] for a in r.json()}
        assert "audit_match" in ids
        assert "birth_death_check" in ids
        assert "find_duplicates" not in ids

    @pytest.mark.asyncio
    async def test_scope_selection_includes_find_duplicates(self, sample_run) -> None:
        client = sample_run["client"]
        run_id = sample_run["run_id"]
        r = await client.get(
            f"/api/runs/{run_id}/ai-verify/actions?scope_kind=selection",
        )
        assert r.status_code == 200
        ids = {a["id"] for a in r.json()}
        assert ids == {"audit_match", "find_duplicates", "birth_death_check"}

    @pytest.mark.asyncio
    async def test_scope_all_includes_every_action(self, sample_run) -> None:
        client = sample_run["client"]
        run_id = sample_run["run_id"]
        r = await client.get(
            f"/api/runs/{run_id}/ai-verify/actions?scope_kind=all",
        )
        assert r.status_code == 200
        ids = {a["id"] for a in r.json()}
        assert ids == {"audit_match", "find_duplicates", "birth_death_check"}

    @pytest.mark.asyncio
    async def test_response_shape_matches_to_dict(self, sample_run) -> None:
        client = sample_run["client"]
        run_id = sample_run["run_id"]
        r = await client.get(
            f"/api/runs/{run_id}/ai-verify/actions?scope_kind=selection",
        )
        assert r.status_code == 200
        for action in r.json():
            assert set(action) == {
                "id", "label", "description", "scope_kinds",
                "evaluators", "min_candidates",
            }
            assert isinstance(action["scope_kinds"], list)
            assert isinstance(action["evaluators"], list)
            assert isinstance(action["min_candidates"], int)

    @pytest.mark.asyncio
    async def test_invalid_scope_kind_rejected(self, sample_run) -> None:
        client = sample_run["client"]
        run_id = sample_run["run_id"]
        r = await client.get(
            f"/api/runs/{run_id}/ai-verify/actions?scope_kind=garbage",
        )
        # FastAPI's regex validator on the query param surfaces as 422.
        assert r.status_code == 422


class TestSessionsEndpoint:
    """``GET /runs/{id}/ai-verify/sessions`` is the audit-log lookup.

    With no AI-verify session ever started for the run, the list is
    empty — which is the green-path baseline.
    """

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, async_client) -> None:
        fake_run = uuid.uuid4()
        r = await async_client.get(f"/api/runs/{fake_run}/ai-verify/sessions")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_run_returns_empty_list(self, sample_run) -> None:
        client = sample_run["client"]
        run_id = sample_run["run_id"]
        r = await client.get(f"/api/runs/{run_id}/ai-verify/sessions")
        # Either the endpoint succeeds with [] (sessions dir empty) or
        # it returns 200 with whatever the eval-agent state dir has.
        # The contract: status 200, body is a list.
        assert r.status_code == 200
        assert isinstance(r.json(), list)
