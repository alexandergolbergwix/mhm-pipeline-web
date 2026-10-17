# Publication Foreign-Block Resolution — Full Implementation Plan

Status: IMPLEMENTED (2026-10-17; F2 → F1 → F3 → F4). Target release: the
next live pass (run `3494ebf5-ceb0-4702-a94e-c8f10be7f251`, publication
`f0992dbf-…`, release 1849 entities / 107 blocks).
Written: 2026-10-17. Owner: Alexander Goldberg (curator) + agent sessions.

Implementation notes (2026-10-17):

- F2 shipped as `TargetObservation.method` + `PlanActionRecord.reconcile_method`
  + migration `0051_plan_reconcile_method`; the plan digest includes the method.
- F1 shipped as an additional `_is_our_item(…, our_p3959=…)` channel
  (manuscripts only) used by the uploader call-sites and the publication
  gateway, plus the `_write_once_sync` re-acceptance
  (`P3959_IDENTITY_ACCEPTED`). The publication `_reconcile_sync` ownership
  split passes `our_p3959`, so P3959-equal manuscripts observe
  `present_owned` and plan `update (present_owned)` at dry-run time (the
  F1 Change-3 "publication core maps the method" wording is realized as
  reconcile-time ownership — a bare method-string map would plan unverifiable
  updates for shelfmark collisions; the write gate stays the authority).
- F3 shipped as the pre-assessment sweep in
  `wikidata_publication_auto_job.py` + `route_blocked_target` /
  `shared_p3959` in `automatic_policy.py`.
- F4 shipped as `WikidataBlockedActionDrawer.tsx` (accept affordance for every
  blocked row, claims diff, reuse / accept & update / leave out) with
  `BlockedPlanAction.entity_label` + `our_statements`,
  `PlanSummary.accepted_foreign_update_count`; the batch consent checkbox flow
  is preserved (pinned e2e).
- Rule W-270 (CLAUDE.md + docs/architecture/rules/wikidata-studio.md);
  block page `docs/architecture/blocks/wikidata-studio/foreign-block-resolution.md`.
- Frontend unit tests written but not executed here (npm registry unreachable
  in the implementation environment); run `npm install && npm run test:unit`
  in `frontend/` before deploy.

---

## 1. Current situation (measured 2026-10-17)

The live release `f0992dbf` plans:

| Planned action | Count | Meaning |
|---|---|---|
| create (absent) | 1580 | No production item exists — write it |
| update (present_owned) | 162 | Our own earlier item — add claims |
| skip (present_foreign) | 0 | — |
| **block (present_foreign)** | **107** | A production item exists, pipeline refuses |

All 107 blocks carry the same detail:
`Foreign QID consent does not match the QID, revision, and entity digest`.

### Why every block says "consent does not match"

The dry-run marks an update-via-consent only when the curator's consent
(qid + remote_revision + entity_digest) equals the current observation.
None of the 107 got a consent (the drawer flow never ran — see F4),
so all stay `block`.

### The 107 by entity type

| Entity type | Count | What the targets are |
|---|---|---|
| manuscript | 16 | **Our own data**, created 2025-… by a former
project member (`Lapon-Kandelshein Essi`). Each carries our exact
P3959 control number (e.g. `Q134619072` → P3959 `990000533850205171`)
and MHM claim shapes (P31 manuscript, P1574, P407, P953…). |
| work | 54 | **Genuine scholarly matches** — the catalogue truly
contains Mishnah (Q191825), Aleinu (Q2240795), מסכת יומא (Q1063210),
Yitzhak Abuhav (Q6522889), זמירות ישראל (Q25636007)… All targets are
community-created (BeneBot*, MerlIwBot, Sk!dbot, GZWDer, נדב ס). |
| person | 37 | Mixed: (a) real persons with our authority IDs
(Yosef Ben-David Q16130567, Yosef Yitzchak Shloush Q8055907, Ludwig
Philippson Q88899), (b) **false string matches** — "David Sultan"
matched a politician (Q1176777), a scribe matched Mauricio Kagel
(Q178293). |

### Root causes (numbered — the fixes map 1:1)

**F-CAUSE-1 (code):** the ownership check (`_is_our_item`,
Rule 38 triple-verification) accepts only *same-account-created* items.
The 16 manuscripts were created by a former project member, so the
pipeline sees "foreign" although the P3959 control number proves the
item IS ours (the ID is globally unique to our catalog feed).

