# Wikidata Studio — duplicate detection + LOD interlinking plan

Status: **implemented 2026-08-02** (C1-C6), with two of the plan's own premises
disproved during the work — see "Corrections" below. Written 2026-08-01 against export (19)
of run `48ba6c13` (313 entities: 68 manuscripts, 105 works, 140 persons).

Every number below is measured from that export or from a live Wikidata query on
2026-07-31 / 2026-08-01 — none is an estimate unless it says so.

## Corrections made while implementing

Two claims in Part A / C4 below were **wrong**, and are left in place with this
notice so the reasoning stays auditable:

1. **"43 works emit `P2093` while we mint that person item in the same batch"** —
   false. Measured: **0 of 43** have a person item. Of the 43, 23 match an
   authority row on the same record and 21 of those are approved `role=author`,
   but **0 carry a VIAF or Mazal identifier**.
   `converter/wikidata/person_projection.py` therefore refuses to create them,
   citing **Wikidata:Notability** — an item with only a name invites community
   deletion. With no person item to link to, `P2093` is exactly what Wikidata's
   guidance prescribes. **Those 43 are policy-compliant, not defects.**
2. **C4's estimate of "~43 `P50` edges, persons linked 16 → 60-90"** — wrong for
   the same reason. C4 shipped as the underlying fix (the canonical context now
   stamps `marc_authority_matches` onto its records, which it never did) so `P50`
   follows wherever an identifier *does* exist, but it yields no edges today.

The real orphan cause was elsewhere and is what C5 fixed: **122 of 140 persons
carry `P214` (VIAF)** and are perfectly notable, while `ROLE_TO_PID` simply had no
entry for `former owner` (49 approved rows), `mentioned` (27) or `signatory` (13).
Every one was dropped with a log line. Fixing it needed **no new items at all**.

## Why these two problems are one problem

The duplicate check asks *"does an item with this identity already exist?"* The
LOD goal asks *"can this item be reached from another item?"* Both are blocked by
the same missing thing: **our entities do not carry resolved identity for the
things they point at.**

The clearest example: 65 of 68 manuscripts carry `P217` (inventory number) but
only **15 carry `P195`** (the holder as a QID) — even though the English label
names a holding institution for essentially all of them, across 22 distinct
institutions. Resolve those 22 names to QIDs once and you get *both* a new
duplicate key (`holder + inventory number`) *and* ~50 new outbound LOD edges.
That is the highest-leverage single change on this page.

## Part A — current state, measured

### A1. Duplicate probe coverage

`_IDENTIFIER_PIDS_BY_TYPE` in `app/pipeline/wikidata_duplicate_probe.py`:

| type | probed on | our coverage | verdict |
|---|---|---|---|
| person | `P214`, `P8189`, `P244`, `P227` | 133 of 140 carry an authority id | **adequate** |
| manuscript | `P3959` only | 68 of 68 carry it | **weak — see A2** |
| work | *nothing* | — | **absent** |

The module is honest about the gap: when it cannot probe it returns
`STATUS_SKIPPED` with the note *"absence of a duplicate is NOT established for
this item"*. That contract must survive every change below.

### A2. The `P3959` blind spot, proven

`P3959` (NLI manuscript ID) only finds items that *someone else* chose to record
an NLI number on. Live check of all 33 Samaritan manuscripts on Wikidata:

- 33 of 33 carry **no `P3959`**;
- they are identifiable instead by holder + inventory: `CAJS Rar Ms 75–117`
  (Penn, `Q49117`), `MS. Bodley Or. 651/663/699` (Bodleian, `Q82133`),
  Columbia `MS X893.153 K64`, Free Library `Lewis O 150`.

So for a manuscript held anywhere outside NLI's own numbering, `absent` means
"nobody recorded our number", not "the manuscript is not there".

