# Wikidata upload readiness

> Up: [Wikidata Studio](README.md)

The export audit reports data defects and Publication prerequisites separately.
Zero data defects does not establish permission to publish.
`publication_preflight` reports missing approvals, incomplete AI verdicts,
incomplete duplicate probes, and local or deferred connections.
Unstructured evidence counts as an incomplete duplicate probe.
The Publication service still requires a sealed Release and a valid Dry-run Receipt.

## Person identity

The canonical build compares each person's own given name with the authority heading.
Shared ancestor names cannot establish identity when the given names differ.
The build preserves supported alternative headings and existing spelling tolerance.
The build clears unconfirmed identifiers and omits people without a publishable identity.
The source evidence remains available for curator correction.
Fingerprint version `hmo-wikidata-v14` invalidates older canonical caches.

`converter.authority.heading_fidelity.given_names_match` supplies the shared comparison.
The export audit checks emitted P8189 claims against their authority headings.
An old AI `full` verdict cannot override an explicit heading conflict.
Publication repeats this check before it accepts an older Studio cache.
The desktop converter needs an explicit port before the next shared-source sync.

## Connections between entities

The Publication projector first collects the source entity keys and existing QIDs.
It then resolves local references in statements, qualifiers, and references.
It retains unresolved targets in the Release reference index.
The default profile `mhm-wikidata` version `1` blocks unresolved connections.
The SQL and in-memory repositories apply the same rule.

The curator can select **Defer connections that need new QIDs** before preparation.
This option selects profile version `1-nodes`.
The projector defers a complete statement only when all its local targets exist in the source.
It stores that statement, including its qualifiers and references, in `deferred_statements`.
The Release digest covers the deferred statement.
The Publication page exposes the retained statement before approval.
An absent target remains a blocker in both profiles.

A node Release does not complete the deferred connections automatically.
After the targets receive confirmed QIDs, rebuild and reconcile the source.
Prepare profile version `1` to include those connections in a later Release.
Review that Release and run its dry-run before publication.
Do not treat the earlier node Release as a complete graph upload.

## Regression tests

- `backend/tests/unit/test_hmo_canonical_wikidata.py`: rejects an ancestor's identifier through the canonical build.
- `backend/tests/test_wikidata_export_quality_checker.py`: separates Publication prerequisites and rejects false authority identifiers.
- `backend/tests/test_publication_router.py`: rejects old false identities, resolves known targets, indexes nested targets, and exposes deferred statements through the API.
- `backend/tests/unit/test_publication_module.py`: blocks unresolved local connections in the default profile.
- `frontend/tests/unit/wikidataPublicationPanel.spec.tsx`: tests the explicit option and the retained claim view.
- `frontend/e2e/wikidata-publication.spec.ts`: checks the option and the request to the backend.
