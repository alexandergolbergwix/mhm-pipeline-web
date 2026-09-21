"""Jev-primary rollout step 6/8: the typesafe tier model passes tier-model
validation and ``TYPESAFE_API_KEY`` reaches the eval-agent subprocess env."""

from __future__ import annotations

import asyncio
import sys
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.models.run_job import JOB_KIND_NER_VERIFY
from app.pipeline import agent_runner
from app.pipeline.judge_models import (
    resolve_tier1_model,
    tier1_api_key_for_spec,
)
from app.pipeline.run_job_params import prepare_job_params


def _auth_db():
    auth = AsyncMock()
    auth.user.id = uuid.uuid4()
    auth.kek = b"x" * 32
    return auth, AsyncMock()


def test_registry_lists_typesafe_model() -> None:
    spec = resolve_tier1_model("typesafe/jev-1.13.0")
    assert spec.provider == "typesafe"
    assert spec.label == "Jev 1.13 (TypeSafe)"
    assert spec.api_key_env == "TYPESAFE_API_KEY"
    assert spec.supports_agentic is False


def test_typesafe_credentials_resolve_from_env(monkeypatch) -> None:
    spec = resolve_tier1_model("typesafe/jev-1.13.0")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert tier1_api_key_for_spec(spec, gemini_key=None) is None
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    assert tier1_api_key_for_spec(spec, gemini_key=None) == "ts-key"


@pytest.mark.asyncio
async def test_typesafe_tier_model_accepted(monkeypatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    auth, db = _auth_db()
    with patch(
        "app.pipeline.run_job_params._validate_verify_params",
        new=AsyncMock(),
    ), patch(
        "app.pipeline.run_job_params._resolve_gemini_key",
        new=AsyncMock(return_value=None),
    ):
        merged = await prepare_job_params(
            db, auth,
            run_id=uuid.uuid4(),
            kind=JOB_KIND_NER_VERIFY,
            params={
                "action_id": "audit_ner_extraction",
                "tier_model": "typesafe/jev-1.13.0",
            },
        )
    assert merged["tier_model"] == "typesafe/jev-1.13.0"


@pytest.mark.asyncio
async def test_typesafe_tier_model_requires_key(monkeypatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    auth, db = _auth_db()
    with patch(
        "app.pipeline.run_job_params._validate_verify_params",
        new=AsyncMock(),
    ), patch(
        "app.pipeline.run_job_params._resolve_gemini_key",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(HTTPException) as exc:
            await prepare_job_params(
                db, auth,
                run_id=uuid.uuid4(),
                kind=JOB_KIND_NER_VERIFY,
                params={
                    "action_id": "audit_ner_extraction",
                    "tier_model": "typesafe/jev-1.13.0",
                },
            )
    assert exc.value.status_code == 400
    assert "TYPESAFE_API_KEY" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_typesafe_api_key_reaches_subprocess_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-subprocess-key")
    package = tmp_path / "eval_agent"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "cli.py").write_text(
        "import json, os\n"
        "print('[TRACE] ' + json.dumps({'type': 'agent.verdict', "
        "'key': os.environ.get('TYPESAFE_API_KEY')}), flush=True)\n"
        "print('[STEP] complete', flush=True)\n"
    )
    monkeypatch.setattr(agent_runner, "_python_for", lambda root: sys.executable)
    monkeypatch.setattr(agent_runner, "_SUBPROCESS_IDLE_TIMEOUT_S", 2)

    async def run():
        return [ev async for ev in agent_runner.spawn_eval_agent_run(
            pipeline_output=tmp_path, evaluators=("person_ner",),
            eval_agent_root=tmp_path, tier_model="typesafe/jev-1.13.0",
            api_key=None,
        )]

    events = await asyncio.wait_for(run(), timeout=5)
    verdict = next(e for e in events if e.type == "agent.verdict")
    assert verdict.payload["key"] == "ts-subprocess-key"
    assert events[-1].type == "runner.exit"
    assert events[-1].payload["return_code"] == 0
