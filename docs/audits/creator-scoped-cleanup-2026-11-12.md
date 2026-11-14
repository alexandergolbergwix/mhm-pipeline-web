# Creator-scoped Wikidata cleanup, 12 November 2026

The individual corrections succeeded through the normal Wikidata editor on 12 November 2026.
The agent removed 46 repeated reference blocks and corrected one generated person description.
Both title examples now have one reference. No foreign item received an edit.
The agent corrected the second generated description on 13 November 2026.
The bulk cleanup remains incomplete because its request still failed the `maxlag=5` check.

## Verified creator scope

The user permits edits only to entities that `Alexander_Goldberg_IL` created.
Wikidata normalizes that name to `Alexander Goldberg IL`, user ID `6935067`.
The audit checks the first revision of each target through the public Action API.
It checks the creator name and user ID. It does not infer ownership from NLI identifiers, QID ranges, or local mappings.

| Creator result | Targets |
|---|---:|
| Required account | 1,605 |
| Other accounts, excluded | 17 |
| Unverified creator | 0 |
| Total checked | 1,622 |

The [eligible list](data/creator-cleanup-2026-11-12-eligible.csv) contains 1,604 targets with supported cleanup proposals.
One other owned target needs no supported reference or description correction.
The [excluded list](data/creator-cleanup-2026-11-12-excluded.csv) contains all 17 foreign targets and their creator proof.

## Original prepared corrections

The saved public snapshots support reference cleanup on 1,602 owned entities.
The proposals cover 16,356 claims and would remove 44,755 redundant reference blocks.
They retain the latest recorded retrieval date only when every other source detail matches.
They preserve distinct sources, claim values, qualifiers, and ranks.

Two owned person entities have generated role lists as descriptions: Q141748469 and Q141953242.
The proposals replace those lists with `person associated with Hebrew manuscripts`.
They do not change names, authority identifiers, or role statements.

The complete proposal file is `state/creator-cleanup-2026-11-12/scoped-cleanup-plan.json`.
It is non-executable. Every target needs a fresh entity read and creator check before execution.
The reference proposals use the earlier complete public snapshots, with fresh reads for the reported examples.
A changed revision blocks an old proposal.

## Initial API attempts and result

The normal Wikidata API sandbox showed the signed-in account `Alexander Goldberg IL`.
The prepared request targeted only title statement `Q141747600$7E9C74EA-B631-4FE0-A0C9-425448DCE12C`.
It named three obsolete reference hashes and preserved the latest reference.
It included `assertuser=Alexander Goldberg IL`, `baserevid=2568070012`, and `maxlag=5`.

Wikidata rejected three submit attempts before the edit with `maxlag`.
The reported normalized lag values were 9.683, 5.75, and 6.75.
The agent retained the normal lag limit.
The agent checked the public entity after the attempts instead of assuming success.

| Example | Revision after rejected requests | Result at that point |
|---|---:|---|
| Q141747600 | 2568070012 | Unchanged; title still has four references |
| Q141175529 | 2568055253 | Unchanged; title still has four references |
| Q141953242 | 2568062169 | Unchanged; description correction remains prepared |

## Retry at 13:59 UTC

The agent checked the example creators and public revisions again on 12 November 2026.
The creator of Q141175529 still matched `Alexander Goldberg IL`, user ID `6935067`.
The agent submitted its title-reference correction through the normal Wikidata API sandbox.
The request used `assertuser=Alexander Goldberg IL`, `baserevid=2568055253`, and `maxlag=5`.
Wikidata rejected the request before the edit with `maxlag`.
It reported normalized lag 10.933 and query-service lag 656 seconds on `wdqs1013`.

The agent stopped the batch and retained the lag limit.
The public read confirmed unchanged revisions for all three examples above.
Q141175529 and Q141747600 still have four title references each.
No public correction or foreign edit occurred.
The evidence file is `state/creator-cleanup-2026-11-12/retry-1359-receipt.json`.

## Verified individual corrections

The normal Wikidata editor applied the corrections on 12–13 November 2026.
The agent checked each first-revision creator before each target edit.
The public history confirms that `Alexander Goldberg IL`, user ID `6935067`, made each edit.
The complete public entities match the planned changes exactly.

| Target | Verified revision | Applied correction |
|---|---:|---|
| Q141175529 | 2568311986 | Removed five repeated reference blocks from two statements |
| Q141747600 | 2568314383 | Removed 41 repeated reference blocks from 15 statements |
| Q141953242 | 2568312787 | Replaced the generated role list with `person associated with Hebrew manuscripts` |
| Q141748469 | 2568575252 | Applied the same description correction |

Both title statements now have one reference.
The supported reference audit finds no further reference repairs on the two corrected reference targets.
The edits preserve statement values, qualifiers, ranks, labels, aliases, and other descriptions.
These four targets account for 19 public edits, 46 reference removals, and two description corrections.
No foreign item received an edit.

The receipts are `state/creator-cleanup-2026-11-12/interactive-final-receipt.json`
and `state/creator-cleanup-2026-11-13/Q141748469/receipt.json`.
The original proposal packet remains historical and non-executable.
Its old revisions must not authorize another edit of these corrected targets.

## Bulk retry on 13 November 2026

The agent prepared a fresh, creator-verified request for Q141175480 at revision 2568052550.
The request retained `assertuser=Alexander Goldberg IL`, the exact base revision, and `maxlag=5`.
Wikidata rejected the request before the edit with `maxlag`.
It reported query-service lag of 821 seconds on `wdqs1013`.
The public read confirmed the unchanged revision.

A public read-only POST with `action=wbgetentities`, `props=info`, and `maxlag=5` reproduced the same rejection.
This probe checks the POST lag gate without an authenticated session or a wiki edit.
A successful GET alone does not establish that the POST lag gate will permit an edit.
The bulk process must retain its lag limit and respect `Retry-After`.

The fresh read on 13 November covered all 1,605 owned items and excluded all 17 foreign items.
It found no generated person descriptions in the owned scope.
It found 44,709 repeated reference blocks on 16,339 statements across 1,600 owned items.
Both corrected reference targets remain clean under the supported reference audit.
The snapshot and summary are `state/creator-cleanup-2026-11-13/owned-current-snapshot.json`
and `state/creator-cleanup-2026-11-13/remaining-summary.json`.
Every remaining target still needs a fresh first-revision check and an exact revision before an edit.
The [remaining list for 13 November](data/creator-cleanup-2026-11-13-remaining.csv) contains those 1,600 targets.
Its rows remain non-executable and require a new creator check before each edit.
All 11 read-only probes failed the lag check.
The final probe reported query-service lag of 812 seconds.
The bulk request remains unapplied. No write process remains active.

## Identity cases and exclusions

Q134926101 belongs to another creator and is excluded, including its repeated references.
The proposed merge recipients Q6609840 and Q8055907 also belong to other creators.
Those merges would modify foreign recipients and therefore fall outside the permitted scope.
The conflicting person names and authority identifiers also require source review before any identity decision.
No merge was attempted.

## Evidence

The evidence directory is `state/creator-cleanup-2026-11-12/`.
It contains 1,622 first-revision responses, the account identity, scoped proposals, final example reads, and a SHA256 manifest.
`write-block-receipt.json` records the rejected attempts and unchanged revisions.
`merge-recipient-creators.json` records the separate recipient checks.
The creator audit completed at `2026-11-12T12:24:30.934442+00:00`.

The local prevention changes remain separate from these verified public corrections.
See [creator-only writes](../architecture/blocks/wikidata-studio/creator-only-writes.md) and
[the reference rule](../architecture/blocks/wikidata-studio/reference-retrievals.md).