**F-CAUSE-2 (code):** when the **live final identity check** finds an
existing item at write time (`The final CREATE identity check found
Q…`), the block lands on the item — but the release entity never
receives `existing_qid`, so the AI-resolve flow (which keys reuse/defer
decisions off `existing_qid`) has nothing to act on. The 107 sit in
"need attention" with no designed resolution path executed.

**F-CAUSE-3 (code):** `Resolve with AI` judges only items whose
document matches the plan; the 107 blocks remain "needing attention"
instead of being deferred out of the release by the automatic policy.
The flow that should sweep them (blocked → defer with reason) exists in
`automatic_policy.decide()` but only runs for items whose observation
reached it with `existing_qid` present.

**F-CAUSE-4 (data, not code):** a handful of person matches are wrong
string matches (name collisions). These need the AI dual-judge (which
requires the primary-evidence fixtures) to say `different_entity`, then
defer. Not fixable by rule alone — by design.

### What is NOT the problem

- The MARC records themselves are fine — all 16 manuscripts carry valid
  P3959s; the 54 works match real items precisely by Hebrew title (+author
  where present).
- The fail-closed gates did their job: zero wrong writes reached the wiki.

---

## 2. Target situation

After implementing F1–F4:

1. **The 16 project-era manuscripts**: planned action becomes
   `update (present_owned)` — the P3959 identity override upgrades them.
   Their updates write cleanly (the same claims the pipeline verifies).
2. **The 54 genuine work matches + ~33 real person matches**: the AI
   resolve judges identity with the dual model; same-entity verdicts
   become **reference-only reuses** (skip, zero writes, curator-visible in
   the summary as "reused without updates") or, when the item needs our
   claims added, **foreign-consent updates** requiring the explicit
   drawer accept (checkbox → consent → update). Different-entity verdicts
   defer out of the release with a reason.
3. **The false string matches (~4 persons)**: AI says `different_entity`
   → defer; they leave the release, remain in Studio for curator
   follow-up, and the release publishes without them.
4. **Zero unexplained blocks.** Every item is either written, reused,
   consent-updated, or deferred-with-reason. The publication state
   machine returns to `publishable` with 0 blocks.

Success criteria:

- `publication_plan_actions` for the next release has no `block` rows
  that lack an executed path (update / reuse / consent / defer).
- The "Resolve with AI" flow processes ALL blocked items and reports a
  per-item decision (kept / reused / deferred) with reasons.
- The live execution finishes with the blocked residue count equal to
  only curator-consent items that were NOT accepted (explicitly chosen).

---

## 3. The fixes, in detail

### F1 — P3959 identity ownership override (code, high value)

**Files:** `backend/converter/wikidata/uploader.py`,
`backend/app/pipeline/wikidata_upload.py` (shared helper), tests.

**Rule:** an existing production item whose **P3959 (NNL catalog ID)
exactly equals the record's control number** is project-owned data,
regardless of who pressed the create button. The control number is
minted by our catalog feed (NLI/ ktiv), globally unique, and already the
manuscript identity key everywhere else in the pipeline (W-… ledger keys,
duplicate probe, verify evidence). The creator check stays as the
fallback; P3959 becomes an additional accepted channel **only for
manuscripts** (persons/works keep the strict creator check — their
identifiers are not project-minted).

**Change 1 — `WikidataUploader._is_our_item(qid, *, allow_p3959=False)`:**
add the optional channel: when `allow_p3959` and the item's P3959
matches the *current item's own* P3959 statement (passed in, not
re-fetched), return True. Implementation detail: `_is_our_item` is
called from many call-sites without item context, so instead:
- extend `_assert_modifiable(qid, stage, *, our_p3959: str | None = None)`
  to consult the channel;
- the two call-sites that HAVE the native item in hand
  (`upload_item`, `_write_once_sync` in the publication gateway) pass
  the item's P3959;
- inside `_is_our_item`, before failing on creator mismatch, accept when
  the cached P3959 map (qidgetentities claims → P3959 value) equals the
  passed value. Cache the fetched P3959 per QID as `_q3959_cache`.

**Change 2 — the publication gateway preflight** (`_preflight`,
`_write_once_sync`, `_confirm_mutation_sync` in
`app/publication/wikidata_gateway.py`): when the uploader is live and
the mutation's native item carries P3959, the `UnauthorisedModificationError`
from adoption/creator checks converts to an accepted update IF the
remote item's P3959 equals ours. Concretely: catch
`UnauthorisedModificationError` in `_write_once_sync`, fetch the target
claims (already have `_wbgetentities`), compare P3959, and on equality
proceed as `present_owned` (log `P3959_IDENTITY_ACCEPTED qid=…`).

