# Cantarini: redirect prerequisite after the merge

The donor still has an English description.
Wikibase requires a completely empty donor before `wbcreateredirect` can replace an item with a redirect.
Labels, descriptions, and aliases count as content.
Zero statements alone do not meet that requirement.
This research made no live edits.

## Current residual fields

The fresh API read occurred at 20:42:40 UTC on 11 November 2026.
The evidence resides in `state/gila-independent-samples-2026-11-11/history/post-merge-residual-entities.json`.

| Field | Q141175543 donor | Q141755239 recipient |
|---|---|---|
| Revision | 2568070525 | 2568070527 |
| Labels | None | Hebrew: `דרושים` |
| Descriptions | English text below | English: `Work preserved in a Hebrew manuscript, attested in NLI record 990001915930205171` |
| Aliases | None | None |
| Statements | 0 | 6 |
| Sitelinks | None | None |

The donor's exact residual description is:

> Work preserved in a Hebrew manuscript with author recorded in the source catalogue, attested in NLI record 990001915930205171

The merge moved the statements at 20:34:40 UTC.
It did not create the redirect.
The separate redirect operator stopped before a request because its local check found the residual description.
That stop agrees with the server's content requirement.

## Primary technical evidence

The official [ItemRedirectCreationInteractor](https://gerrit.wikimedia.org/r/plugins/gitiles/mediawiki/extensions/Wikibase/+/refs/heads/master/repo/includes/Interactors/ItemRedirectCreationInteractor.php) calls `Item::isEmpty`.
It rejects a nonempty item with the error `origin-not-empty`.
It does not ignore terms when it checks the donor.

The official [Item data model](https://gerrit.wikimedia.org/r/plugins/gitiles/mediawiki/extensions/Wikibase/+/refs/heads/master/lib/packages/wikibase/data-model/src/Entity/Item.php) requires an empty fingerprint, statement list, and sitelink list.
The official [Fingerprint data model](https://gerrit.wikimedia.org/r/plugins/gitiles/mediawiki/extensions/Wikibase/+/refs/heads/master/lib/packages/wikibase/data-model/src/Term/Fingerprint.php) requires empty label, description, and alias lists.
The donor's residual description therefore makes `isEmpty` false.

The official [redirect interactor](https://gerrit.wikimedia.org/r/plugins/gitiles/mediawiki/extensions/Wikibase/+/refs/heads/master/repo/includes/Interactors/EntityRedirectCreationInteractor.php) also checks the destination.
The destination must exist and must not itself be a redirect.
The source and destination must have compatible types and different identifiers.
The interactor checks permissions and the current source content before it saves the redirect.

The live [API help](https://www.wikidata.org/w/api.php?action=help&modules=wbcreateredirect) requires POST and a CSRF token.
The official [API module](https://gerrit.wikimedia.org/r/plugins/gitiles/mediawiki/extensions/Wikibase/+/refs/heads/master/repo/includes/Api/CreateRedirect.php) defines `from`, `to`, `token`, and `bot` parameters.
It does not define `baserevid` for the redirect action.
A separate source-term edit can use its normal revision check before the redirect request.
The operator must then confirm the actual redirect through a fresh read.

The audit saved the retrieved official source files and API help under the evidence directory.
These source reads used Wikimedia Gerrit and the Wikidata Action API.
They did not use GitHub.

## Exact metadata disposition

The donor and recipient descriptions cite the same source record.
The donor adds only the phrase that the catalogue records an author.
The recipient already retains the explicit Cantarini author claims and their source evidence.
The residual description supplies no distinct author, source identifier, date, place, or work identity.

Retain the exact donor text in the operation receipt and entity history.
Record its disposition as redundant with the recipient's source and explicit author evidence.
Do not insert the whole description as an alias; it is not a name or title.
Do not replace valid recipient terms merely to preserve the longer sentence.

The narrow source edit removes only the donor's English description.
It leaves the recipient and all claims unchanged.
The operator must bind that edit to a fresh source revision.
The operator must then read the donor again and confirm that every content field is empty.
Only then can a separate redirect request satisfy the server's empty-source requirement.
No broad entity clear operation is necessary.

This report establishes the prerequisite and the metadata decision.
It does not claim that the source-term edit or redirect succeeded.

## Later verified execution

The authorized operator completed the exact source partition and metadata prerequisite.
The separate redirect succeeded at revision `2568073483`.
The final public read confirms Q141175543 redirects to Q141755239.
The [final audit](gila-live-update-recheck-2026-11-11.md) records the complete receipts and remaining cases.
