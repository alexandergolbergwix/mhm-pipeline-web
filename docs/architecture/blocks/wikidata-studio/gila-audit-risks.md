# Gila Audit Risks

> Up: [Wikidata Studio](README.md) | [Audit](gila-audit-completion.md)

The Gila audit is not complete. These findings require review against current
source and live receipts; the reference-block migration is local code only.

- `content_projection.py` deduplicates work occurrences by title before it
  emits `P1574`. A later occurrence can lose its folio or author evidence.
- `wikidata_canonical_enrichment.py` can match one legacy work to more than one
  canonical work. A later alias can redirect a manuscript's local reference.
- Work author normalization and local-reference resolution can lose or
  misattribute author evidence. A title or shared QID alone does not settle a
  conflict between authors.
- Old flat multi-snak reference data does not identify source boundaries.
  The new `reference_blocks` contract rejects ambiguous grouping at safety
  gates, but cannot reconstruct old boundaries or repair live claims (W-304).
- `ExtractionApproval.approved=false` covers both pending and unapproved rows.
  This value alone does not establish that a curator explicitly rejected a row.

Resolve these findings with source-aware identity and evidence before a live
correction. The owner-only mode has a narrower manuscript scope and does not
repair historical work or reference claims.
