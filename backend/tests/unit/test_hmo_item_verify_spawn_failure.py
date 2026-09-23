"""A verify pre-spawn failure must surface its real cause (Rule W-258).

Run f4e8e4b3 (2026-09-22): the tier-1 judge `typesafe/jev-1.13.0` needs
`TYPESAFE_API_KEY`; `ensure_tier1_credentials` raised inside
`spawn_eval_agent_run` before any subprocess existed, the stream's
`finally` masked it as "eval-agent stopped after 0 of N verdicts…", and the
job finalized `succeeded` with "Verification complete". The real cause must
reach `runner_error` / `session.end`.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.pipeline import hmo_item_actions
from app.pipeline.hmo_item_verify import hmo_item_verify_event_stream
from app.pipeline.judge_models import Tier1CredentialsError


@pytest.mark.asyncio
async def test_spawn_failure_message_reaches_session_end() -> None:
    action = hmo_item_actions.get_action("audit_hmo_wikibase_item")
    assert action is not None

    with patch(
        "app.pipeline.hmo_item_verify.spawn_eval_agent_run",
        side_effect=Tier1CredentialsError(
            "Jev 1.13 (TypeSafe) is not configured on this server "
            "(missing env TYPESAFE_API_KEY).",
        ),
    ):
        events = [
            ev
            async for ev in hmo_item_verify_event_stream(
                run_id=str(uuid.uuid4()),
                session_id="s-w258",
                action=action,
                items=[{"local_id": "QDraft_A", "source_uri": "http://x#A"}],
                uncached_items=[{"local_id": "QDraft_A", "source_uri": "http://x#A"}],
                pre_cached=[],
                marc_records=[],
                api_key="",
                override_cache=True,
                tier_model="typesafe/jev-1.13.0",
            )
        ]

    types = [ev.type for ev in events]
    assert types == ["session.start", "session.end"]
    end = events[-1].payload
    assert "TYPESAFE_API_KEY" in str(end.get("runner_error") or "")
    assert end.get("outcome") == "partial"