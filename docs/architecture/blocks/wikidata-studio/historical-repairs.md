# Exact historical repairs

> Up: [Wikidata Studio](README.md) · Rules W-307, W-310, W-323

`wikidata_gila_repairs.py` remains a read-only proposal builder.
`wikidata_gila_execution.py` provides the separate executor.
`backend/scripts/execute_gila_repairs.py` provides the operator command.
The command first creates a fresh plan with the normal credential and ownership guard.
The command applies a saved plan only with `--apply`.
The [retrieval rule](reference-retrievals.md) also covers references that differ only by a supported P813 date.
The operator must have authorization for the live repair scope.
The command accepts the session cookie through a hidden terminal prompt.
The session, KEK, and wiki credential remain in memory.

The plan binds the actor, project, run, authorized QIDs, and raw source hashes.
Each repair rechecks the current raw source, exact entity snapshot, and owner.
Colophon changes must match a new proposal from current source text.
Placeholder catalogue notes cannot supply that text.
The text check permits exact source text after doubled-quote normalization.
It also permits one outer quote pair, with an optional terminal period.
The check does not remove internal punctuation, uncertainty marks, or source words.
The retained claim must still match the source exactly and preserve every existing safety check.

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

Preflight records each blocked QID separately and saves only ready proposals.
Its summary keeps blocked QIDs separate from already-clean QIDs.
Apply still stops after the first non-verified result.
