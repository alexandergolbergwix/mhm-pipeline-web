# Gila audit — Source-qualified projection and review

> Up: [Wikidata Studio](README.md) · Rules W-294/W-296

The [11 November implementation receipt](../../../audits/gila-code-implementation-2026-11-11.md) records the current local code and corpus checks.
The [12 November live verification](../../../audits/gila-live-verification-2026-11-12.md) checks all 897 manuscripts and lists the unresolved source identities.
The historical measurements below retain their original dates. Live correction remains a separate operation.

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

Owned updates deduplicate existing reference blocks by semantic snaks.
Native statements now carry explicit blocks and a flat compatibility view
(W-304). The original boundaries of old flat multi-snak data cannot be
reconstructed reliably; safety gates require a rebuild from source.
Reference-only changes count as updates, so the exists path does not skip them.
The local migration does not prove that live references were corrected.

The current legacy build schema is `source-aware-works-v24`.
The current canonical fingerprint salt is `hmo-wikidata-v28` (W-322).
W-296 originally used `source-aware-works-v13` and `hmo-wikidata-v16`.
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
Use `--audit-projection canonical` for the final canonical path.
The report now includes per-fact comparisons and non-executable historical proposals.
See [Gila source fidelity](gila-source-fidelity.md) for contracts and limits.

## Full-corpus receipt: upload `24802798-dd95-4973-8ec8-32138f459e8f`

The live job processed 1,996 items. Its result recorded zero connections added
and 433 unresolved connections; inspect the per-link outcomes before retry.
It recorded 1,558 adopted, 407 blocked, 19 skipped, one created, and 11 failed.
Ten failures are owned manuscript updates with colliding Hebrew label/description
pairs. The builder proposed each literary Hebrew title as a label.
The target's Hebrew description plus that title duplicated a sibling item's pair.
The API named the sibling QID. Each target P3959 still matched its source record.
The local uploader fix preserves the existing target label and adds the proposed
title as an alias. A targeted retry must verify all ten outcomes.

| Source CN | Target QID | Conflicting sibling QID |
|---|---|---|
| `990000413130205171` | `Q141746838` | `Q141746840` |
| `990000898590205171` | `Q141747184` | `Q141747245` |
| `990000953610205171` | `Q141747216` | `Q141747217` |
| `990001022920205171` | `Q141747250` | `Q141747327` |
| `990001067870205171` | `Q141747292` | `Q141747291` |
| `990001078470205171` | `Q141747296` | `Q141747323` |
| `990001153990205171` | `Q141747437` | `Q141747438` |
| `990001324750205171` | `Q141747621` | `Q141747618` |
| `990001366520205171` | `Q141747665` | `Q141747666` |
| `990001905840205171` | `Q141748194` | `Q141748246` |

The remaining failure is `work:מדרש_איכה`.
Its cached proposal has Hebrew label `מדרש איכה` and only an English description.
The local WBI payload does not contain an equal same-language label/description
pair. The API returned the generic Hebrew collision message without a QID.
`wbsearchentities` found only the different-title Q141748690, and exact work
reconciliation found no candidate. The local uploader now stops after one such
error and reports manual payload review. It does not guess a QID. This item
remains failed until a reviewed payload or exact candidate resolves it.

The 407 blocked and 19 skipped items are separate from the 11 write failures.
Work-identity review and uncertain duplicate confirmation remain open.

The report compares selected source fields. Its remaining-check list identifies
semantic and live checks that still need review.
Zero transfer cases in this report do not certify all nine requirements.

## Local measurement

The 897-record local build produced 1,996 items and zero validation-error items.
It retained 413 named unknown scribe claims on 400 manuscripts, 897 qualified
Ktiv memberships, and 563 role-qualified colophons.
The report listed 147 absent-source field cases and 1,163 identity/scope cases.
These counts describe proposals and review cases, not completed live corrections.
The 2026-11-07 read-only legacy audit checked 897 records and found 896 known
QIDs. It reported 90 absent-source cases, zero selected transfer cases, and
1,220 identity/scope cases. It did not verify live corrections.
The mixed old work and failed bulk items still need their own repair receipts
and live read-back before the audit can close.

The [retrieval contract](reference-retrievals.md) distinguishes exact repeats from the same citation with different P813 dates.
