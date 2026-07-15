# Wikidata Studio — Phase 1 projection quality

> Up: [Wikidata Studio](README.md) · [Rules](rules.md)

Phase 1 makes the public Wikidata projection conservative when the MARC/RDF
record contains evidence but not a sufficiently specific semantic assertion.
Rejected candidates remain in the source record, work-candidate evidence, or
curator review context; they are not silently rewritten as stronger claims.

## Deterministic guards

- `rdf_helpers._normalize_marc_isbd_quotes` protects Hebrew gershayim before
  removing ISBD wrappers. Doubled forms such as `רס""ג` become `רס"ג`; wrapper
  quotes are removed.
- `_is_catalog_note_placeholder` rejects explicit catalog/workflow markers
  before P1684 inscription projection. Actual colophon, gloss, correction, and
  marginalia text remains eligible.
- `content_projection._is_primary_subject` requires a canonical or explicit
  primary marker before a MARC 650/600 topic becomes P921. Broad headings such
  as `Jews` remain excluded.
- `marc_subject_resolve.genre_projection_supported` gates over-specific 655
  labels (autograph, license, negotiable instrument, family record, pinkas)
  on an explicit record assertion or matching evidence. `Illustrated works
  (Manuscript)` is not statically mapped to `Q48498`: it is a genre label, not
  proof of an illuminated manuscript. `illuminated_instance_supported`
  requires an authority-stamped QID or structured confirmed decoration evidence
  before P31=Q48498. Free-text notes and `has_decoration` alone are ignored.
- `ROLE_TO_PID` and provenance projection do not treat former owners, sellers,
  or censors as current P127 ownership. Those roles remain source evidence.
- Current-owner 710 contributors are checked for a verified organization QID.
  P195 uses that QID; the canonical National Library of Israel name is a
  verified mapping, while an unknown external institution is not defaulted.
  Provenance gifts and photo credits do not become current holders.
- Exact verified Masorah is eligible for P921; a MARC 100 author plus a real
  245 title creates a source-backed manuscript→P1574→work chain when no 505
  contents list exists. Explicit `דפוס צלום`/facsimile wording produces a
  printed-facsimile description and printed-book P31 rather than Q87167.
- Placeholder institutions such as `Unknown Library` are omitted from English
  descriptions; verified holders remain supported by P195/evidence. Authority
  persons keep natural-order labels plus inverted and Latin aliases. Exact work
  QID aliases do not cover partial/parenthetical titles unless independently
  verified.
- Hebrew date normalization treats geresh/gershayim as punctuation in century
  tokens and fails closed to the ordinary year parser for mixed century/year
  catalogue prose. A malformed date token MUST NOT abort normalization of the
  entire record.

## Source and module boundary

The guards live in the focused Wikidata projection modules and shared RDF label
helper, behind the `WikidataItemBuilder` facade (R1). They do not mutate MARC
records or HMO staging data, so curators can still inspect rejected evidence and
reconcile it later. The validator and upload gates remain unchanged.

## Regression checks

Run the focused suite from `backend/`:

```bash
.venv/bin/python -m pytest \
  tests/unit/test_wikidata_phase1_projection.py \
  tests/unit/test_wikidata_work_candidates.py \
  tests/unit/test_marc_650_655_lod.py -q
```

The fixtures cover Hebrew abbreviation marks, catalog-note suppression,
secondary versus primary subjects, unsafe genre/P31 suppression, explicit
illumination evidence, role-safe P127, and external holding-institution
descriptions/P195.
