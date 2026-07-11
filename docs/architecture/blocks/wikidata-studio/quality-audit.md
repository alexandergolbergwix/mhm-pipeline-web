# Wikidata Studio — Local quality audit

> Up: [Wikidata Studio](README.md) · [Skills](skills.md)

## Purpose

`backend/scripts/local_measure_verify.py --channel wikidata` rebuilds a run's
Studio items locally from the production read model. It writes only a scratch
report: it does not mutate Postgres, caches, curator overrides, or Wikidata.

Use it before deploying builder or evidence changes to distinguish deterministic
build defects from an evaluator verdict. A full AI judgement is a separate,
external-provider operation and requires explicit curator approval.

## Audit order

1. Compare manuscripts, persons, and works separately. A sudden loss of a
   whole entity type is usually a build-input or join defect.
2. Run the item validator and resolve every ERROR before considering upload.
3. Check local-reference targets, item record associations, labels,
   descriptions, and statement density.
4. Group remaining partial/fail verdict evidence. Change the builder only when
   its MARC/authority evidence supports the claim; do not optimize to a judge
   response by adding ungrounded facts.

## Count interpretation

Person totals are intentionally lower than raw approved authority-match totals
when a match lacks an external identifier or has an unsupported role. This is
the notability and role-safety boundary, not a failed job. By contrast, a loss
of all authority-backed persons indicates that authority keys failed to join
MARC records; `build_items_for_run` must canonicalise control numbers on all
record, authority, and NER inputs (Rule W-66).

## Safe remediation sequence

- Fix placeholder labels from catalog evidence, preferably shelfmark-based for
  otherwise generic manuscript titles.
- Replace generic descriptions only with source-grounded title, creator,
  language, date, or manuscript-context evidence.
- For a new MARC role, verify the live Wikidata property's datatype,
  constraints, and entity-type fit before adding it to `ROLE_TO_PID`.
- Do not create identifier-free people. Preserve their name-string evidence
  only where the claim model permits it, rather than minting a potentially
  non-notable item.
- Treat Hebrew-only labels as valid when no reliable transliteration exists;
  do not invent English labels.

## Completion criteria

The rebuild must have no validator ERRORs or unresolved `__LOCAL:` targets;
every person item must carry an authority identifier; and every new
relationship must be source-backed and property-verified. Then run the
opt-in AI audit and use its grouped evidence to prioritize any remaining
semantic corrections.
