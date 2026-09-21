# Jev-Primary Rollout — full path to RLCD-based verification in production

> Up: [Eval-Agent AI Verification System](README.md) ·
> Companion data: `state/typesafe-bakeoff/` · Harness:
> `backend/scripts/typesafe_bakeoff.py` (all measured functions to port live here)
> Plan written 2026-09-21; implement in a fresh session. Status:
> **steps 1–8 implemented** (2026-09-21) — Jev selectable as a tier-1 model
> (`typesafe/jev-1.13.0`), deterministic gates + escalation policy wired,
> escalation default OFF. Step 9 (rollout sequence) not started.

## Goal and policy (fixed, from the 2026-09-20/21 measurements)

1. **Jev (RLCD) = primary judge on every verify channel** — every row is
   judged by Jev; verdicts are structured check lists with calibrated
   confidence.
2. **LLM (Qubrid Kimi) = exception handler only** — it re-judges rows that
   are *not* a high-confidence `full` (measured 15–25% of rows). LLM
   workload drops ~75–85%.
3. **No auto-approval by any judge** until per-channel certification
   passes: accuracy ≥ 0.98 vs gold, unsafe-approve = 0, double-run
   determinism ≥ 0.99. SHACL stays the only upload gate.
4. Cost/speed basis (console-verified): Jev input **$0.042/Mtok
   ($42/Btok)**, output free; 0.37–0.61 s/item; full 1,238-row
   certification cost $0.42. Kimi: 9.2–22.2 s/item.

## Current state (what exists)

- Certification harness + gold: `backend/scripts/typesafe_bakeoff.py`
  (question sets, verdict mapping, gates, gold scoring) +
  `state/typesafe-bakeoff/gold-v1/*.jsonl` (1,238 rows).
- Gates NOT yet met (Jev: 0.826 accuracy, 8 unsafe/1,238, det 0.966) →
  policy stays "Jev primary + LLM exception handler"; Jev-only is a later
  phase gated on re-certification.
- `TYPESAFE_API_KEY` already set on Heroku (`mhm-pipeline-web`).
- Production integration NOT started: Jev is not yet selectable in the
  verify modals/jobs.

## Implementation steps (in order)

### 1. Registry: add the typesafe provider
`eval-agent/config/tier1_models.yaml`:

```yaml
  typesafe/jev-1.13.0:
    label: Jev 1.13 (TypeSafe)
    provider: typesafe
    api_key_env: TYPESAFE_API_KEY
    supports_agentic: false
```

Both loaders are generic (`eval-agent/eval_agent/judge_models.py:56`,
`backend/app/pipeline/judge_models.py:64`) — no loader change needed.
Frontend tier-1 pickers (`GET /api/judge-models`, `Tier1ModelSelect.tsx`)
list it automatically. Backend credential resolution is generic too
(`tier1_api_key_for_spec` non-gemini path reads the env var; key already
on Heroku).

### 2. TypesafeJudge client (eval-agent)
New `eval-agent/eval_agent/client/typesafe_client.py` implementing the
Judge protocol (`client/judge_interface.py`: `judge(prompt, schema,
timeout) -> JudgeResponse`):

- `state` = the prompt with the trailing line
  `\nReturn only the JSON verdict.` replaced by a Jev instruction line
  ("answer the attached questions; each is one rubric axis"). Keep the
  rest byte-identical — this parity is what the certification measured.
- Questions: port the **tuned question sets** from
  `backend/scripts/typesafe_bakeoff.py::questions_for(evaluator_id,
  candidate, max_claims)` into `eval-agent/eval_agent/client/typesafe_questions.py`
  (they encode the rubric nuances that produced the certified numbers —
  do NOT regenerate generic schema-driven questions for the six certified
  evaluators). Generic schema→questions fallback only for
  authority/hmo-schema channels in v1.
- Request: `POST https://api.typesafe.ai/v1/systemone`
  `{state, model: "jev-1.13.0", questions}`; retries/backoff honoring
  `retry-after` (429/5xx); a 4xx fails that row only (never the batch).
