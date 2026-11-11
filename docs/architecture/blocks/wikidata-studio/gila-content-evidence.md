# Gila content evidence

> Up: [Source fidelity](gila-source-fidelity.md) · Rule W-305

The final corpus comparison exposed defects beyond the selected-field audit.
The code now separates a normalized work identity from the source title in P1932.

MARC 505 and parsed MARC 500 entries retain `source_title` before label cleanup.
P1932 retains meaningful quotation marks and a terminal Hebrew geresh.
The code removes balanced transport wrappers and unescapes doubled MARC quotes.
An explicit responsibility separator supplies the author separately.
The source title does not disappear on repeated preparation.

A named commentary can contain `כתובים` without becoming catalogue noise.
An anchored Latin 100/245 title can supply a work with its source author.
The Latin 505 authority gate remains active.
A generic manuscript title does not establish a new work.
An editor or compiler does not establish a main-work author.
Citation abbreviation checks respect word boundaries; `אלפסי זוטא` is not a citation fragment.

Quoted names in explicit place lists, testimony, or drawing descriptions do not establish named works.
An entry endpoint such as `מגיע עד "ערך משה"` does not establish another work.
The parser retains these passages in `non_work_content_evidence` and in the complete notes.
Each evidence row has the source field, source passage, reason, and available folio.
The guard applies to its own passage. It does not suppress another anchored work passage.

A short unidentified supplement can remain deferred when a specific exemplar already exists.
Its work evidence carries `projection_status=deferred` and a specific `projection_reason`.
The audit reports that decision separately from source absence or curator rejection.
Policy exclusions remain review cases; the code does not call them curator decisions.

The audit derives expected content wording from the source before it inspects emitted claims.
It checks source author roles before it expects a 100/245 work.
Native dates use the uploader's explicit calendar default when the native contract omits that field.
Live dates still require their actual calendar and precision.
Century passages with a named year or boundary require review; the audit does not select an exact year from them.
An absent link to an unresolved work remains an identity case, rather than a confirmed historical defect.
The final native cleanup retains a reason when a specific exemplar supersedes an unknown-text fallback.

Tests: `test_gila_full_path_regressions.py`, `test_wikidata_gila_facts.py`,
`test_notes_work_extraction.py`, `test_rdf_helpers.py`, and `test_rdf_build.py`.
