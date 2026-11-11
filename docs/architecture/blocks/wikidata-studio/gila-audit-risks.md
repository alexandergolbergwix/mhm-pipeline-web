# Gila Audit Risks

> Up: [Wikidata Studio](README.md) | [Audit](gila-audit-completion.md)

The Gila audit is not complete. These findings require review against current
source and live receipts; the reference-block migration is local code only.

- W-305 regressions cover repeated title/author/folio occurrences, conflicting
  public QIDs, and ambiguous canonical aliases. These code checks do not repair
  historical work links or establish the identity of every proposed component.
- Approved work targets require compatible author evidence. A title or shared
  QID alone does not settle a conflict between authors. Unknown targets remain
  explicit identity-review cases in the fact report.
- Old flat multi-snak reference data does not identify source boundaries.
  The new `reference_blocks` contract rejects ambiguous grouping at safety
  gates, but cannot reconstruct old boundaries or repair live claims (W-304).
- `ExtractionApproval.approved=false` covers both pending and unapproved rows.
  This value alone does not establish that a curator explicitly rejected a row.
  Migration 0053 now supplies explicit rejection. It does not reconstruct lost historical decisions.
- A historical colophon proposal requires matching rank, language, valid scope,
  and source evidence. An incompatible claim remains for review.

Resolve these findings with source-aware identity and evidence before a live
correction. The owner-only mode has a narrower manuscript scope and does not
repair historical work or reference claims.
See [Gila source fidelity](gila-source-fidelity.md) for the new local checks.
See [content evidence](gila-content-evidence.md) for the final corpus defects and explicit source review reasons.
