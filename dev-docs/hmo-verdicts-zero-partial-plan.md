# Plan: HMO Wikibase items → 0 partial / 0 fail

Source: export (5) — `run-48ba6c13-…-hmo-wikibase-items (5).json`, judged
2026-07-10 08:53 UTC by Kimi K2.5 (Qubrid), override cache.
Scorecard: **1750 pass / 155 partial / 4 fail** of 1909 (91.7% pass, up from
71% pre-W-52-rubric).

## Critical context: this verify ran against the OLD build

Every one of the 56 Production partials still shows the pre-W-52 label
(`Production 990000403370205171`) and the old one-line description. The v239
deploy (Rule W-52) landed, but the run's **items were not rebuilt** before
this verify. The judged_at timestamps are uniform (08:53), i.e. one pass over
stale item data.

**Step 0 (ops, before any new code):** RDF rebuild → HMO **Rebuild (skip
cache)** → re-verify (no override cache; salt `w52_v1` auto-invalidates).
That alone should clear the label-artifact clusters already fixed in v239:
dangling `)` labels (incl. the `יקרא)` **fail**), `Latin-in-he` labels
(Poland / Jewish law / Epstein / Gaster / Hebron / Mantua…), Time-Span bare
years, tradition language tags. Measure the true post-W-52 baseline first —
the plan below targets what **survives** that rebuild.

---

## Verdict anatomy (all 159)

| (name_ok, type_ok, role_ok) | count |
|---|---|
| (partial, yes, n/a) | 132 |
| (partial, yes, yes) | 22 |
| other | 5 |

It is almost entirely a **name_ok=partial** problem: labels/descriptions
judged thin, malformed, or wrong-language. Cluster sizes: Production 56,
Expression 23, Person 19, Work 18, TextTradition 9, Manuscript 7,
Paleographical_Unit 7, E74_Group 7, SubjectType 6, Place 4, misc 3.

---

## P0 — Correctness regressions to fix FIRST (shipped in v239, wrong)

### P0.1 `אבן` strip corrupts Ibn-names ⚠
`clean_person_display_name('סיד, יצחק אבן')` currently → `'סיד, יצחק'` —
the W-52 dangling-particle strip deletes the **Ibn** that belongs to the
surname (`יצחק אבן סיד`, Ibn Sid; same for `חביב, שמעון אבן` → Ibn Habib).
A trailing `אבן` after `Surname, Given` is an **inverted heading**, not a
dangling particle.

**Fix** (`rdf_helpers.clean_person_display_name`): replace the `אבן` strip
with uninversion — `^(?P<sur>\S+), (?P<given>.+?) אבן$` →
`{given} אבן {sur}` (yields `יצחק אבן סיד`). Keep the plain dangling-`בן`
strip (that one IS a truncated patronymic: `לוב, יצחק בן`). Tests: both
Ibn cases + the existing `בן` case must keep passing.

### P0.2 MARC 710 blindly forces `organization` → 3 of the 4 fails
`infer_person_type({'name':'Sassoon, David Solomon','field':'710'})` →
`organization`. NLI catalogs famous former-owner **persons** (Adler, Roth,
Sassoon) under 710, so they exported as `E74_Group` with `class_qid` Q28 —
the judge correctly failed all three.

**Fix** (`rdf_helpers.infer_person_type`): field-based inference must not
override an obviously personal name. New order: explicit `type` wins →
institutional keyword in name → `organization` → else if name matches the
personal-comma format `Surname, Given` (`^\S+, \S+`, no digits, no
institutional keyword) → `person` **even when field is 710/610/810** →
else field-based. `Gaster, Moses Collection` stays org ("collection"
keyword). Tests: Adler/Roth/Sassoon → person; Gaster Collection / Library
of… → organization.

---

## P1 — Substance for event/structural entities (≈63 partials)

### P1.1 Production events with NO place/date/scribe (56 — largest cluster)
All 56 have **zero** production substance in the record (no
`production_place`/`production_time`/`has_scribe` links — verified against
deferred_links+claims). The W-52 label becomes `Production of MS {cn}` but
the comment stays `Production event for manuscript {cn}.` — the judge will
still call that "merely repeats the label".

**Fix (build)** — `graph_builder._add_production_event`: when
`detail_parts` is empty, ground the comment in what IS known:
`Production of manuscript {cn} ('{ms title}', shelfmark {shelfmark});
production place and date are not recorded in the catalog record.`
(title/shelfmark from `data`; omit missing parts; never fabricate).