**Change 3 — the reconciler**: `reconcile_manuscript_by_identifiers`
already returns the QID for a P3959 match — no change. The dry-run
already observes `present_foreign` for it; the ownership re-class
happens at write time. Additionally, in `_reconcile_sync` (upload path)
stamp `outcome.method = "p3959/shelfmark"` (already does) — the
publication core maps a `p3959/shelfmark` method to allow-owned-update
(see F2 note on observations carrying method).

**Guard:** the P3959 channel NEVER applies on test.wikidata.org (there,
P-numbers are remapped and P3959 may not exist) and NEVER applies when
the remote item LACKS P3959 entirely (absence is not identity). Two
independent values must be equal — no prefix/fuzzy compare.

**Tests** (`tests/unit/test_wikidata_upload_guards.py`,
`tests/unit/test_wikidata_publication_gateway.py`):
- p3959 equal → update proceeds; p3959 absent on remote → refused;
  p3959 differs → refused; test wiki → strict creator behavior
  unchanged; person/work entity types → strict behavior unchanged.

---

### F2 — Stamped observations carry the reconcile method (code, enables F4)

**Files:** `app/publication/types.py` (PlanActionRecord / TargetObservation),
`app/publication/core.py` (dry-run), `app/publication/sql_repository.py`
(row persistence — observation_status column already exists; add
`reconcile_method TEXT NULL` to `publication_plan_actions` via a
migration), `modal` none.

**Why:** F3/F4 need to distinguish *how* a target was found:
- `identifier` / `p3959/shelfmark` (strong: catalog-ID equality), vs
- `label` (title-string match — needs the AI judge), vs
- unknown (the live preflight found it mid-write).

**Change:** `TargetObservation` gains `method: str | None`;
`reconcile_batch` fills it from the ReconcileOutcome method
(`_reconcile_sync` already produces it); `PlanActionRecord` gains
`reconcile_method`; the dry-run stores it; the SQL repo round-trips it.
The reuse cache (F-obs) round-trips it too (the digest governs reuse,
method just rides along).

**Migration:** one nullable column; no backfill needed (NULL = legacy).

---

### F3 — Sweep unexecuted blocks out of the release (code, closes the UX hole)

**Files:** `app/pipeline/wikidata_publication_auto_job.py`,
`app/publication/automatic_policy.py` (helper), tests.

**Rule:** an item blocked with NO `existing_qid` on its release entity
(the F-CAUSE-2 class) cannot be judged by the existing reuse flow; the
automatic policy must defer it deterministically instead of leaving it
"needing attention". The AI resolve, when it runs with scope=blocked,
ALREADY handles items that HAVE `existing_qid` (reuse/defer). The gap is
items whose plan observation carries a QID but whose entity document
does not.

**Change (in the auto job, before assessment):**
1. Load the plan's blocked actions.
2. For each blocked entity WITHOUT `entity.document.existing_qid`:
   - Fetch the target item (one `wbgetentities` batch — the gateway
     `fetch_entity`).
   - If **P3959 identity** (manuscript, F1) → mark `update_existing`
     (the entity document gets `existing_qid` stamped — this makes the
     F1 write path available in the SAME release).
   - Else if the target has a **shared identifier** with our item
     (P214/P8189/P244/P227/P213/P268 on persons; P3959 on works;
     shared_identifiers() in automatic_policy) → route to the AI judge
     as today (existing flow) — it needs primary evidence, so keep the
     dual-judge requirement.
   - Else (label-only match, unknown provenance) → **defer** with
     reason "A live-wiki item exists but shares no verifiable
     identifier; curator review required." — the item leaves the
     release cleanly and shows in Studio for manual accept.
