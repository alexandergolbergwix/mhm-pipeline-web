# MARC → Wikidata coverage & mapping improvement plan

Status: IN PROGRESS (2026-10-19) — WP9 + WP7 landed in one session; WP1…WP6
and WP8 remain. Next session continues from the WP9 gate below.
Basis: `reports/marc-wikidata-field-coverage-17th-century.md` +
`reports/marc-wikidata-mapping-391-fields.md` (measured against live
`query.wikidata.org`, 2026-10-18). Source corpus:
`pipeline/data/tsvs/17th_century_samples.tsv` — **897 MARC records**, 391
MARC columns (178 populated), matching **897 Wikidata items 1:1 by P3959**
(0 duplicates, 0 orphans).

## Session log

### 2026-10-19 — WP9 harness + WP7 mapping fixes (done)

**WP9.** `backend/converter/wikidata/marc_field_map.py` is the single field →
target table with a deliberate fate per field (`map` / `evidence` /
`out_of_model`). `backend/scripts/marc_wikidata_coverage.py` measures it
(`--projected` prices a change before upload, `--claims` replays a snapshot,
`--live` joins WDQS by P3959) and exits non-zero on regression or
`--fail-below-target`. CI fixture at
`backend/tests/unit/fixtures/marc_coverage_{corpus.tsv,claims.json,baseline.json}`;
tests in `test_marc_wikidata_coverage.py`. Rules W-275 / R139.

**WP7.**
1. `710` → `P195` was already correct in the projection
   (`manuscript_projection.py:282`); the plan's `P50` row was a **report**
   mapping-table error. Fixed in `marc_field_map.py` + pinned by test. The
   remaining gap is unresolved holder QIDs (W-143/W-161), not the mapping.
2. `035$a` now falls back into the P3959 identity channel when `001` is absent
   (`control_number_from_035`); anything unrecognised fails closed.
3. MARC `880` now projects as its linked tag (`880$6` → target tag); the
   romanized form reaches `marc_880`/`marc_880_name`. All 25 corpus rows
   resolve to `245`. Rules W-276 / R140.
4. RDA `336-338` declared **out-of-model** in the ledger (kept as carrier
   evidence). Corpus count is 0 for all three.
5. `505` kept as `P1574`; corpus count is 1. No change.

### Corpus corrections the audit missed

Several fields named in this plan are **empty** in the 17th-century corpus, so
their "expected lift" cannot be priced on it: `260$a` (0), `264` (0), `520$a`
(0), `563$a` (0), `593$a` (0), `752$a` (0), `034` (0), `336-338` (0).
Conversely the real data sits in fields this plan called catalog-only:
`597$a` (773) is a **Ktiv credit line**, not a colophon; the true colophon text
is `957$a` (617); script tradition is `958$a` (768) / `958$b` (66), not `593`.
`903$a` (897) is an access restriction. WP5's colophon/script work must target
`957`/`958`, and the `593`/`597` targets need re-stating.

### WP9 projected baseline (897 records, current code)

Measured with `--projected` — what the *code* emits, which already exceeds the
live figures in the plan's baseline table. **Corrected 2026-10-19** after two
harness defects were fixed: the work-layer join missed the `__LOCAL:` prefix on
`P1574` (every work-layer field read 0%), and the property counter incremented
once per work per record (`P1476` read 167%).

| Property | projected | live (plan) | target |
|---|---|---|---|
| P3959 / P571 / P407 / P1476 | 100% | 100% | 100% |
| P1104 | 88.4% | 88.6% | 85% |
| P195 | 71.2% | 71.7% | 70% |
| P217 | 99.3% | 98.9% | 95% |
| P1574 | **58.4%** | 16.9% | 90% |
| P2093 | **45.6%** | 0% | 60% |
| P50 | 0% | 0% | 60% |
| P136 | 12.9% | 15.1% | 80% |
| P921 | 0.6% | 2.3% | 60% |
| P2048 | 5.1% | 5.4% | 50% |
| P186 | 1.2% | 2.9% | 40% |
| P7153 | 0.4% | 7.2% | 40% |
| P1071 | 0% | 5.1% | 60% |
| P7535 | 0% | 0% | 50% |
| P953 | 0% | 12.8% | 80% |

