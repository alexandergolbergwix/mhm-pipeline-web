# Sequential reference repairs

> Up: [Wikidata Studio](README.md) · R195–R196 · W-324, W-328, W-338, W-343, W-345

`backend/scripts/run_gila_reference_queue.py` processes reviewed QIDs one at a time.
The command uses the normal Publication path and `maxlag=5`.
It waits for each saved retry date before another attempt on the same execution.
The default wait limit is 24 hours per item with `--apply`.
An exhausted limit returns `paused` and exit code 75 with its checkpoint intact.
The foreground exception remains a separate interactive action.

## Input and access

The JSON input accepts QID strings or inventory objects with a `qid` field.
Every QID must be valid and unique.
The successful build receipt supplies the actor and project context.
The command rechecks current project access and each exact MARC source.
The normal gateway requires creator user ID `6935067` before each public write.
Foreign-created items remain read-only.

The operator needs authorized server access and a configured live Publication credential.
`--heroku-app` reads the database and bot credentials into memory through the Heroku CLI.
The command keeps credentials out of arguments, output, and queue files.
Without this option, the command uses the configured server environment.

## Preview

From the repository root:

```bash
cd backend
.venv/bin/python -m scripts.run_gila_reference_queue \
  --run-id <run-uuid> \
  --build-job-id <successful-build-uuid> \
  --qids-from ../state/reviewed-reference-qids.json \
  --state-dir ../state/reference-repair-queue \
  --heroku-app mhm-pipeline-web
```

The default command creates local plans and performs no public edit.
Use the same command with `--apply` for an authorized repair scope.
Use `--wait-seconds 3600` for an explicit one-hour limit.
Use the same candidate file and state directory after exit code 75 or a process stop.
An early restart cannot shorten the saved server delay.
The local command uses POSIX file locks; run it on macOS or Linux.

## Exact scope and restart

`queue.json` binds the ordered QIDs, run, actor, and project.
A different input or actor requires a separate review.
The file lock permits one process per state directory and releases after a crash.
Each item retains `plan-QID.json` and `receipt-QID.json`.
`progress.json` records the results of the current invocation.
During a confirmed maxlag pause, it records `service_delay`, the current QID, and the saved retry date.
The current delayed item does not enter the completed count.
The `retry` object retains the code, UTC observation, host, type, scaled lag, raw `queryserviceLag`, and retry delay.
The existing intent detail stores this bounded, versioned JSON evidence; no database migration is necessary.
Raw API info, arbitrary headers, and credentials remain excluded.
A later successful attempt clears the old retry evidence.

The backend worker uses a read-only POST to check a confirmed maxlag delay.
It uses `maxlag=5` without credentials, a QID, or an edit payload.
The same worker waits at least one minute between probes.
It honors a longer `Retry-After` during that run.
A successful probe cannot shorten the saved retry date.
After that date, only a successful probe can release a confirmed maxlag retry.
The worker keeps the existing execution and rechecks the complete write boundary.
It caps its backoff for new maxlag refusals at 60 seconds before server delays and jitter.
Other refusals and the local CLI keep their prior retry policy.
The worker stops after its existing finite wait limit.
The existing Publication tables retain the durable intent, retry date, and execution.
The command creates no second retry store.

A saved plan remains immutable.
The runner reuses that plan and its execution after a confirmed API rejection.
A changed source, changed target, foreign creator, or uncertain outcome stops the queue.
A confirmed retry pause retains its plan and execution for a later invocation.
A blocked or uncertain result requires inspection before another invocation.
The runner never advances to another QID after a failed or uncertain item.

The correction scope covers equivalent reference blocks only.
Every correction preserves values, qualifiers, ranks, and distinct source identities.
Success requires a complete public entity match against the expected result.
An item that already has clean references receives `already_clean`; this status is not a correction receipt.
Source imports, identity decisions, and the six Studio corrections retain their normal review paths.

## Files and tests

- `backend/app/publication/reference_repair_queue.py` owns validation, exact plans, locks, and sequential control.
- `backend/app/publication/reference_recovery.py` defines the read-only probe and mirror scan.
- `backend/tests/unit/test_reference_recovery.py` checks the probe and snapshot classifications.
- `backend/tests/unit/test_publication_historical_repairs.py` checks saved dates, probe delays, and replay.
- `backend/scripts/run_gila_reference_queue.py` supplies server credentials, project access, current sources, and audit records.
- `backend/tests/unit/test_reference_repair_queue.py` covers restart, due dates, foreign targets, changed evidence, and uncertain writes.
- Existing historical-repair and creator tests retain the transport and ownership boundaries.

The [local operator UI](local-reference-repair-ui.md) can start the existing reviewed queue. It preserves the same command, access checks, locks, and retry state. It excludes the six Studio corrections.