- Mapping answers → verdict dict: axes from Choice answers;
  `overall` computed IN CODE from the universal table (n/a→yes; any
  no→fail; else partial if any partial; else full) — never asked of the
  model; `confidence` = min axis confidence; `reasoning` = synthesized
  template (axes + evidence_field Choice + match_kind + probabilities);
  `suggested_fix: null`; `input_tokens` from `usage.input_tokens`,
  output 0.
- Known limits: no free text; per-claim statement checks only for
  `wikidata_item` (≤ `max_claims` = 8).

### 3. Route the provider in the session
`eval-agent/eval_agent/orchestration/session.py::_build_judge_for_model`
(~line 928): add a `provider == "typesafe"` branch returning
`TypesafeJudge(model=spec.id, api_key=env, rate_limiter=rl)`.

### 4. Deterministic code gates (port, do not reinvent)
New `eval-agent/eval_agent/jev_gates.py`, ported verbatim from the harness
(`_deterministic_text_artifacts(payload, marc_context)`, validator
ERROR→fail, artifact downgrade, `ROLE_CONF_GATE = 0.5` confidence gate):
applied in `session.py` after `parse_verdict` **only when the primary
judge is typesafe** (the gates need the candidate payload, which the
Judge protocol does not carry). These gates were worth −10 unsafe
approvals in the ablation; do not put mechanical artifacts back into
model questions.

### 5. Escalation policy (LLM exception handler)
`session.py::_judge_linear` (~line 693): after a usable typesafe verdict,
if `config.escalate_policy` and (`overall != "full"` or `confidence <
config.escalate_below_conf`) → run the existing `_judge_fallback`
(`config.fallback_model`, already wired) and use the fallback's verdict
for that row (row's `judge_id` then honestly records the deciding judge).
Add `SessionConfig` fields `escalate_policy: bool = False`,
`escalate_below_conf: float = 0.85` + `run` CLI flags. Default OFF until
the registry entry is validated.

### 6. Backend pass-through
`backend/app/pipeline/run_job_params.py` + `agent_runner.py` (~line 276
env injection): confirm the typesafe spec flows through tier-model
validation and `TYPESAFE_API_KEY` reaches the subprocess env. No code
expected beyond a test.

### 7. Verdict + cache compatibility
Verdicts must validate against `eval-agent/config/schemas/verdict.v2.json`
(additionalProperties: false — do not add fields; escalation is visible
via `judge_id`). Cache keys already include the judge model → Jev
verdicts cache separately; no invalidation.

### 8. Tests
- eval-agent: typesafe question-set construction per evaluator; verdict
  mapping incl. overall table + gates; retry/4xx-row behavior; escalation
  policy unit test (high-conf full → no fallback; partial/low-conf →
  fallback used, judge_id = fallback).
- backend: registry lists the model (`test_judge_models.py` pattern);
  credentials resolve (`test_run_job_params_tier_model.py` pattern).

### Implementation status (2026-09-21)

Steps 1–8 done. Where each landed, plus the deliberate deltas:

| Step | Landed in | Notes / delta from plan |
|---|---|---|
| 1 | `eval-agent/config/tier1_models.yaml` | Verbatim; both loaders + backend credential resolution needed no change |
| 2 | `eval-agent/eval_agent/client/typesafe_questions.py`, `typesafe_client.py` | Question sets ported verbatim; generic schema→questions fallback for non-certified evaluators (authority / hmo-schema / self-verify paths) |
| 3 | `session.py::_build_judge_for_model` | `provider == "typesafe"` branch → `TypesafeJudge` |
| 4 | `eval-agent/eval_agent/jev_gates.py` + `session.py::_judge_with_retries` | Gates run on the verdict dict **before** `parse_verdict` (plan said after — equivalent: `parse_verdict` is a pure mapping of the final axes/overall). The Judge protocol carries a per-call optional `context` (`evaluator_id` + `payload`) so the client can build tuned questions; raw answers + min-axis confidence travel in a new optional `JudgeResponse.meta` — the cached verdict dict stays schema-clean (no `_jev` transient keys) |
| 5 | `session.py::_judge_linear` + `_should_escalate` / `_should_escalate_cached`, `SessionConfig.escalate_policy` / `escalate_below_conf`, CLI `--escalate-policy` / `--escalate-below-conf` | Escalation is typesafe-only and default OFF; cache-hit rows escalate on overall alone (a cached verdict carries no per-axis confidence), so warm-cache re-runs stay consistent with the first escalated run |
| 6 | `backend/tests/unit/test_typesafe_pass_through.py` | Confirmed: tier-model validation accepts the spec, `ensure_tier1_credentials` reads `TYPESAFE_API_KEY`, and the key reaches the spawned subprocess env (fake `eval-agent` package) |
| 7 | `test_gated_verdict_validates_against_schema` | Inner verdict + full results.jsonl envelope validate against `verdict.v2.json`; escalation visible only via `judge_id`; cache keys already judge-model-scoped |
| 8 | `eval-agent/tests/test_typesafe_client.py`, `test_jev_gates.py`, `test_jev_escalation.py`; backend registry/router/credential tests | 125 eval-agent tests + backend slices green |

**Wikidata-contract extension (2026-09-21, post-steps follow-up):** for
`wikidata_item` the Jev set gains `p31_ok` (instance-of typing per the WPM
data model — person→Q5, manuscript→Q87167/specific subclass, work→written-work
class) and `duplicate_risk` (existing QID / live-probe status per Rule W-139),
and `jev_gates.py` gains three mechanical gates the confidence gate can never
soften: probe `candidates_found` without an adopted existing QID → `type_ok=no`
(link instead of create), no P31 statement → `type_ok=no` (structural claim
missing), and `duplicate_risk=duplicate_found` is a blocking check in the
overall while `unknown` never moves an axis. The duplicate gate reads the
probe answer with the backend's surface precedence (`_wikidata_existence` →
`verify_evidence.wikidata_existing.duplicate_check` → `_duplicate_status`).
These two questions extend the certified set — re-run the wikidata-channel
bake-off (`typesafe_bakeoff.py --channel wikidata --gold …`) before defaulting
that channel to Jev.