Worked example — `QDraft_MS_990001404380205171`, *Cambridge University Library,
F 18760*, a Samaritan Torah (Exodus), 1639. Six live probes (`P3959`, `P217`,
`inlabel`, free text, Hebrew title, `P31`+`P195=Q1028334`) all returned 0, and no
Cambridge Samaritan item exists. **The conclusion was right and the pipeline's
own probe could not have reached it** — it would have run one `P3959` lookup.

### A3. The link graph

92 internal edges, **0 dangling** — the `__LOCAL:` resolver works. But 91 of 92
originate from a manuscript:

| edge | count |
|---|---|
| manuscript → work (`P1574`) | 76 internal + 15 to live QIDs |
| manuscript → scribe (`P11603`) | 13 internal |
| everything else | 3 |

Plus 636 links to real Wikidata QIDs (`P31`, `P407`, `P195`, `P921`, `P136`…) —
external anchoring is healthy.

Orphans (no inbound internal edge): **124 of 140 persons**, 30 of 105 works.

### A4. Why persons are orphaned

| works by author shape | count |
|---|---|
| no author at all | 61 |
| `P2093` author *name string* only | 43 |
| `P50` author *item* | **1** |

`P2093` is Wikidata's explicit "no item exists for this person" fallback. We emit
it **while minting that person item in the same batch**.

Root cause: **works carry 0 authority-evidence rows** (0 of 105), while persons
carry 133 of 140. The work projection has no identity for its own author.

### A5. Name matching is not an option

Tested: token-overlap matching of the 43 `P2093` strings against the 140 person
items yields 17 "matches", **all false**. Hebrew patronymics share tokens, so
`משה בן מימון` (Maimonides) matched `אליהו בן משה כרמי`. Minting `P50` from name
similarity would inject wrong authorship at scale — the failure mode Rule W-72
exists to prevent. **Any link must come from a shared identifier.**

## Part B — the join key we already have

Person items record their own MARC provenance:

```json
{"role": "scribe", "field": "700/710/711", "main_marc_tag": "100",
 "source": "mazal", "mazal_id": "987007263785105171",
 "preferred_name_heb": "כרמי, ישראל בן יוסף"}
```

And **46 of 52 work record-id sets overlap a manuscript's**. So the edge is
derivable with no string comparison at all:

> `(record_id, MARC tag) → person item` — a work built from record R whose author
> came from R's `100` links to the person whose authority row names record R and
> tag `100`.

The same key gives manuscript → `P127` former owner from the `541`/`561` rows.

## Part C — proposed work, in dependency order

### C1. Resolve the holding institution to a QID (unblocks C2 and C5)

An audited name → QID table for the 22 institutions in the corpus, built the way
Rule W-26 requires: **every QID fetched live before it is written down**, never
from memory, with the fetched label recorded next to it.

Corpus distribution (from the English label):

| n | institution |
|---|---|
| 13 | The Jewish Theological Seminary of America |
| 11 | Jerusalem, NLI |
| 7 | The British Library |
| 5 | The National Library of Israel |
| 4 | The Russian State Library |
| 3 | University Library Johann Christian Senckenberg |
| 2 each | Ben Zvi Institute; Institute of Oriental Manuscripts (RAS); Bodleian; Columbia; **Cambridge UL**; Leeds UL |
| 1 each | 10 more |

Already in use and to be re-verified, not assumed: `Q23308` (7), `Q188915` (5),
`Q82133` (2), `Q46815` (1). Verified during this analysis: Cambridge UL =
**`Q1028334`**; Penn = `Q49117`; Free Library of Philadelphia = `Q3087288`.

Fail closed: an unresolved institution emits **no** `P195` and, per C2, leaves the
manuscript probe on `P3959` alone with `STATUS_SKIPPED` semantics preserved.

Expected: `P195` 15 → ~65.

### C2. Second duplicate key for manuscripts: holder + inventory number

`haswbstatement:P195=<holder> haswbstatement:P217="<inv>"`, batched with `|` (Rule
W-119: CirrusSearch via the Action API, never WDQS on this path; `OR` does not
work in `haswbstatement`).

