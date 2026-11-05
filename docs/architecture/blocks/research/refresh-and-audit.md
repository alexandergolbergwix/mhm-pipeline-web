# Research Assistant — Refresh requests and source counts

> Up: [Research Surface](README.md) · Rule W-295

## Prompt gate

The production journal recorded two refusals on 2026-11-04.
The local router returned `local_reject_after_jev` at 14:10 UTC.
Jev returned `harmful_or_exfiltration` at 14:43 UTC.
The curator asked: `update the data please things have beign changed`.

`scope.py` recognizes corpus follow-ups and Hebrew tokens.
The Jev questions explicitly include data refreshes, report updates, public wiki
reads, and canvas exports in the research scope.
The harm question checks harm and data exfiltration separately from relevance.
All three Jev answers must permit the request.
Jev can resolve a local `out_of_domain` result.
It cannot override an explicit injection pattern or a rejected topic cluster.

Both Heroku and Modal use `JevPreFilter` before the planner.
Deploy both applications after a gate change.

## Source counts

The summary reads both `canonical` and `legacy` Studio caches.
A request with `approved_only=False` can use an existing approved-only snapshot
when no full snapshot exists.
A request with `approved_only=True` never uses an unapproved snapshot.
`studio_items_for_project` attaches live QIDs from the Wikidata ledger.
It preserves author-specific work identity during that lookup.
The Wikidata provider normalizes `QDraft_MS_` and `marc:` control numbers.

When project Wikibase SPARQL returns no entities, the summary uses the instance
ledger for entities in the current RDF graph.
URI identity uses exact URIs or the two known HMO namespaces.
A shared fragment in an unrelated namespace does not establish identity.

The summary key includes scalar Studio fingerprints for both sources and
approval modes, plus a digest of instance mappings.
An upload or a changed QID invalidates a cached summary without an RDF rebuild.
The summary never loads Studio result blobs just to calculate a fingerprint.

## Public Wikidata reads

`wiki.py` sends a User-Agent with the project URL and contact email.
An optional login failure permits an anonymous `wbgetentities` read.
A successful local read does not establish that every remote host has access.

## Audit status and job receipts

On 2026-11-04, read-only checks confirmed the four previously reported gaps
closed on `Q141747170`, `Q141747098`, and `Q141175895`.
The check covered person roles, full author names, codex classification,
script correction, and the single qualified colophon.
This does not close the complete audit.
`Q141747170` and `Q141747098` still have no `P11603` scribe links.
The first record names its scribe in MARC 700, but its authority rows contain
no match for that scribe.
The second record has no MARC 700 scribe and no prepared `colophon_scribe`.
The source text and authority evidence need further review before publication.
Job `20690c64-2e3d-4efd-80dc-eecf65722e4d` recorded five `exists` outcomes
and one `updated` outcome.

The earlier job `31e59984-4942-405f-b690-0d590ceb05b5` recorded 344 updates,
490 `exists`, 67 adoptions, and two failures.
The failures concerned `work:מדרש_איכה` and `QDraft_MS_990001188700205171`.
The former had equal Hebrew label and description values.
The latter had a label/description collision with `Q141754494`.
The final targeted job did not include these two items.
Always inspect item outcomes; a job status of `succeeded` does not prove
that every item succeeded.
