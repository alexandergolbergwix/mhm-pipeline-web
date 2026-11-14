# Person dates from flattened MARC fields

> Up: [AI Extraction](README.md) · Rule W-322

`marc_ingest._aligned_person_dates` retains empty occurrence slots.
It attaches a date only when the name and date arrays have the same number of slots.
The rule applies to authors, contributors, personal subjects, and structured entries with several names.

An incomplete date array does not establish its associations.
The parser retains its field, names, date values, and reason in `person_subfield_reviews`.
The reason is `person_date_alignment_unproven`.
The next preparation removes an obsolete review when the raw source supplies a complete array.
Repeated preparation retains the same person-date result.

The Gila source record `990000885680205171` has four 700 names and two date values.
The former parser attached `1469-1549` to the scribe and shifted the next date to Elia Levita.
The source does not preserve those occurrence associations.
The parser now retains both dates as source evidence without those person assignments.

The 897-record local check finds 187 ambiguous date groups on 185 records.
It finds no person-date difference after a second preparation.
The broader preparation check also finds 25 pre-existing `linked_880` differences.
This change does not resolve those separate differences.

`wikidata_gila_facts` exports each date group with scope `person_date_alignment`.
It does not generate a public birth or death claim from that group.
The completion packet assigns the group to email point 5 and retains it for manual review.

Tests: `backend/tests/unit/test_marc_person_date_alignment.py`.
The tests cover explicit empty slots, incomplete arrays, structured entries, source repair, and audit evidence.
