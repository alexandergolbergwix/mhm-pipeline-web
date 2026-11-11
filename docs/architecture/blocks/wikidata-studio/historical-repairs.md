# Exact historical repairs

> Up: [Wikidata Studio](README.md) · Rule W-307

`wikidata_gila_repairs.py` remains a read-only proposal builder.
`wikidata_gila_execution.py` provides the separate executor.
`backend/scripts/execute_gila_repairs.py` provides the operator command.
The command first creates a fresh plan with the normal credential and ownership guard.
The command applies a saved plan only with `--apply`.
The operator must have authorization for the live repair scope.
The command accepts the session cookie through a hidden terminal prompt.
The session, KEK, and wiki credential remain in memory.

The plan binds the actor, project, run, authorized QIDs, and raw source hashes.
Each repair rechecks the current raw source, exact entity snapshot, and owner.
Colophon changes must match a new proposal from current source text.
Placeholder catalogue notes cannot supply that text.

The database audit stores the exact proposal and digest before the POST.
The audit uses channel `wikidata_repair` and operation `repair_intent`.
The selected target receives one POST with its exact base revision.
The transport does not retry a write or follow a redirect.
The executor compares the complete returned entity after the write.
Only an exact verified result sets `corrected: true`.

The batch stops after a blocked or uncertain result.
A previous pending or uncertain audit prevents a resend.
The result operations are `verified`, `blocked`, or `uncertain`.
The local receipt duplicates this durable evidence.
The executor does not restore a missing ledger mapping or resolve a mixed work.
