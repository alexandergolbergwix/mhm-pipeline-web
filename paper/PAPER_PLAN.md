# Paper plan — scalable quality assurance for large-scale Wikidata/Wikibase item creation

Status: outline v1 (2026-09-21). All numbers come from the measured runs in
this repo (see §7 data sources). Target venues TBD — see open questions.

## Working title (options)

1. "Beyond the Human Eye: A Three-Layer Verification Architecture for
   Large-Scale Scholarly Wikidata Item Creation"
2. "Rules, LLM Judges, and RLCD Decision Models: Scalable Quality Assurance
   for Manuscript-to-Wikidata Pipelines"
3. "Certifying Machine Judges: Escalation Policies for Human-Eye-Less
   Verification of Linked Data Item Creation"

## Core thesis

Curator-scale linked-data pipelines (here: Hebrew manuscript MARC records →
RDF → project Wikibase → public Wikidata) can replace the human eye with a
**three-layer verification architecture** — deterministic rule engine,
generative LLM judge, and RLCD-trained decision model (TypeSafe Jev) —
*if* the layers are assigned responsibilities by measurement: mechanical
artifacts go to code, semantic judgment to models, and model uncertainty to
a confidence-gated escalation policy. A **certification methodology**
(curator/LLM gold set, unsafe-approve metric, acceptance gates, double-run
determinism) decides which judge may act, and no judge auto-approves until
the gates pass.

## Contributions

1. **Architecture**: three-layer verification (rule engine → LLM judge →
   RLCD decision model) with per-layer responsibility assignment, mapped
   onto a production 5-stage pipeline with event-versioned curator review.
2. **Certification methodology**: gold-set construction (1,238 rows, full
   populations), the unsafe-approve metric (the only error direction that
   ships bad items), acceptance gates (accuracy ≥ 0.98, unsafe = 0,
   determinism ≥ 0.99), and a tuning loop that converts every gold miss
   into either a deterministic check or a rubric-encoded question rule.
3. **Empirical findings** (measured on production data):
   - LLM judges are **inconsistent on identical defects** (the same
     description artifact flagged on some rows, missed on 12 identical
     rows); atomic decomposition of the rubric into typed questions makes
     the RLCD judge uniform.
   - **Deterministic code beats model judgment for mechanical artifacts**
     (parenthesis balance, identifier-CN vs description-CN mismatch,
     quote-collision garbles, truncated folio ranges) — moving these from
     the model to code removed 10 of 11 unsafe approvals.
   - **Confidence gating is the safety mechanism**: calibrated
     probabilities turn "unsure" into review-routed partials; a hard
     LLM-style fail would have shipped nothing but routed everything.
   - **Escalation policy**: RLCD judge primary, LLM as exception handler
     for non-high-confidence rows → ~85% LLM spend reduction at equal-or-
     better safety (Jev 0.65% unsafe vs Kimi 2.4% on its subset).
   - **Cost/speed Pareto**: 0.4 s/item vs 9–22 s/item (~100–250×
     wall-clock); per-item cost depends on the LLM rate — Jev buys time
     and structure, LLMs buy cheap tokens and free-text audit trails.

## Section plan

1. **Introduction** — GLAM linked-data publication bottleneck; why item
   creation is the risk surface (public, identity-bearing, hard to undo);
   the human-eye scaling problem; contribution list.
2. **Background & related work** — Wikidata/Wikibase quality literature;
   SHACL/validation; LLM-as-judge (position bias, consistency findings);
   LLM calibration & abstention; RLCD / non-generative decision models
   (System One); human-in-the-loop escalation patterns. *(needs a lit pass)*
3. **The pipeline** (system paper section) — 5 stages (AI extraction →
   authority enrichment → RDF/HMO ontology → project Wikibase → public
   Wikidata), event-versioned curator review, job/cache infrastructure.
4. **Layer 1 — deterministic rule engine**: 30 rules (SHACL wrappers,
   class/claims presence, MARC grounding, duplicates, language hygiene,
   fail-closed API checks); blocking vs advisory; why code must own the
   mechanical artifact class (with the 10-of-11 unsafe-approval result).
5. **Layer 2 — LLM-as-judge**: rubric engineering from production
   incidents; agentic tool-loop vs linear modes; evidence packs; the
   inconsistency findings; token/latency costs (22 s/item, 3 h per 500).
6. **Layer 3 — RLCD decision model (Jev)**: state+questions paradigm;
   atomic decomposition of rubric axes; per-claim statement checks;
   confidence gates; the structured-checks UX (rule-panel parity);
   latency (0.4 s/item) and cost profile.