**Fix (rubric)** — `hmo_wikibase_item.md` rule 3d addendum: *when the
description states that the catalog record carries no production
place/date/scribe, that IS substantive (an honest negative finding) →
`name_ok = yes`.* Mirror in the evaluator's `SYSTEM-LABELED EVENT`
grounding text.

**Fix (gate)** — `hmo_export_quality`: flag
`production_description_repeats_label` when the description is exactly the
old `Production event for manuscript {cn}.` template.

### P1.2 Paleographical_Unit (7)
Label and description are identical system text. `build_graph`'s PU loop
already knows `script_values[idx]` and the scribe URI/label.

**Fix**: comment `Paleographical unit {n} of manuscript {cn}: script
{script_type}, scribe {name}.`; when neither known, `…: script and scribal
attribution not recorded in the catalog.` Optionally add
`Paleographical_Unit` to the evaluator's `_STRUCTURAL_ENTITY_TYPES` (they
are structural subdivisions).

### P1.3 TextTradition minted for "Unidentified textual content" (subset of 9)
The unidentified-content fallback Work flows into `work_expression_pairs`
and the philological loop mints a circular tradition
(`Textual tradition of the work 'Unidentified textual content of MS …'`).

**Fix** (`build_graph` philological loop): skip pairs whose title starts
with `Unidentified textual content` (or tag the fallback pair dict with
`"synthetic": True` and skip on that). No tradition/witness/bridge for
synthetic placeholders.

---

## P2 — Label hygiene, second pass (≈25 partials)

### P2.1 Interior ISBD quotes that mimic gershayim
`הוא תיקוני עוונות" ו"שער הנבואה` survives sanitization: quote count is
even (parity check passes) and `ו"ש` matches the gershayim guard (quote
between two Hebrew letters). Same family: `ו"יזכור" לאישים…`.

**Fix** (`rdf_helpers`): targeted normalization **before** the gershayim
guard: replace `X" ו"Y` (`r'(?<=[֐-ת])" ו"(?=[֐-ת])'`)
→ `X ו"Y`→ actually drop both quotes → `… עוונות ושער הנבואה`? No —
titles quote work names legitimately. Correct transform: this pattern is a
*conjunction of two quoted titles with the outer quotes already stripped*;
restore balance by **dropping all straight quotes not forming gershayim
inside a single word** when the first quote in the string closes nothing
(i.e. text before the first quote contains ≥2 words). Implement as: if
label doesn't start with `"` and contains `" ו"` → strip every quote that
is preceded/followed by a space (word-boundary quotes), keep true
gershayim (mid-word). Verify against: `שד"ל` (kept), `כמה"ר` (kept),
`הוא תיקוני עוונות" ו"שער הנבואה` → `הוא תיקוני עוונות ושער הנבואה`
*(check: the `ו"ש` quote is mid-word → escapes the word-boundary rule;
needs the paired `" ו"` handler specifically: collapse `'" ו"'` → `' ו'`
first, then parity check)*. Add all three as test fixtures.

### P2.2 Pipe-separator leftovers
`…ממנטובה"|נדפס בעילום שם ע"י שד"ל במחזורו` — `|` glues a publication
note onto the title. **Fix** (`clean_marc_label` or
`parse_contents_entry`): truncate the title at the first `|`; the tail is
a note, not part of the title.

### P2.3 Single-token vav-prefix fragments as Works
`ותשובת` ("and the responsa-of…") is a 505 continuation fragment.
**Fix** (`is_descriptive_content_title`): reject single-token titles
starting with `ו` whose remainder is a construct-state fragment (≤6
letters after the vav). Conservative: only single-token; multi-word titles
starting with vav stay.

### P2.4 Very long ISBD titles as Expression/Work labels
`שער שברי לוחות : פירוש המסורת אשר חבר…` (100+ chars) keeps the full
subtitle chain in the label; the judge wants the short scholarly title.
**Fix** (`_add_work`/`_add_expression`/`_add_content_work`): when the
sanitized title exceeds ~80 chars and contains ` : `, use the pre-colon
head as the label; put the full title in the description (and optionally
an alias). Put MS scope FIRST in expression comments (mirror the W-52 work
pattern) so 250-char truncation can never eat it.

---

## P3 — Person labels & descriptions (≈19 partials)

1. **Truncated Hebrew vs fuller English** (`מימרן, אלי` he vs
   `מימרן, אליהו` en): when both the MARC heading and the authority
   `preferred_name_heb` land as `he` labels, the exporter picks
   arbitrarily. **Fix** (`_labels_for_node` or `_add_person`): prefer the
   **longest** Hebrew form as the primary `he` label.
