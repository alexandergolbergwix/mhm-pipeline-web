"""Serve a loopback UI for an existing reviewed reference-repair queue."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit


class Runner(Protocol):
    pid: int

    def poll(self) -> int | None: ...


class QueueControl:
    def __init__(self, repo: Path, queue_root: Path):
        self.repo = repo.resolve()
        self.queue_root = queue_root.resolve()
        self.state = self.queue_root / "queue"
        self.manifest = json.loads((self.state / "queue.json").read_text())
        self.digest = self._scope_digest()
        self._mutex = threading.Lock()
        self._runner: Runner | None = None
        self._validate_scope()

    def _scope_digest(self) -> str:
        files = [self.state / "queue.json", self.queue_root / "candidates.json"]
        return hashlib.sha256(b"\0".join(path.read_bytes() for path in files)).hexdigest()

    def _validate_scope(self) -> None:
        if self._scope_digest() != self.digest:
            raise ValueError("The reviewed queue scope changed. Review it before another start.")
        candidates = json.loads((self.queue_root / "candidates.json").read_text())
        qids = [row if isinstance(row, str) else row["qid"] for row in candidates]
        if qids != self.manifest.get("qids") or not qids:
            raise ValueError("The candidate file does not match the reviewed queue.")
        if self.manifest.get("run_id") != "47e03437-09f2-4d6b-aea7-89fe79d76198":
            raise ValueError("The queue belongs to another run.")

    def _locked(self) -> bool:
        with (self.state / "queue.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
        return False

    def _runner_present(self) -> bool:
        if self._runner is not None and self._runner.poll() is None:
            return True
        marker = self.state / "ui-runner.json"
        if not marker.exists():
            return False
        pid = json.loads(marker.read_text()).get("pid")
        if not isinstance(pid, int) or pid <= 0:
            raise ValueError("The local runner marker requires inspection.")
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def status(self) -> dict[str, object]:
        self._validate_scope()
        path = self.state / "progress.json"
        progress = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(progress, dict):
            raise ValueError("The queue checkpoint requires inspection.")
        results = progress.get("results", [])
        current = progress.get("current") or {}
        if (
            not isinstance(results, list)
            or not all(isinstance(row, dict) for row in results)
            or not isinstance(current, dict)
        ):
            raise ValueError("The queue checkpoint requires inspection.")
        active = self._locked() or self._runner_present()
        return {
            "active": active,
            "status": progress.get("status", "ready"),
            "verified": sum(row.get("corrected") is True for row in results),
            "already_clean": sum(row.get("status") == "already_clean" for row in results),
            "total": len(self.manifest["qids"]),
            "current_qid": current.get("qid"),
            "next_attempt_at": current.get("next_attempt_at"),
            "scope_digest": self.digest,
            "scope": "reviewed reference repairs only",
            "creator_uid": 6935067,
            "maxlag": 5,
        }

    def _spawn(self) -> Runner:
        command = [
            sys.executable,
            "-m",
            "scripts.run_gila_reference_queue",
            "--run-id",
            self.manifest["run_id"],
            "--build-job-id",
            "140b0e5d-7a54-49d9-a3fb-808ce376f3cd",
            "--qids-from",
            str(self.queue_root / "candidates.json"),
            "--state-dir",
            str(self.state),
            "--heroku-app",
            "mhm-pipeline-web",
            "--apply",
        ]
        log_path = self.state / "ui-runner.log"
        descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "ab") as log:
            return subprocess.Popen(  # noqa: S603 — fixed queue command; no shell or HTTP arguments
                command,
                cwd=self.repo / "backend",
                stdout=log,
                stderr=log,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )

    def start(self, confirmed: object, digest: object) -> dict[str, object]:
        with self._mutex:
            if (self.state / "backend-handoff.json").exists():
                raise ValueError("The queue moved to the administrator backend controls.")
            if confirmed is not True or digest != self.digest:
                raise ValueError("Confirm the exact reviewed reference scope first.")
            with (self.state / "ui-start.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                snapshot = self.status()
                if snapshot["active"] or snapshot["status"] == "complete":
                    return snapshot
                if snapshot["status"] in {"blocked", "outcome_unknown"}:
                    raise ValueError("The blocked queue requires inspection before another start.")
                self._runner = self._spawn()
                marker = self.state / "ui-runner.json"
                marker.write_text(json.dumps({"pid": self._runner.pid}))
                return {**snapshot, "active": True, "status": "start_requested"}


class RepairServer(ThreadingHTTPServer):
    def __init__(self, port: int, control: QueueControl, web_root: Path):
        self.control = control
        self.web_root = web_root.resolve()
        self.key = secrets.token_hex(32)
        super().__init__(("127.0.0.1", port), RepairHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class RepairHandler(BaseHTTPRequestHandler):
    server: RepairServer

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, body: dict[str, object]) -> None:
        self._send(status, json.dumps(body).encode(), "application/json")

    def _authorized(self) -> bool:
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            self._json(403, {"error": "Use the local viewer address."})
            return False
        if not hmac.compare_digest(self.headers.get("X-Repair-Key", ""), self.server.key):
            self._json(403, {"error": "The local operator key is required."})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802 — stdlib handler API
        path = urlsplit(self.path).path
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            self._json(403, {"error": "Use the local viewer address."})
            return
        if path == "/api/repairs/status":
            if self._authorized():
                try:
                    self._json(200, self.server.control.status())
                except (ValueError, OSError, KeyError, TypeError):
                    self._json(409, {"error": "The local queue state requires inspection."})
            return
        if path in {"/", "/index.html"}:
            html = (self.server.web_root / "index.html").read_text()
            html = html.replace(
                "This viewer does not apply repairs or query production.",
                "The table shows saved results. "
                "The separate control uses the reviewed reference queue.",
            )
            html = html.replace(
                "LOCAL · READ ONLY · SAVED AUDIT", "LOCAL · SAVED AUDIT · GUARDED REPAIRS"
            )
            control = """<section id="repair-control"><h2>Reference repairs</h2>
