# Wikidata Studio - Owner-Only Upload

> Up: [Wikidata Studio](README.md) | Rule W-303

## API And Scope

`POST /api/runs/{run_id}/jobs` accepts `kind: "wikidata_upload"` with
`params.owner_only: true`. The convenience
`POST /api/runs/{run_id}/wikidata-studio/upload` accepts `owner_only=true`.
The job API requires booleans for the flag and the related boolean options.
If supplied, `source` must be exactly `canonical` or `legacy`; an invalid value
returns HTTP 400 before credential lookup. The worker also validates `source`.
The alias accepts each owner-only boolean query option at most once, with
`true` or `false`. Optional `local_ids` must be a non-empty list of non-empty
strings. The job applies `source`, `approved_only`, `item_approved_only`,
`ai_verified_only`, `local_ids`, and `update_existing` filters together.
`update_existing=false` excludes selected existing targets.

The supported targets are `dry_run` and `live`; `test` is rejected. Both
require the saved live Wikidata credential. The worker authenticates even for
a dry-run. This path accepts live bot passwords or JWT credentials, not the
other credential formats accepted by the general uploader.

## Target And Write Guards

The worker builds native items without probe adoption and reads the live global
ledger. It binds targets before the optional item-scope filters, so duplicate
identity checks use the full native build. An eligible target must satisfy all
of these conditions:

- The item is a manuscript with a unique `QDraft_MS_<digits>` or
  `marc:<digits>` local ID. The control number has at least five digits.
- The canonical record set is exactly that control number, which belongs to
  the selected run. The native item must also contain exactly one record
  string; after whitespace and surrounding quotes are stripped, it must match
  the control number. Works and persons cannot qualify.
- The native item must contain exactly one `P3959` statement with type
  `external-id`, rank `normal` or `preferred`, and a value equal to that
  control number. Missing, multiple, malformed, deprecated, or different
  values or ranks block the item.
- The exact `wikidata:marc:<local_id>` ledger entry contains a valid QID.
  An existing built QID must agree. Duplicate local IDs or QIDs block selection.
  A ledger read error stops the job; an override alone cannot select a target.
- The item has no `__LOCAL:` value in any statement, qualifier, or reference.
  The worker blocks the whole item before resolution or deferred-link partition.
- The item has no `P1574=Q234460` generic text claim. Such a claim can hide
  unresolved source evidence, so this mode blocks it.
- A live, authenticated, endpoint-pinned session confirms the exact existing
  non-redirect item, its positive revision, and its visible first author as the
  authenticated account. The remote item must contain exactly one `P3959`
  statement with rank `normal` or `preferred`, a value snak, `external-id`
  datatype, and string datavalue equal to the same control number. Missing,
  multiple, malformed, deprecated, or different remote values or ranks block
  the item. The usual quality and ownership guards apply.

The preview checks the remote target again after preparation. Before a live
write, the uploader rechecks the local record and `P3959`, the remote target,
its `P3959` and creator, and the revision. It compares the revision with the
fetched entity and sends `baserevid`. The preview also builds the proposed
entity and applies the claim guards. See [claim safety](owner-only-claim-safety.md)
for replacement rules and limits. The uploader does not create or adopt an
item and ignores foreign modification approvals. It does not
write ledger mappings. Live outcomes can still persist audit and upload status.
The worker processes one item at a time and rejects shard state. Modal calls
the ordinary job runner without shard fan-out for `owner_only`.

The live job persists an audit for each item outcome with strict error handling.
If audit persistence fails, the job retains the item outcome, marks its
`audit_status` as `failed`, sets `aborted_audit`, and stops before the next
item. The job fails even if the remote update already succeeded. The audit
failure does not roll back the Wikidata write. Verify the remote outcome
before a retry. If the worker exits after a remote write but before audit or
job persistence, the audit row and outcome can both be absent. Verify the
remote state before a retry. Dry-run does not persist upload audit outcomes.

## Preview And Limits

The review page offers **Preview owner-only updates** when a build exists and
no upload job is active. It uses the current source and approval filters, not
the review-table filters. It starts a `dry_run` with `update_existing=true`.
The preview contacts live Wikidata for authentication and checks. It does not
write to Wikidata, change upload status, or write the ledger. The job service
retains results. The UI shows progress, cancellation, errors, item outcomes,
counts, and a separate deferred-connection outcome table. This mode rejects
local references instead of deferring them, so normal results have no deferred
connection outcomes.

`would_update` means **eligible to attempt update**, not a guarantee. The
preview is not a curator approval, a Publication Release or Receipt, or a
reservation of the target or revision. Live checks and guards can later fail.
The local Studio snapshot and overrides can change after the job loads them;
the remote revision check does not bind or refresh the local snapshot.
Failed or cancelled preview results can be incomplete. The UI does not offer
an owner-only live action; API clients can explicitly request `live`.

Tests: [Wikidata Studio tests](tests.md) list the owner-only suites and limits.
