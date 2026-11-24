# Manuscript audit exports

The audit result on **Wikidata Manuscript Audit and Repair** provides two JSON downloads.
Run a fresh audit to prepare the downloads for an older report.

- **Download verified-only export** includes verified manuscript mappings and verified checks.
  It includes the exact statement ID and value when the check identifies a statement.
  Each check has the audited item revision. A check can verify a mapping or source occurrence.
- **Download proven claim errors** lists exact statements with a reviewed work-link conflict or an unsupported date qualifier.
  A work-link conflict needs a reviewed source choice for the same occurrence.
  A date qualifier error needs one matching live date and the exact original MARC date text.
  The claim must cite the same catalogue record. The report holds other source references for review.
  The first creator check must return UID `6935067`. An unavailable result excludes the item.

The audit saves both downloads with the completed snapshot.
The reports include the audit ID and time. The error report also includes the proof source and statement ID.
The downloads read the saved snapshot and make no Wikidata changes.
A repair still needs current source, revision, and creator checks.

## Files

- `wikidata_gila_exports.py` builds both reports.
- `wikidata_gila_facts.py` records exact statement matches and proof of conflicts.
- `wikidata_gila_people.py` and `wikidata_gila_sources.py` record matches for roles and evidence occurrences.
- `wikidata_gila_audit_job.py` stores the reports with the successful audit.
- `GET /api/admin/wikidata-gila-repairs/exports` returns the requested report to an administrator.
- `test_wikidata_gila_exports.py` checks the verified filter and the creator, source, and revision gates.