Per-field authorship, now measured correctly through `P1574`: `100$a`
**89.5%** (408/456), `700$a` **45.9%** (305/665). The author data is largely
built; `P50` reads 0% because the built works carry the name string (`P2093`),
not a resolved authority QID. WP1's remaining work is QID resolution and
upload, not author extraction.

Three findings that change later packages: **P1574 is already 58.4% in code**
(the 16.9% live figure is an *upload* gap, not a projection gap — WP1's work is
partly "upload what we already build"); **P2093 is already 45.6% in code**
against 0% live, for the same reason; and **P953 is 0% because the projection
emits `P973` (described at URL) for `856$u`, not `P953`** (full work available
at URL). WP6 must decide which property is correct rather than assume P953.

A raw rebuild must not overwrite live items: enrichment inputs (`kima_places`,
authority matches, NER entities, `provenance_ner`) are absent from a raw-TSV
run, so `P2888` (893 items), `P953` (115), `P1071` (46) and others read 0%.
`P1433` (9) and `P6819` (6) have no emission site in the code at all and would
be deleted. Use the Studio rebuild path, which joins enrichment.

### Remaining gates

| Order | Package | Gate |
|---|---|---|
| 2 | WP1 authorship | P50/P2093 ≥60% on works; P1574 ≥90% |
| 4 | WP2 subjects/genres | P921 ≥60%, P136 ≥80% |
| 5 | WP3 + WP4 physical/places | P2048 ≥50%, P1071 ≥60% |
| 6 | WP5 + WP6 notes/digital | P7535 ≥50% (retune judge per W-67), P953 vs P973 |
| 7 | WP8 ledger | publish `ledger_rows()` as the reports page |

Per-session decisions taken (2026-10-19): WP5 promotes `520` → `P7535` **and
retunes the judge** (eval-agent `p7535_scope`, rubric, cache salt) to accept
520 scope-and-content text while Rule W-67 still blocks 500/561 prose. WP1/WP2
**widen the gates with new rule entries**, only where the record carries
source-backed evidence; shortfalls are logged, not hidden by lowering targets.

Goal: raise the measured coverage of the mapped MARC fields on the live
items, fix mapping errors, and give every catalog-only field a deliberate
fate (map / attach as evidence / declare out-of-model).

## Baseline (live, 897 items)

| Layer | Properties | Coverage |
|---|---|---|
| Identity | P3959, P31, P1476, P571, P407 | **100%** |
| Bridge/refs | P973, P2888 | 99.5% |
| Shelfmark / copyright | P217, P6216 | 98.9% |
| Extent (folios) | P1104 | 88.6% |
| Genre · subtitle · digital URL | P136 · P1680 · P953 | 15.1% · 14.2% · 12.8% |
| Work link | P1574 | **16.9%** (152/897) |
| Significant place · location of creation | P7153 · P1071 | 7.2% · 5.1% |
| Dimensions · script | P2048/P2049 · P9302 | 5.4% · 5.4% |
| Material · subjects | P186 · P921 | 2.9% · 2.3% |
| Provenance · inscriptions | P3342 · P1684 | 1.6% · 0.9% |
| **Authors** | **P50 / P2093** | **0%** (despite `100$a`×456, `700$a`×665) |
| Summary · binding | P7535 · P1552 | 0% · 0% |
| IIIF · date qualifiers | P6108 · P1319/P1326/P580/P582 | ≤0.1% · 0% |