<p>This control starts or resumes the existing reviewed queue.
It preserves maxlag=5 and creator UID 6935067 checks.
It does not apply the six Studio corrections, identity decisions, or source imports.</p>
<p id="repair-status" role="status">Please wait for the queue status.</p>
<label><input type="checkbox" id="repair-consent" style="min-width:0;flex:none">
I authorize the reviewed reference repairs only.</label>
<p><button id="start-repairs" disabled>Start reference repairs</button></p></section>"""
            html = html.replace("</header>", "</header>" + control, 1)
            html = html.replace(
                "</html>",
                f"<script>const REPAIR_KEY={json.dumps(self.server.key)};</script>"
                '<script src="/repair-controls.js"></script></html>',
            )
            self._send(200, html.encode(), "text/html; charset=utf-8")
        elif path == "/repair-controls.js":
            script = Path(__file__).with_name("gila_repair_controls.js").read_bytes()
            self._send(200, script, "text/javascript; charset=utf-8")
        elif path == "/audit.json":
            self._send(200, (self.server.web_root / "audit.json").read_bytes(), "application/json")
        else:
            self._json(404, {"error": "Not found."})

    def do_POST(self) -> None:  # noqa: N802 — stdlib handler API
        if not self._authorized():
            return
        if self.path != "/api/repairs/start":
            self._json(404, {"error": "Not found."})
            return
        if self.headers.get("Origin") != self.server.origin:
            self._json(403, {"error": "Use the local viewer controls."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 1024 or self.headers.get("Content-Type") != "application/json":
                raise ValueError("Invalid request size or type.")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict) or set(body) != {"confirmed", "scope_digest"}:
                raise ValueError("Unexpected request fields.")
            result = self.server.control.start(body["confirmed"], body["scope_digest"])
            self._json(200, result)
        except (ValueError, OSError, KeyError, TypeError):
            self._json(
                409, {"error": "The queue cannot start. Check consent, scope, and queue state."}
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--web-root", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    control = QueueControl(repo, repo / "state/gila-automatic-reference-2026-11-17")
    server = RepairServer(args.port, control, args.web_root)
    print(f"Local repair UI: {server.origin}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
