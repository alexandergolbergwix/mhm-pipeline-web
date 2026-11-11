# Gila source fidelity and historical proposals

> Up: [Wikidata Studio](README.md) · Rule W-305

## Identity and occurrences

The content projection separates work identity from a source occurrence.
Occurrence identity includes title, author, and folio. The enclosing record supplies source scope.
Structured contents and NER retain distinct occurrences of one work.
Approved work targets also require compatible author evidence.
A same-title supplement cannot suppress the main 100/245 work by another author.
An ambiguous NER author remains a review case instead of selecting a homonymous target.
See [content evidence](gila-content-evidence.md) for source title punctuation and narrative exclusions.

The canonical merge rejects conflicting public QIDs and author identities.
It does not map one legacy local ID to several canonical targets.
Safe local authors retain their identifier and source evidence.
The canonical author path uses the verified authority matcher before it supplies a public QID.

## Source scope and wording

MARC 958 retains `script_type_source` and `script_mode_source` beside normalized classes.
P9302 carries the complete source term in P1932 when that source term exists.
The broad script class does not replace the source distinction.

An explicit contained-letter passage supplies its own genre.
The whole manuscript does not inherit that letter genre.
A sender or recipient needs an unambiguous passage and folio before it receives a scoped manuscript claim.
Otherwise, `person_role_scope_unresolved` preserves the role for review.
A generic 751 related place does not prove manuscript attestation or custody.
Only an explicit attestation role supplies that P7153 qualifier.
The canonical sanitizer enforces the same source boundaries.

`catalog_references.py` separates a catalogue code from bibliographic prose.
An explicit `number` or `catalog_code` can supply P528.
A verified `catalog_qid` supplies P972. A prose citation alone supplies neither an invented code nor a source QID.
The prepared record and audit export retain the citation text for source review.
MARC 942 retains separate catalogue and number fields; repeated preparation does not duplicate that reference.

## Fact comparison

`wikidata_gila_facts.py` compares prepared source facts with final native items and an optional live snapshot.
The comparison checks source IDs, Ktiv roles, shelfmarks, dates, languages, physical units, scripts, contents, and colophons.
It checks that the English description exists and contains no Hebrew source name.
Time checks include precision, calendar, source date text, and unsupported circa.
Work checks retain verified source QIDs and inspect available author and record-scope evidence.
A title qualifier alone cannot establish work identity.
Unresolved local targets and public targets without sufficient identity evidence remain review cases.

Each fact has a stable ID, source field, scope, expected value, observed values, revision, and review status.
Live gaps and live identity cases require review even when the local proposal is correct.
Value presence and source provenance have separate results.
Missing, flat, inconsistent, or mismatched reference blocks require review.
Source references bind the control number directly or through a supported NLI URL.
Historical live defects have a separate classification from the three source review classes.
Zero selected transfer failures does not certify the whole audit.

## Historical proposals

`wikidata_gila_repairs.py` produces exact before/after claims and performs no writes.
Reference proposals remove identical blocks and retain distinct sources, dates, qualifiers, ranks, and values.
Colophon proposals require source text and a uniquely scoped retained statement.
The retained and removed statements must have the same language, datatype, rank, and source scope.
A deprecated retained statement or an invalid folio cannot support removal.
The retained colophon receives every distinct reference from the duplicate statement.

The separate [executor](historical-repairs.md) requires authorization and records exact write receipts.
Each proposal binds the QID, revision, entity digest, and claim IDs.
Validation rejects changed evidence, an applied or uncertain prior write, or unsupported claim changes.
It does not grant ownership, curator approval, or permission to write.
Work partition reviews retain every source-bearing claim for review; they do not merge works or infer deletions.
All proposals remain non-executable until a separate authorized workflow checks their current evidence.

## Measurement

From `backend/`, use the read-only measurement environment:

```bash
.venv/bin/python -m scripts.local_measure_verify \
  --channel wikidata --run-id 47e03437-09f2-4d6b-aea7-89fe79d76198 \
  --gila-audit --audit-projection canonical \
  --audit-live-snapshot /path/to/wbgetentities.json \
  --output-dir state/gila-canonical-audit
```

Canonical mode reads durable HMO snapshots and curator overrides, then applies the current source projection.
It disables reconciliation and performs no production cache write.
It fails if canonical snapshots are absent instead of substituting a legacy result.
The report exports fact comparisons, stage traces, historical proposals, and work partition reviews separately.
The legacy mode remains available for comparison. Neither mode produces a corrected-QID receipt.
Current versions: `source-aware-works-v21` and `hmo-wikidata-v22`.

Explicit extraction rejection requires migration `0053_extraction_rejection`.
See [the review-state contract](../extraction/rejection.md).

These projection modules are web-specific divergences. The desktop converter has no corresponding split modules.
Do not overwrite them during a desktop sync. Port their contracts explicitly before a future shared-source sync.