Unmapped catalog-only fields carrying real data: `907` (digital-file ids),
`952` (rights/export), `957` (colophons), `958` (script), `921/934/915`
(local ids), `336–338` (RDA), `034` (coords), `710`×897 (holding library).

## Work packages (priority order)

### WP1 — Authorship end-to-end (highest value)

**Problem.** `100$a` (456 records) and `700$a` (665 records) yield ZERO
author claims. WPM-correct placement is the WORK, not the manuscript — but
the link to works (`P1574`) exists on only 152/897 items, so authorship is
missing everywhere for 83% of the corpus.

**Changes.**
1. Ensure every manuscript links to its work: `P1574` (exemplar of) from
   contents/subject evidence. Sites:
   `backend/converter/wikidata/content_projection.py`,
   `manuscript_projection.py` (P1574 emission), `item_builder.py`.
2. Put authors on the WORK items: `P50` (linked authority QID) else
   `P2093` (author name string) per Rule W-69/W-209. Sites:
   `manuscript_projection.py` (work creation), `authority` linking for
   `100$a`/`700$a` (Mazal/KIMA/VIAF matching is already in the pipeline).
3. Wire `245$c` (statement of responsibility) → `P2093` fallback.
4. For works already on Wikidata (Mishnah-style targets), author updates
   follow the consent/adopt path (W-270/W-272 — adopt own items, drawer
   for community items).

**Expected lift.** P50/P2093: 0% → ≥60% of works; P1574: 16.9% → ≥90% of
manuscripts. **Verify:** live query `?item wdt:P1574 ?work . ?work wdt:P50|wdt:P2093 ?a`
over the 897 P3959 items.

### WP2 — Subjects and genres

**Problem.** `650$a` subjects → P921 only 2.3%; `655$a` genre → P136 15.1%.

**Changes.** `manuscript_projection.py:601` (subjects → P921) — audit why
650 values drop (the 2026-07-16 audit: only `650$a/$0/$2` promoted;
subdivisions `$v/$x/$y/$z` lost). Extend the subject mapping to keep
qualifiers; promote `655` fully to `P136`. Sites:
`manuscript_projection.py`, `property_mapping.py`, `content_projection.py`.
**Expected.** P921 → ≥60% (650 present on most records), P136 → ≥80%.

### WP3 — Physical description

**Problem.** `300$c` dimensions 5.4%, `340$a` material 2.9%, `563$a`
binding 0%.

**Changes.** `manuscript_metadata.py`/`manuscript_projection.py` extent
block: parse `300$c` (cm) → P2048 height (+P2049 width when both sides
present); `340$a` → P186 (controlled material terms); `563$a` → P1552.
Fail-closed on unparseable dimensions (Rule R2/R4 family — never invent).
**Expected.** P2048 → ≥50%, P186 → ≥40%, P1552 measurable.

### WP4 — Places and production

**Problem.** `752$a` place of production → P276/P1071 1.8–5.1%;
`751$a` → P7153 7.2%; `260$a` place not promoted.

**Changes.** Promote `260$a/264$a` place → `P1071` (location of creation)
with `P276` where a geographic object is verified; `752` → same;
`751` → P7153. Institution places go through the audited holder table
(Rule W-143/W-161 — never default to NLI). `034` coords: map to P625 only
with verified place QIDs (Rule R4 fail-closed).
**Expected.** P1071 → ≥60%, P7153 → ≥40%. Re-verify against
`show_wikidata_places` (P625) map after.

### WP5 — Notes family

**Problem.** `520$a` summary → P7535 0%; `561$a` provenance → P3342 1.6%;
`500$a` → P1684 0.9%; `593$a` script → P9302 5.4%; `597` colophon
unmapped.

**Changes.** Promote `520` → P7535 (scope and content); `561` provenance
person extraction → P3342 (+P11811/P11812 where order is known, W-146:
never bare P127); `500` note classifier → P1684 (inscriptions) / P5102;
`593` script vocabulary → P9302; add a `597` colophon handler (either
P7535 text or a dedicated note target — decide in code comment).
**Expected.** P7535 → ≥50%, P3342/P1684/P9302 measurable gains.

