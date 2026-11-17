import http.client
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.serve_gila_repair_ui import QueueControl, RepairServer


@pytest.fixture
def control(tmp_path, monkeypatch):
    root = tmp_path / "reviewed"
    state = root / "queue"
    state.mkdir(parents=True)
    (root / "candidates.json").write_text(json.dumps(["Q123"]))
    (state / "queue.json").write_text(
        json.dumps(
            {
                "run_id": "47e03437-09f2-4d6b-aea7-89fe79d76198",
                "qids": ["Q123"],
            }
        )
    )
    result = QueueControl(tmp_path, root)
    monkeypatch.setattr(result, "_spawn", Mock(side_effect=AssertionError("Unexpected launch")))
    return result


def test_start_requires_exact_scope_and_confirmation(control):
    with pytest.raises(ValueError, match="Confirm"):
        control.start(False, control.digest)
    with pytest.raises(ValueError, match="Confirm"):
        control.start(True, "another-scope")


def test_active_queue_never_starts_another_process(control, monkeypatch):
    monkeypatch.setattr(control, "_locked", lambda: True)
    assert control.start(True, control.digest)["active"] is True
    control._spawn.assert_not_called()


def test_concurrent_clicks_start_one_runner_and_restart_retains_its_marker(control, monkeypatch):
    spawn = Mock(return_value=SimpleNamespace(pid=os.getpid(), poll=lambda: None))
    monkeypatch.setattr(control, "_spawn", spawn)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: control.start(True, control.digest), range(4)))
    assert all(result["active"] for result in results)
    spawn.assert_called_once()
    restored = QueueControl(control.repo, control.queue_root)
    assert restored.status()["active"] is True


def test_changed_candidates_and_blocked_outcomes_refuse_start(control):
    (control.state / "progress.json").write_text(json.dumps({"status": "outcome_unknown"}))
    with pytest.raises(ValueError, match="inspection"):
        control.start(True, control.digest)
    (control.queue_root / "candidates.json").write_text(json.dumps(["Q456"]))
    with pytest.raises(ValueError, match="scope changed"):
        control.start(True, control.digest)


def test_status_counts_only_verified_corrections_and_retains_retry_time(control):
    (control.state / "progress.json").write_text(
        json.dumps(
            {
                "status": "service_delay",
                "results": [{"corrected": True}, {"status": "already_clean"}, {"corrected": False}],
                "current": {
                    "qid": "Q123",
                    "next_attempt_at": "2026-11-17T18:00:00Z",
                    "secret": "excluded",
                },
                "untrusted": "excluded",
            }
        )
    )
    state = control.status()
    assert state["verified"] == 1
    assert state["already_clean"] == 1
    assert state["next_attempt_at"] == "2026-11-17T18:00:00Z"
    assert state["creator_uid"] == 6935067
    assert state["maxlag"] == 5
    assert "excluded" not in json.dumps(state)


def test_fixed_command_uses_normal_queue_and_keeps_credentials_out_of_arguments(
    control, monkeypatch
):
    launch = Mock(return_value=SimpleNamespace(pid=os.getpid()))
    monkeypatch.setattr("scripts.serve_gila_repair_ui.subprocess.Popen", launch)
    QueueControl._spawn(control)
    arguments = launch.call_args.args[0]
    assert arguments[1:3] == ["-m", "scripts.run_gila_reference_queue"]
    assert "--apply" in arguments
    assert "--heroku-app" in arguments
    assert not any("foreground" in argument or "token" in argument for argument in arguments)
    assert not launch.call_args.kwargs.get("shell")


@pytest.fixture
def server(control, tmp_path):
    web = tmp_path / "www"
    web.mkdir()
    (web / "index.html").write_text("<html><header></header></html>")
    (web / "audit.json").write_text("{}")
    instance = RepairServer(0, control, web)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance
    instance.shutdown()
    instance.server_close()
    thread.join(timeout=2)


def request(server, path, *, method="GET", key=True, origin=True, body=None, host=None):
    headers = {}
    if key:
        headers["X-Repair-Key"] = server.key
    if origin:
        headers["Origin"] = server.origin
    if host:
        headers["Host"] = host
    if body is not None:
        headers["Content-Type"] = "application/json"
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        return response.status, response.read().decode()
    finally:
        connection.close()


def test_http_guards_key_origin_host_and_arbitrary_arguments(server):
    path = "/api/repairs/start"
    body = {"confirmed": True, "scope_digest": server.control.digest}
    assert request(server, path, method="POST", key=False, body=body)[0] == 403
    assert request(server, path, method="POST", origin=False, body=body)[0] == 403
    assert request(server, path, method="POST", host="other.example", body=body)[0] == 403
    assert request(server, path, method="POST", body={**body, "command": "anything"})[0] == 409
    assert request(server, "/api/repairs/status", key=False)[0] == 403
    assert request(server, "/.env")[0] == 404
    assert request(server, "/api/studio/source-choice", method="POST", body=body)[0] == 404
    server.control._spawn.assert_not_called()


def test_http_existing_process_and_passive_page_do_not_launch(server, monkeypatch):
    monkeypatch.setattr(server.control, "_locked", lambda: True)
    code, html = request(server, "/", key=False)
    assert code == 200
    assert "Start reference repairs" in html
    assert "six Studio corrections" in html
    code, body = request(
        server,
        "/api/repairs/start",
        method="POST",
        body={
            "confirmed": True,
            "scope_digest": server.control.digest,
        },
    )
    assert code == 200
    assert json.loads(body)["active"] is True
    server.control._spawn.assert_not_called()