7. **Certification methodology**: gold construction (who labels, flags,
   reconciliation of labeler disagreement — the truncated-folio class),
   unsafe-approve as the primary metric, gates, double-run determinism,
   the tuning loop (each miss → deterministic check or question rule).
8. **Evaluation**:
   - E1 head-to-head on identical fixtures (agreement, latency, cost)
   - E2 certification vs gold per channel (accuracy, unsafe-approve,
     safe-miss; full populations for NER/wikidata, stratified HMO)
   - E3 ablation: model-only vs model+code gates (11→1 unsafe)
   - E4 escalation policy simulation (LLM spend vs safety)
9. **Production policy & deployment** — Jev primary, LLM exception
   handler, no auto-approval pre-certification; curator UX parity.
10. **Limitations** — gold labeled by a strong LLM (curator countersign
    pending); Hebrew-heavy states on an English-primary model; single
    domain (manuscripts); determinism 0.966 vs 0.99 gate.
11. **Future work** — curator countersign; multi-domain replication;
    learned question policies; self-consistency voting for borderline
    axes; serving the checks list in the curator drawer.

## §7 Data sources (already measured)

- Certification: `state/typesafe-bakeoff/gold-v1/` (1,238 rows) +
  per-channel `bakeoff_report_*.json` (accuracy, unsafe, safe-miss)
- Head-to-head: 40-item/channel runs (agreement, latency, tokens)
- Kimi token truth: eval-agent run `manifest.json` stats (313k in / 9.5k
  out for 40 HMO items; 14m48s wall)
- Determinism: double-run n=500 (0.966/0.99)
- Rule engine: `backend/app/pipeline/rule_verify/` (30 rules) +
  `rule-verify.md` (advisory contract, fail-closed API rules)
- Incident-driven rubric evolution: docs/architecture/rules/ (W-1…W-253)

## Decisions (2026-09-21)

1. **Venue: Semantic Web** — ISWC/ESWC resource track or Semantic Web
   Journal; LaTeX (sw-journal / ceurart template per venue).
2. **Ground truth: curator countersign** — a 200-row stratified countersign
   set is exported at
   `state/typesafe-bakeoff/jev-vs-qubrid/curator_countersign_200.jsonl`
   (67 full / 66 partial / 67 fail; both judges' verdicts + gold inline;
   the curator fills `countersign.overall` + `agree_with_gold`).
   E2 reports gold accuracy against curator-confirmed labels.
3. **Cost: formula** — tokens + latency measured; Qubrid rate as a
   parameterized formula with a worked example.

## Resolved (2026-09-21): venue, blindness, scope

4. **Scope: ONE systems paper.** The certification methodology is the
   differentiator; splitting it off dilutes both halves. Target the
   **ESWC In-use track** (deployed semantic system + real evaluation —
   exact fit for a production pipeline with measured results) with the
   ISWC 2027 In-use/Resource track and Semantic Web Journal as fallbacks.
5. **Blindness: draft double-blind from line one.** ESWC/ISWC research and
   in-use tracks are double-blind: no repo URLs, no institution names
   (write "a national-library Hebrew-manuscript pipeline"), anonymize
   deployment numbers where identifying. Keep an unblinded copy for SWJ.
6. **Timeline (deadlines to confirm when calls post — ESWC 2027 site not
   live yet):**
   - ESWC historically: abstracts ~Oct 6, papers ~Oct 13 → **~3 weeks out
     from today**. Realistic only if the countersign lands this week.
   - Fallback A: ISWC 2027 (research ~Apr, resource/in-use ~Jun 2027).
   - Fallback B: SWJ (rolling; slower review, no deadline pressure).
   - Recommendation: draft the full paper now against the SWJ/ISWC
     structure; submit to ESWC 2027 In-use **only if** the countersign +
     related-work pass finish by the deadline; otherwise ISWC 2027.
7. **Related-work pass is the remaining unbuilt section** — needs a lit
   sweep (LLM-as-judge consistency, SHACL validation, Wikidata quality,
   RLCD/calibration, HITL escalation) in a dedicated session.

## Drafting note

This session is at its context limit. Start a fresh session with:
"Read paper/PAPER_PLAN.md and draft the paper per the plan" — the plan
file + the JSON sources under state/typesafe-bakeoff/ carry everything.

## Open questions (remaining)

1. **Venue pick within SW** — ISWC/ESWC resource track (deadline-driven)
   vs Semantic Web Journal (rolling)? Determines template + page budget.
2. **Anonymity** — double-blind? (masks repo/deployment references)
3. **Scope** — one systems paper (all three layers) vs splitting the
   certification methodology into a separate short paper?