3. Progress reflects the sweep phase (message: "Sweeping N blocked
   items: M re-routed, K deferred").

**Guard:** the sweep NEVER accepts a foreign item implicitly. Reuse
(reference-only) requires the AI dual-judge same-entity verdict on
identifier-verified pairs (unchanged). The sweep only:
stamp-existing-qid for F1 manuscripts, defer the rest. The drawer
consent path remains the explicit human gate for community-item updates.

**Tests** (`tests/test_publication_router.py` automatic-resolution
fixture): blocked entity with no existing_qid + P3959 equal →
update_existing stamped; blocked entity with no shared identifier →
deferred with the reason; blocked entity with shared person identifier →
still routed to the judge (fixture asserts the judge was invoked).

---

### F4 — Drawer accept for foreign updates, everywhere the block surface shows

**Files:** `frontend/src/components/wikidata/WikidataPublicationPanel.tsx`,
`WikidataPublicationControls.tsx` (blocked-actions detail list),
`app/routers/publication.py` (consent endpoint — already exists), tests.

**Current state:** the consent flow works end-to-end
(`PublicationForeignQidConsent` schema + dry-run matching + gateway
`register_foreign_accept`), and the drawer exposes it for items that
HAVE `existing_qid`. The 107 never reached it (F-CAUSE-2). After F3
stamps what it can and defers the rest, the REMAINING accept-cases are
curator choices: "this community item (Mishnah) IS our work — reuse or
update it".

**Change:**
1. The blocked-item detail rows render an **"Accept existing item"**
   affordance for every blocked entity (not only those with a stamped
   existing_qid): clicking it opens the drawer with the REMOTE item
   (qid + revision from the plan action's target_qid/target_revision —
   both already stored), shows a diff (our claims vs remote claims),
   and offers:
   - **Reuse without updates** (reference-only skip — zero writes), or
   - **Accept & update** (writes the consent: qid + remote_revision +
     OUR entity_digest — the digest check stays; the curator accepts
     our claim-set going onto their item), or
   - **Defer** (leave it out of this release).
2. The consent POST refreshes the plan (the dry-run re-run is required
   by design; the run-scoped observation cache makes it fast).
3. The summary line gains "reused without updates: N · accepted
   foreign updates: M" so the curator sees the outcome class split.

**Guard (unchanged, W-…):** an accepted foreign update still requires
the curator's sealed consent — no automatic path writes onto a
community item. The F1 manuscript class is project data and bypasses
this (it is not community data); everything else keeps the human gate.

**Tests:** panel-level — the accept affordance appears for a blocked
entity with target_qid but no entity existing_qid; consent round-trip
stamps the plan action (reuse/consent) and the dry-run plans
skip/update accordingly.

---

### F5 — (already deployed; listed for the plan's completeness)

Deployed during this session and NOT to redo:
- Run+target-scoped observation cache (re-checks skip unchanged items).
- Subset seed for the AI resolve (no full re-reconcile per retry).
- Transient WDQS 3× retry + one in-dry-run re-check of unknowns.
- W-182 test-wiki remap on publication writes + read-back confirmation
  through the adapted shapes; label-conflict adoption on test.
- Deterministic API rejections classify as blocked (no churn).
- 16 parallel writers on Modal (8 GB container, deploy-surviving).
- Bounded WBI write retries (3 × 15 s; maxlag cannot park the pass).
- Ledger recording of created QIDs + projection merge (phase-2 links).

---

## 4. Execution order & sizing

| Step | Fix | Files touched | Risk | Est. |
|---|---|---|---|---|
| 1 | F2 method stamping | types/core/sql repo + migration | low | 1–2 h |
| 2 | F1 P3959 override | uploader + publication gateway | med (ownership) | 2–3 h |
| 3 | F3 block sweep | auto job + policy helper | med | 2–3 h |
| 4 | F4 drawer accept affordance | frontend + router tests | low-med | 2–3 h |
| 5 | Full local test sweep + Heroku/Modal deploy | — | — | 0.5 h |
| 6 | Re-run: prepare → resolve-with-AI (sweep) → check → publish | runtime | — | ~30 min |

Steps 1→2→3 are dependency-ordered (F3 uses F1's acceptance and F2's
method stamp). F4 is independent of 1–3 and can ship in parallel.

## 5. Explicit non-goals

- No loosening of the creator check for persons/works (community safety).
- No automatic writes onto community items (consent stays human).
- No deletion/merge of wrong person matches in this pass — the AI defers
  them; fixing the reconciler's person disambiguation (name + birth-year
  evidence) is a separate follow-up.
- No change to the moratorium posture: live writes only through the
  explicit live toggle (already the case).

## 6. Verification protocol (after deploy)

1. `prepare` → confirm the plan shows 16 manuscripts as
   `update (present_owned)` (F1 through the sweep, F3 step 2).
2. `Resolve with AI` → expect per-item report: ~33 persons + ~54 works
   judged (reuse/defer/consent), ~4 false matches deferred, 16
   manuscripts stamped update.
3. Dry-run → **0 unexplained blocks** (consent cases remain blocked
   until accepted — curator clicks through the drawer list; each click
   re-plans).
4. Publish → the audit shows: created ~1580, updated ~162+16,
   reused-without-updates ~23+K, deferred = the false matches only.
5. `backend/scripts/check_wikidata_export_quality.py` for the compact
   read-only audit; then the phase-2 linking release (ledger wiring
   already deployed).
