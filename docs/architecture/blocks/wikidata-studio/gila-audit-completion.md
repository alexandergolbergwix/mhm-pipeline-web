# Gila audit — Source-qualified projection and review

> Up: [Wikidata Studio](README.md) · Rules W-294/W-296

## Accepted model

The user chose separate physical manuscripts and written works on 2026-11-05.
Keep `Q47461344` on work items. Manuscripts keep their physical classes.
Record this decision in the response to Gila's request for both classes.
Image counts and filenames do not prove codex form.
Both the legacy builder and final canonical sanitation enforce this rule.

## People and works

`person_linking.py` retains a MARC 700/800 scribe without an authority ID as
`P11603 somevalue`, with the exact name in `P1932` and the catalog reference.
A question mark retains `P1480 = Q30230067`. This creates no person item.
The person key includes the role, so one person can have two source roles.

Approved exact authority matches and the audited identity table can supply P50.
The verified Cantarini entry uses `Q6076130`, NLI `987007259482805171`, and VIAF
`37971001`. An explicit rejected match blocks the audited fallback.
The Isaac of Troki candidate has a different NLI authority ID from the current
source match. That identity requires a source check before reuse.

`content_projection.py` restricts the record-author fallback to the primary
title or an unambiguous MARC 505 entry. Supplements need their own author evidence.
`hmo_canonical_wikidata.py` applies the same title-specific evidence after merge.
It preserves supported P50 values and removes unsupported P50/P2093 values.

The anchored note `בראשו שילב המעתיק דרשה על פרשת ויגש` creates a separate work.
Its `Sermons` genre resolves to `Q60797` and stays on that work.
The manuscript does not inherit that section genre as P921/P136.
The parser supplies no author, folio, or physical unit without source evidence.
An unidentified descriptive sermon uses a source-scoped local ID.
The builder does not reconcile that identity by title alone.

## Merge and upload

Qualified statement identity includes roles, folio ranges, and named unknown values.
Qualifier order and display labels do not create distinct facts.
Qualified Ktiv membership replaces an unqualified duplicate.
An exact source date removes stale circa from the same canonical date and precision.
Different dates or uncertainty statements remain distinct.

Owned updates deduplicate reference blocks by semantic snaks.
They preserve distinct sources and ignore transport hashes and property order.
The claim compiler also removes repeated native reference snaks.
Reference-only changes count as updates, so the exists path does not skip them.

The legacy build schema is `source-aware-works-v13`.
The canonical fingerprint salt is `hmo-wikidata-v16`.
Port shared converter changes upstream before the next full desktop sync.

## Deterministic report

From `backend/`, with the normal read-only measurement environment:

```bash
.venv/bin/python -m scripts.local_measure_verify \
  --channel wikidata --run-id 47e03437-09f2-4d6b-aea7-89fe79d76198 \
  --gila-audit --output-dir state/gila-audit
```

`--gila-audit` checks the full local builder proposal without an AI judge.
It writes `gila_builder_audit.json` with one row per source record and known QID.
Cases use `absent_in_source`, `present_not_transferred`, or
`identity_or_scope_uncertain`. The report retains `record_status_notes` separately
from inscriptions. It includes recent per-item upload outcomes.

`--audit-live-snapshot PATH` accepts a `wbgetentities` JSON with an `entities` object.
For multi-author work items, the report retains the revision, claim IDs, source
control numbers, and source authors as a `review_source_partition` proposal.
Multiple authors can be legitimate; this proposal requires source review.
The report never performs a repair or declares a QID corrected.

The report compares selected source fields. Its remaining-check list identifies
semantic and live checks that still need review.
Zero transfer cases in this report do not certify all nine requirements.

## Local measurement

The 897-record local build produced 1,996 items and zero validation-error items.
It retained 413 named unknown scribe claims on 400 manuscripts, 897 qualified
Ktiv memberships, and 563 role-qualified colophons.
The report listed 147 absent-source field cases and 1,163 identity/scope cases.
These counts describe proposals and review cases, not completed live corrections.
The mixed old work and failed bulk items still need their own repair receipts
and live read-back before the audit can close.
