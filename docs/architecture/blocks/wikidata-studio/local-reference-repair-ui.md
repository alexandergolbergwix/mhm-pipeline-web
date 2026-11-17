# Local reference-repair UI

The local UI controls the existing reviewed reference queue.
It does not run the six Studio corrections, source imports, identity decisions, or foreground repairs.
The normal reference queue retains its Publication intents, creator checks, and retry dates.
The separate FastAPI repair-plan page remains read-only.

## Start the local UI

Use the existing saved audit viewer and reviewed queue files.
From the repository root:

```bash
backend/.venv/bin/python -m backend.scripts.serve_gila_repair_ui \
  --web-root state/gila-local-viewer-2026-11-17/www
```

Open `http://127.0.0.1:5173/`.
The process binds only to `127.0.0.1`.
It requires the existing queue manifest and matching candidate file.
The script fixes the run, successful build, and existing normal queue command.
It does not accept a replacement command or QID list from HTTP.

## Operator controls

The page shows the current queue process and its last checkpoint.
The counter includes only receipts with `corrected=true`.
It reports already-clean items separately.
A service delay shows the current QID and saved retry time.
The saved audit table retains its original audit date.
It does not update from queue progress or prove current public completion.

When the queue is inactive, select the reference-scope checkbox.
Select **Start reference repairs**.
The control starts or resumes the exact existing queue with `--apply`.
The child command checks current project access and the exact MARC sources.
It verifies creator UID 6935067 before each public write.
Foreign-created items remain read-only relation targets.
The automatic path retains `maxlag=5`.
An active queue disables the button.
The backend also refuses duplicate starts.
A blocked or uncertain checkpoint requires inspection before another start.
An incomplete HTTP response does not trigger an automatic resend.

## Local endpoints and key

- `GET /api/repairs/status`: selected checkpoint fields and current process state.
- `POST /api/repairs/start`: exact scope digest and `confirmed=true` only.

Both endpoints require the per-process `X-Repair-Key` header.
The local page receives this key from the local server.
The key stays out of query strings, arguments, and logs.
Each server restart creates another key.
The POST also requires the exact local `Origin`, Host, JSON content type, and a bounded body.
The server serves only its page, selected script, and saved audit JSON.
It does not serve the repository or environment files.
The loopback key does not replace the downstream project and creator checks.
It does not grant a Studio exception or remove an access block.

The launcher retains a local child PID marker across a UI restart.
The existing queue lock prevents two queue workers.
A PID that still exists conservatively blocks another start.
Inspect a reused PID before a manual recovery.
The helper adds no retry store or background job kind.
The existing queue retains durable Publication state.

## Files and verification

- `backend/scripts/serve_gila_repair_ui.py`: loopback server, key, scope, and process guards.
- `backend/scripts/gila_repair_controls.js`: checkbox, one POST, status polls, and error display.
- `backend/tests/unit/test_gila_repair_ui.py`: local HTTP guards, exact scope, concurrency, and duplicate tests.
- `frontend/e2e/gila-local-repair-ui.spec.ts`: mocked start, active queue, and refused-start browser tests.

For the browser tests, set `GILA_LOCAL_UI_URL` and `E2E_BASE_URL` to the local UI URL.
Run `yarn test:e2e gila-local-repair-ui.spec.ts --workers=1` from `frontend`.
The tests mock all local API requests and do not start a real repair.

The [administrator backend job](admin-reference-repairs.md) replaces this local operator path for production. A `backend-handoff.json` marker forbids another local start after transfer.