`absent` may only be reported when **both** keys were probed. One key answered
plus one key unavailable stays `skipped` — the A1 contract.

Depends on C1. Unblocks: the Penn/Bodleian-shaped collision class entirely.

### C3. Duplicate probe for works (the largest gap)

All 105 works carry `P1476`; only 2 carry an `existing_qid`. Probe by
`wbsearchentities` on the title, restricted by `P31`, and **discriminated by the
author's authority id where C4 has supplied one** (44 works).

This must return *candidates*, never an automatic match — a title is not an
identifier. The judge already caught the failure this prevents: we proposed a new
item against `Q623354`, the Passover Haggadah.

Expected: 105 works move from "no check ran" to a candidate list; from the 12
identifier-probe finds on 68 manuscripts, a double-digit count of works is likely
already on Wikidata. **That number is a guess** — C3 is what measures it.

### C4. Carry authority evidence onto the work projection → `P50`

Fix the loss first (Rule W-140: recover before generating), then emit
`P50 → __LOCAL:<person>` **only when both sides resolve to the same authority
identifier**. Where the matcher abstained (Rule W-84), keep `P2093` — that is the
honest statement, not a defect.

Expected: `P50` 1 → ~43; persons with an inbound edge 16 → 60–90 (**estimate**).

### C5. Remaining edges

- manuscript → `P127` former owner, from the `541`/`561` authority rows. The
  mining phase (Rule W-140, live since v358) now surfaces this prose; the owner
  becomes a *link* only via an authority id, never via the LLM's name string.
- `P195` for every manuscript resolved in C1.

### C6. Make all of it visible

Export (19) reports `duplicate_check: not_run` on all 313 items despite 207
cached probe rows — the read path never attaches it, so a curator cannot see the
answer where they review. Surface `absent` / `skipped` / `candidates_found` +
candidate QIDs per row in the export and the review table.

Also add to the offline gate (`scripts/check_wikidata_export_quality.py`):

- a `P2093` whose person **we emit in the same batch** → finding (guards C4);
- a CREATE manuscript with a resolved holder but **no** holder+inventory probe →
  finding (guards C2);
- a CREATE work with **no** duplicate probe at all → finding (guards C3).

## Part D — sequencing

| step | depends on | unblocks |
|---|---|---|
| C1 holder → QID (audited) | — | C2, C5 |
| C4 authority evidence on works | — | C3 discriminator, `P50` |
| C2 manuscript 2nd key | C1 | — |
| C3 work probe | C4 (partly) | — |
| C5 `P127` / `P195` edges | C1, C4 | — |
| C6 surfacing + gate checks | all | prevents regression |

C1 and C4 are independent and are the two roots — either can go first.

## Part E — rules to add

- **Internal edges MUST come from a shared authority identifier, never a name.**
  Cites the A5 measurement (17 of 17 token matches false).
- **`absent` MUST mean every key for that entity type was probed.** A partial
  probe is `skipped`. Extends the existing honest-negative contract.
- **A `P2093` is a defect when the person item exists in the same batch.**

## Part F — out of scope here, but found during this analysis

Two real defects on `QDraft_MS_990001404380205171`, both needing their own call:

1. **`P282 = Q33513` is wrong.** Q33513 is *Hebrew alphabet* (verified live). It
   is a Samaritan Torah; Samaritan script is **`Q1550930`** (verified live). May
   affect other items — needs a corpus count before any change.
2. **The record is provisional.** `590$a = רשומה זמנית` ("temporary record"), and
   `F 18760` is an **NLI microfilm number**, not a Cambridge shelfmark — so the
   label *"Cambridge University Library, F 18760"* asserts a shelfmark that does
   not exist (Rule W-82 identity). Proposal: fail closed on temporary records,
   and never present an NLI film number as a foreign holder's shelfmark.