### WP6 — Digital access

**Problem.** `856$u` → P953 only 12.8%; IIIF P6108 0.1%; `907` digital-file
identity unmapped.

**Changes.** `856$u` promotion to P953 for all digital links; IIIF manifest
detection → P6108; decide `907` fate (WP7). Sites:
`manuscript_projection.py` digital block, `property_mapping.py`.
**Expected.** P953 → ≥80% (856 present on many records).

### WP7 — Mapping corrections and decisions

1. **`710` → `P195` (collection), NOT `P50`** — 897/897 records carry the
   holding library; the current table's P50 row is wrong (measured 0%).
   Fix the projection to emit `P195` (collection) from `710`/`852`.
2. **`035`/`001` dual identity** — ensure `035$a` falls back into the same
   P3959 channel when `001` is absent.
3. **`880` linked fields** — resolve the linked-tag target (880 rows must
   project as their linked tag, not be dropped).
4. **RDA `336–338`** — decide: map content type to a P31 refinement or
   declare explicitly out-of-model in the coverage ledger.
5. **`505` is nearly empty (1 record)** — keep P1574 mapping but rely on
   WP1's work-linking from other evidence.

### WP8 — Catalog-only field policy (the 2026-07-16 standing risk)

For each unmapped field with data (`907`, `952`, `957`, `958`, `921`,
`934`, `915`, `943`, `903`, `999`, …): record one of three fates in a
coverage ledger (new page in the reports or `docs/`):
(a) map to a property, (b) attach as evidence/reference JSON on the item,
(c) explicitly out-of-model. No field stays silently absent.

### WP9 — Repeatable coverage harness (do first — it prices everything)

Promote the throwaway audit into a repo script
(`backend/scripts/marc_wikidata_coverage.py`) that: parses the TSV, joins
live claims by P3959, prints the per-field table + target thresholds, and
exits non-zero when coverage regresses. Wire one CI test that runs it
against the fixture corpus snapshot. This makes every WP's "expected lift"
measurable before/after.

## Order, sizing, verification

| Order | Package | Est. | Gate |
|---|---|---|---|
| 1 | WP9 harness | 0.5 day | baseline numbers reproduced in one command |
| 2 | WP1 authorship | 1–2 days | P50/P2093 ≥60% on works; P1574 ≥90% |
| 3 | WP7 mapping fixes | 0.5 day | 710→P195, 880, 035 tests green |
| 4 | WP2 subjects/genres | 1 day | P921 ≥60%, P136 ≥80% |
| 5 | WP3 + WP4 physical/places | 1 day | P2048 ≥50%, P1071 ≥60% |
| 6 | WP5 + WP6 notes/digital | 1 day | P7535 ≥50%, P953 ≥80% |
| 7 | WP8 ledger | 0.5 day | every field has a recorded fate |

**Verification protocol (after each WP):**
1. Run the WP9 harness on the 897-record corpus → compare with baseline.
2. Re-check live: `query.wikidata.org` for the moved properties (exact
   P3959 join; one record ↔ one item ceiling must hold).
3. Wikidata Studio AI-verify a sample of touched items (tier-1 judge).
4. Update `reports/marc-wikidata-field-coverage-17th-century.md` numbers.

**Risks / rules.** Never place authors on physical manuscripts (WPM model);
labels/descriptions are not claims (`246` stays aliases). Fail closed on
unparseable dates/dimensions/coords (Rules R2/R4). Creator/consent gates
unchanged: only own items update without consent (W-270/W-272); community
targets need the drawer. All new mappings need `property_mapping.py`
constants verified against the live property page (Rule R8) and a rule
entry in `docs/architecture/blocks/wikidata-studio/rules.md` + docs sync.
