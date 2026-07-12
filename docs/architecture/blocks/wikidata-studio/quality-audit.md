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

## Verdict-analysis baseline (run 48ba6c13)

The exported 228-item review produced 60 `partial` verdicts and 2 `fail`
verdicts; 45 items had not yet received a verdict. Root causes were grouped as
low-confidence English transliteration or title artifacts (works), authority
identity/type conflicts (people), and over-projected or under-explained MARC
metadata (manuscripts). Fix the projection and evaluator evidence contract; do
not weaken the rubric merely to turn incorrect public data into a pass.

The required remediation order is: trust-gate English labels; reject incomplete
work titles; infer corporate versus personal entities from MARC context; retain
authority display variants without treating catalogue order as an error; project
only specific, source-backed subject/genre/provenance claims; then rebuild and
re-verify the full corpus with cache override.

## Completion criteria

The rebuild must have no validator ERRORs or unresolved `__LOCAL:` targets;
every person item must carry an authority identifier; and every new
relationship must be source-backed and property-verified. Then run the
opt-in AI audit and use its grouped evidence to prioritize any remaining
semantic corrections.

## Deterministic export audit

For a downloaded section or diagnostic export, run:

```bash
cd backend
.venv/bin/python -m scripts.check_wikidata_export_quality \
  /path/run-wikidata-approved.json --output /tmp/wikidata-quality.json
```

The report emits counts plus only the relevant row evidence. It currently checks
work identity/author claims, Hebrew leakage in English descriptions, lost Hebrew
gershayim, validator errors/warnings, and whether item approval/verdict fields
are present. A section export may legitimately report
`authority-approved-only`: that flag is not item approval and does not mean AI
verification passed.
