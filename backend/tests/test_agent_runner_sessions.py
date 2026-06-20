"""Session replay helpers for AI verification."""

from __future__ import annotations

import json
from pathlib import Path

from app.pipeline.agent_runner import (
    _verdict_storage_key,
    _verdicts_from_trace,
    persist_session_event,
    read_session,
    AgentEvent,
)


def test_verdict_storage_key_does_not_collapse_same_record(tmp_path: Path, monkeypatch):
  monkeypatch.setenv("EVAL_AGENT_ROOT", str(tmp_path))
  (tmp_path / "eval_agent").mkdir()
  (tmp_path / "eval_agent" / "cli.py").write_text("", encoding="utf-8")

  a = {
    "record_id": "990001",
    "evaluator_id": "authority",
    "candidate": {"name": "Author A", "role": "author"},
  }
  b = {
    "record_id": "990001",
    "evaluator_id": "authority",
    "candidate": {"name": "Scribe B", "role": "scribe"},
  }
  assert _verdict_storage_key(a) != _verdict_storage_key(b)


def test_read_session_prefers_trace_verdicts(tmp_path: Path, monkeypatch):
  monkeypatch.setenv("EVAL_AGENT_ROOT", str(tmp_path))
  (tmp_path / "eval_agent").mkdir()
  (tmp_path / "eval_agent" / "cli.py").write_text("", encoding="utf-8")

  run_id = "run-1"
  session_id = "sess-1"
  session_dir = (
    tmp_path / "state" / "ai-verify-sessions" / run_id / "sessions" / session_id
  )
  session_dir.mkdir(parents=True)

  for i in range(3):
    persist_session_event(
      session_dir,
      AgentEvent(
        type="agent.verdict",
        payload={
          "record_id": "990001",
          "evaluator_id": "authority",
          "candidate": {"_match_id": f"mid-{i}", "name": f"Entity {i}"},
          "verdict": {"overall": "pass"},
        },
      ),
    )

  # Stale shared results.jsonl with only one row — must not win over trace.
  runs = tmp_path / "state" / "ai-verify-sessions" / run_id / "runs" / "20260101"
  runs.mkdir(parents=True)
  (runs / "results.jsonl").write_text(
    json.dumps(
      {
        "record_id": "990001",
        "candidate": {"name": "Stale"},
        "verdict": {"overall": "fail"},
      }
    )
    + "\n",
    encoding="utf-8",
  )

  data = read_session(run_id, session_id)
  assert data is not None
  assert len(data["verdicts"]) == 3
  assert all(v["candidate"]["_match_id"].startswith("mid-") for v in data["verdicts"])


def test_verdicts_from_trace_dedupes_last_write(tmp_path: Path):
  events = [
    {
      "type": "agent.verdict",
      "record_id": "990001",
      "candidate": {"_match_id": "same", "name": "A"},
      "verdict": {"overall": "fail"},
    },
    {
      "type": "agent.verdict",
      "record_id": "990001",
      "candidate": {"_match_id": "same", "name": "A"},
      "verdict": {"overall": "pass"},
    },
  ]
  out = _verdicts_from_trace(events)
  assert len(out) == 1
  assert out[0]["verdict"]["overall"] == "pass"


def test_verdict_storage_key_uses_local_id():
  v = {
    "record_id": "990001",
    "candidate": {"_local_id": "manuscript::990001801390205171", "labels": {}},
  }
  assert _verdict_storage_key(v) == "manuscript::990001801390205171"


def test_read_verify_session_prefers_trace_over_shared_results(
  tmp_path: Path, monkeypatch,
):
  monkeypatch.setenv("EVAL_AGENT_STATE_DIR", str(tmp_path / "verify-state"))
  channel = "wikidata-verify-sessions"
  run_id = "run-wd"
  session_id = "sess-wd"
  session_dir = tmp_path / "verify-state" / channel / run_id / "sessions" / session_id
  session_dir.mkdir(parents=True)

  for local_id in ("manuscript::a", "manuscript::b"):
    persist_session_event(
      session_dir,
      AgentEvent(
        type="agent.verdict",
        payload={
          "record_id": "990001",
          "evaluator_id": "wikidata_item",
          "candidate": {"_local_id": local_id, "labels": {"en": local_id}},
          "verdict": {"overall": "pass"},
        },
      ),
    )

  state_dir = tmp_path / "verify-state" / channel / run_id
  runs = state_dir / "runs" / "20260101"
  runs.mkdir(parents=True)
  (runs / "results.jsonl").write_text(
    json.dumps(
      {
        "record_id": "990001",
        "candidate": {"_local_id": "manuscript::stale"},
        "verdict": {"overall": "fail"},
      }
    )
    + "\n",
    encoding="utf-8",
  )

  from app.pipeline.agent_runner import read_verify_session

  data = read_verify_session(channel, run_id, session_id)
  assert data is not None
  assert len(data["verdicts"]) == 2
  ids = {v["candidate"]["_local_id"] for v in data["verdicts"]}
  assert ids == {"manuscript::a", "manuscript::b"}


def test_resolve_verify_state_dir_uses_tmp_on_dyno(monkeypatch, tmp_path: Path):
  from app.pipeline.agent_runner import resolve_verify_state_dir

  monkeypatch.setenv("DYNO", "web.1")
  monkeypatch.delenv("EVAL_AGENT_STATE_DIR", raising=False)
  p = resolve_verify_state_dir("wikidata-verify-sessions", "run-1")
  assert str(p).startswith("/tmp/mhm-eval-agent-state")
