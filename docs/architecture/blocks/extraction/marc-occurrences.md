# Original MARC occurrences

> Up: [Extraction](README.md) · Rule W-326

`marc_occurrences.py` retains each original field beside the flat compatibility columns.
Each occurrence has a tag, an occurrence number, two indicators, and ordered subfields.
Control fields retain their complete text.
`MarcField.ordered_subfields` and `MarcRecord.field_order` retain the binary source order.

The importer accepts binary MARC21, MARC JSON, MARCXML, and the existing flat formats.
MARCXML must use UTF-8 and the MARC21 namespace or no namespace.
The parser rejects DTD and entity declarations.
The JSON `marc_occurrences` value is authoritative after the first normalization.
The parser regenerates the flat columns from it.
It removes the redundant JSON `fields` representation after conversion.
A record edit must replace the canonical occurrences or supply a new complete `fields` payload.

The parser keeps a library, shelfmark, city, and country within one 942 occurrence.
It never borrows the first city or country for another occurrence.
Flat columns retain empty slots.
The parser removes balanced quote wrappers and preserves quotation marks inside a title.
Unequal column lengths produce `holding_subfield_reviews` and withhold unsupported associations.
Multiple current libraries require a source choice.
A former owner cannot become the current library without new source evidence.

Structured 100/700/710 fields retain their own names, dates, roles, and authority identifiers.
Sparse flat roles and dates produce `person_subfield_reviews`.
The parser never assigns an unsupported role or date to the first name.
Original 880 fields remain separate with their linkage subfields.

Structured 505 fields retain a source key for each title and responsibility.
A single explicit folio in 505$g remains with its unique title.
Multiple unresolved scopes remain review cases; the parser does not pair them by a guess.
A person field retains its associated title and every raw authority identifier.
Structured 581 citations retain semicolons inside the original citation.
Rebuilds replace old derived 505/581/590 values when their raw field exists.
`_marc_derived_keys` identifies derived values that a later structured rebuild must replace.
Removal of an original field also removes its old flat columns and derived values.

`backend/scripts/compare_marc_associations.py SOURCE OUTPUT` performs a local comparison.
The report separates a changed saved association from a newly derived association.
It derives expected tuples directly from original 942 occurrences.
It marks flat records as `original_occurrences_unavailable`.
It performs no public or database write.
The saved 897-record export has no original occurrences; it cannot independently prove every old association.
When original data exists, use the existing record editor to replace that record.
Its endpoint is `PATCH /runs/{run_id}/records/{control_number}` with a `marc` object.
Keep the same control number. Rebuild the source and review the comparison before publication.

This web implementation extends the shared desktop parser model.
Port the ordered fields and occurrence-aware helpers upstream before a shared desktop release.
Do not claim that the upstream pipeline already has these changes.

Tests: `test_marc_occurrences.py`, `test_marc_person_date_alignment.py`, and `test_marc_ingest_ner_inputs.py`.