Step 9 (rollout sequence: shadow → default-per-channel → monitoring →
Jev-only phase) is untouched and still requires the certification gates.

### 9. Rollout sequence
1. Shadow: run both judges on real verify jobs for 2–4 cycles; log
   agreement per channel (reuse `bakeoff_report` comparators).
2. Enable Jev + escalation as the default tier model per channel.
3. Monitor: weekly 50-row tier-1 spot audit; alert on drift.
4. **Jev-only phase (gated)**: after curator countersign of the 200-row
   gold (`state/typesafe-bakeoff/jev-vs-qubrid/curator_countersign_200.jsonl`)
   + gold reconciliation (truncated-folio class) + wikidata role-precision
   round re-certify per channel; add 2-of-3 voting on borderline axes
   (determinism 0.966 → ≥0.99); then demote the LLM to optional
   spot-checker per channel that passes.

## What NOT to do
- Do not auto-approve anything from AI verdicts (curator approval stays;
  eval-agent R13 parity).
- Do not drop the LLM fallback before the gates pass — the two judges
  catch different error classes (8 unsafe rows were Jev-only misses).
- Do not re-generate the tuned question wording from schemas.
- Never price at $42/Mtok — the console-verified rate is $0.042/Mtok
  ($42/Btok); `PRICE_PER_MTOK = 0.042` in the harness.

## Key file map (port source → destination)
| Harness (source of truth) | Production destination |
|---|---|
| `typesafe_bakeoff.py::questions_for` | `eval-agent/client/typesafe_questions.py` |
| `typesafe_bakeoff.py::jev_request` | `eval-agent/client/typesafe_client.py` |
| `typesafe_bakeoff.py::_map_row` + `_deterministic_text_artifacts` + `ROLE_CONF_GATE` | `eval-agent/jev_gates.py` |
| `typesafe_bakeoff.py::_score_vs_gold` / `_load_gold` | stays (certification tooling) |