2. **Inverted-heading display** (`נשיא, דוד בן אהרן` flagged
   "non-standard order"): decide policy — (a) rubric line "MARC heading
   format `Surname, Given` is the scholarly standard → name_ok=yes", or
   (b) uninvert for display (`דוד בן אהרן נשיא`) keeping the heading as
   alias. **Recommend (a) first** (zero risk); (b) only if the judge keeps
   flagging after re-verify.
3. **Subject-person one-liner descriptions** (`מאוריציו`): enrich
   `_add_subject` person branch: `Subject heading (person) '{name}' from
   MARC 600 on manuscript {cn} ('{ms title}').` + rubric note that
   subject-heading persons need no biographical substance.
4. **Org-worded person descriptions**: `_stamp_wikibase_comment` for
   contributors says `Person '{name}' …` even for `E74_Group` — make it
   org-aware (`Collection/Institution '{name}' (former owner) …`).

---

## P4 — Manuscripts, subjects, places (≈17 partials)

1. **Generic one-word MS titles** (`תורה`, `תכלאל` ×7): after rebuild the
   `en` `Jerusalem, NLI, {shelfmark}` label exists. Add: when the he title
   is a single token, disambiguate it too — `תכלאל ({shelfmark})`. Plus
   rubric: a manuscript whose `en` label carries the shelfmark is
   sufficiently identified even when the `he` title is generic.
2. **SubjectType / Place minimal descriptions** (`Jewish magic`,
   `E53 Place linked to manuscript X`): the bare-fallback places come from
   creation sites that never stamp a comment — audit every
   `E53_Place`-minting site (subject places, provenance-event places at
   `graph_builder:1037`, institution seats at `:1981`) and stamp
   `Place '{name}', production/provenance/holding location of manuscript
   {cn}.` For SubjectType, description already lists MS linkage after
   rebuild; add rubric note that a controlled-vocabulary term + MS linkage
   is complete.

---

## P5 — Rubric + salt + expectations

- Rubric additions (3d addendum for empty-production, subject-heading
  persons, MARC-heading name order, generic-MS-title-with-shelfmark,
  vocabulary terms). Bump `HMO_ITEM_VERDICT_SCHEMA` → **`w53_v1`**.
- **Honesty note:** an LLM judge is not deterministic — a handful of
  borderline `partial`s may flip run-to-run. The realistic target is
  0 fails + partials only on genuinely ambiguous catalog noise; treat any
  remaining partial as a curator queue, not a build defect. If the same
  item passes on re-judge, it was judge variance, not code.

---

## Execution order

| # | Work | Files | Est. impact |
|---|---|---|---|
| 0 | **Rebuild run + re-verify** (no code) — true baseline | — | clears old-label clusters incl. 1 fail |
| 1 | P0.1 Ibn uninversion + P0.2 710-person inference | `rdf_helpers.py` | 3 fails → 0; protects Ibn names |
| 2 | P1.1 empty-Production comments + rubric 3d addendum + gate code | `graph_builder.py`, rubric, `hmo_export_quality.py` | ~56 partials |
| 3 | P1.2 PU comments, P1.3 skip synthetic traditions | `graph_builder.py` | ~10 |
| 4 | P2 label hygiene second pass (quotes/pipe/vav/long-titles) | `rdf_helpers.py`, `graph_builder.py` | ~25 |
| 5 | P3 person label policy + descriptions | `graph_builder.py`, rubric | ~19 |
| 6 | P4 MS/subject/place descriptions | `graph_builder.py`, rubric | ~17 |
| 7 | Salt → `w53_v1`; tests; docs (Rule W-53, blocks R-bumps) | cache module, tests, docs | — |
| 8 | Deploy → production rebuild → full re-verify (`--persist-verdicts`) | ops | measure |

Tests to extend: `test_rdf_helpers.py` (Ibn, 710-person, quote/pipe/vav
fixtures), `test_graph_builder_codicological_labels.py` (empty-production
comment, PU comment, no synthetic traditions, long-title head),
`test_hmo_export_quality.py` (`production_description_repeats_label`),
`eval-agent/tests/test_hmo_wikibase_items.py` (rubric grounding).

Docs per task-index: `rdf-graph` (R17), `hmo-wikibase-studio` (R26),
`eval-agent` (R20), CLAUDE.md **Rule W-53**, pointer bumps.

Mirror caveat: `backend/converter/` edits still owe a hand-port to the
desktop pipeline repo (Rule W-43 residual) — do not run the sync script.
