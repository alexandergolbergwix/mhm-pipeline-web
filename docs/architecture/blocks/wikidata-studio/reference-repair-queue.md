# Sequential reference repairs

> Up: [Wikidata Studio](README.md) · R195–R196, R201, R203 · W-324, W-328, W-338, W-343, W-345, W-347

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

## Item reads and edits

`wikidata_reference_reader.py` reads full item data through public GET requests.
It uses `wbgetentities` first to preserve exact plan fingerprints.
A temporary failure can use `Special:EntityData` for the current revision.
The reader verifies the QID, revision, and complete item fields before it returns a snapshot.
An API refusal, a server delay, or invalid item data stops the read.
The reader has no edit credentials and sends no cookies. It cannot grant write authority.
The separate authenticated gateway retains the existing creator, lag, and revision checks.
Read success does not establish edit availability.

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

## Exact scope and restart

`queue.json` binds the ordered QIDs, run, actor, and project.
The file lock permits one process per state directory and releases after a crash.
Each item retains `plan-QID.json` and `receipt-QID.json`.
`progress.json` records the results of the current invocation.
During a confirmed maxlag pause, it records `service_delay`, the current QID, and the saved retry date.
The current delayed item does not enter the completed count.
The `retry` object retains the code, UTC observation, host, type, scaled lag, raw `queryserviceLag`, and retry delay.
The existing intent detail stores this bounded, versioned JSON evidence; no database migration is necessary.
Raw API info, arbitrary headers, and credentials remain excluded.

The backend worker uses a read-only POST to check a confirmed maxlag delay.
It checks the administrator queue limit of `maxlag=20` without credentials, a QID, or an edit payload.
See [Recovery checks](repair-retries.md#recovery-checks-w-347) for the probe interval and server delays.
A successful probe cannot shorten the saved retry date.
After that date, only a successful probe can release a confirmed maxlag retry.
The worker keeps the existing execution and rechecks the complete write boundary.
It caps its backoff for new maxlag refusals at 60 seconds before server delays and jitter.
Other refusals and the local CLI keep their retry policy. The worker keeps its finite wait limit.
The Publication tables retain the intent, retry date, and execution.

A saved plan remains immutable.
The runner reuses that plan and its execution after a confirmed API rejection.
A changed source, changed target, foreign creator, or uncertain outcome stops the queue.
A confirmed retry pause retains its plan and execution for a later invocation.
A blocked or uncertain result requires inspection before another invocation.
The [administrator job](admin-reference-repairs.md) can advance a blocked item after it verifies a successful foreground repair receipt and the exact current item.
The worker saves the recovered checkpoint before it continues with the next item.
The runner never advances to another QID after a failed or uncertain item.

The correction scope covers equivalent reference blocks only.
Every correction preserves values, qualifiers, ranks, and distinct source identities.
An item that already has clean references receives `already_clean`; this status is not a correction receipt.
Source imports, identity decisions, and the six Studio corrections retain their normal review paths.

## Files and tests

- `backend/app/publication/reference_repair_queue.py` owns validation, exact plans, locks, and sequential control.
- `backend/app/publication/reference_recovery.py` defines the read-only probe and mirror scan.
- `test_reference_recovery.py` and `test_wikidata_reference_reader.py` check the probe, snapshot scan, and GET fallback.
- `backend/scripts/run_gila_reference_queue.py` supplies server credentials, project access, current sources, and audit records.
- `backend/tests/unit/test_reference_repair_queue.py` covers restart, due dates, foreign targets, changed evidence, and uncertain writes.
The [local operator UI](local-reference-repair-ui.md) can start the existing reviewed queue. It preserves the same command, access checks, locks, and retry state. It excludes the six Studio corrections.
