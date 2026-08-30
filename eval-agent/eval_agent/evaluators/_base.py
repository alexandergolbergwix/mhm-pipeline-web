"""Pluggable Evaluator interface.

An Evaluator declares:

  - ``id``                — canonical name (matches feature_list.json)
  - ``sub_types``          — categories broken out in metrics
  - ``marc_field_keys``    — semantic MARC slice this evaluator needs
  - ``confidence_field``   — which entity key drives the threshold filter
  - ``rubric_name``        — name of the per-evaluator rubric markdown

And implements:

  - ``extract_candidates(record, marc, threshold)`` → list[Candidate]
  - ``build_prompt(candidate)``                     → str
  - ``parse_verdict(raw, candidate)``               → Verdict

Adding a new evaluator (e.g. for Stage 3 authority resolution) is one
new module under ``eval_agent/evaluators/`` + a rubric file + a
registry entry. No harness changes needed.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
RUBRICS_DIR = REPO_ROOT / "config" / "rubrics"


@dataclass(frozen=True)
class Candidate:
    """One model prediction queued for judging.

    Carries the pipeline's deterministic MARC-grounding signal so the
    Gemini judge sees what fields the entity's text actually appears
    in. The judge no longer has to re-derive that itself — its job is
    just to confirm the verdict given the structured evidence.

    The grounding fields are populated by the pipeline's
    ``filter_with_marc_grounding`` post-filter and read out of
    ``ner_results.json`` at extract-candidate time. When the entity
    pre-dates F8 grounding (older runs) the fields default to safe
    "unknown" values and the prompt falls back to a generic instruction.
    """

    record_id: str
    evaluator_id: str
    sub_type: str
    payload: dict[str, Any]
    confidence: float
    marc_context: dict[str, str] = field(default_factory=dict)
    # F8 MARC-grounding signal — every entity in ner_results.json now
    # carries these. ``grounded`` is the strict role-mapped check;
    # ``exists_in`` lists every MARC field where the text appears;
    # ``grounded_field`` is the specific field that satisfied the
    # strict gate (or None).
    grounded: bool | None = None
    grounded_field: str | None = None
    exists_in: list[dict[str, Any]] = field(default_factory=list)
    # MARC fields the entity's role/type implies — duplicated from
    # the pipeline's role/type map so the prompt can frame the
    # judge's question as "is the match in <these> fields?"
    role_fields: list[str] = field(default_factory=list)


@dataclass
class SuggestedFix:
    """Optional high-confidence corrected entity text proposed by the judge.

    Only populated when:
    * Gemini is high confidence.
    * The corrected value is directly visible in the provided MARC context.
    * The correction is unambiguous (not a role/type fix).
    * The suggested text differs from the original after strip.
    """

    text: str
    reasoning: str | None = None
    source_field: str | None = None
    confidence: str = "high"  # always "high" — other values are dropped

    def to_dict(self) -> dict[str, Any]:
        return {
            "text":         self.text,
            "reasoning":    self.reasoning,
            "source_field": self.source_field,
            "confidence":   self.confidence,
        }


@dataclass
class Verdict:
    """Structured verdict matching ``config/schemas/verdict.v2.json``."""

    record_id: str
    evaluator_id: str
    sub_type: str
    candidate_payload: dict[str, Any]
    confidence: float
    name_ok: str = "no"
    type_ok: str = "no"
    role_ok: str = "n/a"
    overall: str = "fail"
    reasoning: str = ""
    error: str | None = None
    judge_id: str = ""
    cache_key: str = ""
    judged_at: str = ""
    verification_status: str = "judged"
    # True when produced by the agentic tool-loop (vs the linear single-shot).
    # Lives outside the schema-constrained ``verdict`` sub-object so the
    # verdict schema is unaffected; self-verify uses it to keep its gate on
    # the deterministic (linear) verdicts only.
    agentic: bool = False
    # v2 extension: AI-suggested corrected entity text (text-only, high-confidence).
    # None when no fix is proposed or when the evaluator does not support fixes.
    suggested_fix: SuggestedFix | None = None

    def to_jsonl_record(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "judge_id": self.judge_id,
            "record_id": self.record_id,
            "evaluator_id": self.evaluator_id,
            "sub_type": self.sub_type,
            "candidate": self.candidate_payload,
            "confidence": self.confidence,
            "verdict": {
                "name_ok":       self.name_ok,
                "type_ok":       self.type_ok,
                "role_ok":       self.role_ok,
                "overall":       self.overall,
                "reasoning":     self.reasoning,
                "suggested_fix": self.suggested_fix.to_dict() if self.suggested_fix else None,
            },
            "agentic": self.agentic,
            "cache_key": self.cache_key,
            "judged_at": self.judged_at or datetime.now(timezone.utc).isoformat(),
            "verification_status": self.verification_status,
            "error": self.error,
        }


class Evaluator(ABC):
    """Subclass for each (stage, model) being evaluated."""

    id: str = ""
    sub_types: list[str] = []
    marc_field_keys: list[str] = []
    rubric_name: str = ""

    # ── Abstract surface ──────────────────────────────────────────────

    @abstractmethod
    def extract_candidates(
        self,
        *,
        ner_record: dict[str, Any],
        marc_record: dict[str, Any],
        threshold: float,
    ) -> Iterable[Candidate]:
        ...

    @abstractmethod
    def build_prompt(self, candidate: Candidate) -> str:
        ...

    # Evaluators that support suggested_fix. Genre classifier is excluded
    # (genre is a categorical label, not extracted text — no span fixes).
    _FIX_CAPABLE_EVALUATORS: frozenset[str] = frozenset(
        {"person_ner", "provenance_ner", "contents_ner"}
    )

    # A provider failure uses `abstain` as the only public no-decision value.
    # `verification_failed` remains an input-only compatibility value for old
    # snapshots; new sessions never emit it (Rule W-211).
    JUDGE_FAILURE_OVERALL = "verification_failed"
    _PUBLIC_OVERALLS = frozenset({"full", "pass", "partial", "fail", "abstain"})

    def _as_judge_failure(self, v: Verdict, reason: str) -> Verdict:
        v.error = reason
        v.overall = "abstain"
        v.verification_status = "provider_error"
        v.name_ok = "unknown"
        v.type_ok = "unknown"
        v.role_ok = "n/a"
        v.reasoning = (
            f"Judge failure: {reason}. This is NOT an assessment of the item — "
            "the check did not complete and must be re-run."
        )
        return v

    def parse_verdict(self, raw: dict[str, Any] | None, candidate: Candidate) -> Verdict:
        """Map a Gemini response (or None) into a structured Verdict."""
        v = Verdict(
            record_id=candidate.record_id,
            evaluator_id=candidate.evaluator_id,
            sub_type=candidate.sub_type,
            candidate_payload=dict(candidate.payload),
            confidence=candidate.confidence,
        )
        if raw is None:
            return self._as_judge_failure(v, "no verdict (judge failure)")
        v.name_ok = str(raw.get("name_ok", "no"))
        v.type_ok = str(raw.get("type_ok", "no"))
        v.role_ok = str(raw.get("role_ok", "n/a"))
        raw_overall = str(raw.get("overall") or "").strip().lower()
        if raw_overall not in self._PUBLIC_OVERALLS:
            return self._as_judge_failure(
                v,
                "judge returned no valid overall verdict"
                if not raw_overall
                else f"judge returned invalid overall {raw_overall!r}",
            )
        v.overall = raw_overall
        v.reasoning = str(raw.get("reasoning", ""))
        if v.overall != "abstain" and not v.reasoning.strip():
            # The agentic tool-loop cannot send a responseSchema, so a reply can
            # carry the axes and silently omit `reasoning`. An unexplained verdict
            # is not actionable by a curator, so it reads as a failure to judge.
            return self._as_judge_failure(v, "verdict missing reasoning")
        v.suggested_fix = self._parse_suggested_fix(raw.get("suggested_fix"), candidate)
        return v

    def _parse_suggested_fix(
        self,
        raw_fix: Any,
        candidate: Candidate,
    ) -> SuggestedFix | None:
        """Defensively parse and validate a suggested_fix from Gemini.

        Drop the fix if any of:
        * Evaluator is not in the fix-capable set (e.g. genre_classifier).
        * raw_fix is not a dict.
        * raw_fix.confidence != "high".
        * raw_fix.text is empty or equals the original text (after strip).
        * raw_fix.text is unreasonably long (> 512 chars).
        * Candidate has no MARC context AND no grounding signal —
          a verifier without context should not propose fixes
          (guardrail for provenance/contents with missing context).
        """
        if candidate.evaluator_id not in self._FIX_CAPABLE_EVALUATORS:
            return None
        if not isinstance(raw_fix, dict):
            return None
        if raw_fix.get("confidence") != "high":
            return None

        # Guardrail: drop fix when neither MARC context nor grounding is present.
        # This prevents provenance/contents verifiers running without proper
        # context from proposing fixes they cannot substantiate.
        has_marc_context = bool(candidate.marc_context)
        has_grounding = (candidate.grounded is not None) or bool(candidate.exists_in)
        if not has_marc_context and not has_grounding:
            return None

        text = str(raw_fix.get("text") or "").strip()
        if not text:
            return None
        if len(text) > 512:
            return None

        # Drop if the suggested text equals the original (no-op fix).
        # Person NER carries the surface form under "person"; other
        # evaluators under "text" — compare against whichever is present.
        original = str(
            candidate.payload.get("text")
            or candidate.payload.get("person")
            or ""
        ).strip()
        if text == original:
            return None

        return SuggestedFix(
            text=text,
            reasoning=str(raw_fix.get("reasoning") or "") or None,
            source_field=str(raw_fix.get("source_field") or "") or None,
            confidence="high",
        )

    # ── Shared helpers ────────────────────────────────────────────────

    def rubric_text(self) -> str:
        path = RUBRICS_DIR / self.rubric_name
        return path.read_text(encoding="utf-8")

    def format_marc(self, marc: dict[str, str]) -> str:
        if not marc:
            return "  (no relevant MARC fields present)"
        return "\n".join(f"  {k}: {v}" for k, v in sorted(marc.items()))

    def format_grounding(self, candidate: Candidate) -> str:
        """Render the F8 grounding signal as a prompt-readable block.

        Three cases, each unambiguous to the judge:

        * ``grounded=True``  — pipeline confirmed a role-mapped match.
        * ``grounded=False`` + ``exists_in`` non-empty — name is in
          MARC but in the wrong field; the role is probably wrong.
        * ``grounded=False`` + ``exists_in`` empty — discovery: the
          model found something the structured fields don't index.

        When the candidate predates F8 (``grounded is None``), the
        block politely says "no grounding signal available" and the
        judge falls back to its standalone reasoning.
        """
        if candidate.grounded is None and not candidate.exists_in:
            return "  (no MARC-grounding signal available for this candidate)"

        lines: list[str] = []
        role_fields_str = ", ".join(candidate.role_fields) if candidate.role_fields else "(unknown)"
        if candidate.grounded is True:
            lines.append(
                "  STATE = ROLE-GROUNDED — the pipeline's deterministic post-filter"
            )
            lines.append(
                f"  confirms the predicted text appears in the role-mapped MARC field"
            )
            lines.append(
                f"  for this prediction. Role-mapped fields: {role_fields_str}."
            )
            lines.append(
                f"  Matched in: {candidate.grounded_field or '(unspecified)'}."
            )
            lines.append(
                "  This is the STRONGEST positive signal — unless the predicted text"
            )
            lines.append(
                "  itself is wrong (typo, mis-segmentation), agree with the model."
            )
        elif candidate.grounded is False and candidate.exists_in:
            lines.append(
                "  STATE = WRONG-FIELD — the predicted text appears in MARC, but"
            )
            lines.append(
                f"  NOT in the role-mapped field(s) ({role_fields_str})."
            )
            lines.append(
                "  This is a STRONG negative signal: the entity is most likely a"
            )
            lines.append(
                "  wrong-role prediction. The correct role is implied by the field"
            )
            lines.append(
                "  where the name DID appear. Set role_ok = 'no' unless an"
            )
            lines.append(
                "  alternative reading is clearly supported by the cited substring."
            )
        else:
            lines.append(
                "  STATE = DISCOVERY — the predicted text was NOT found in any"
            )
            lines.append(
                "  structured MARC field. Either the NER model is enriching the"
            )
            lines.append(
                "  catalog with information that was never indexed, OR the model"
            )
            lines.append(
                "  hallucinated. Be conservative: set name_ok = 'no' unless the"
            )
            lines.append(
                "  predicted text is clearly visible in a free-text note (and you"
            )
            lines.append(
                "  can quote it from the MARC context block above)."
            )

        if candidate.exists_in:
            lines.append("")
            lines.append("  Pipeline-detected matches (every MARC field where the text appears):")
            for row in candidate.exists_in[:8]:
                mt = row.get("match_type", "?")
                field = row.get("field", "?")
                value = str(row.get("value") or "")
                if len(value) > 80:
                    value = value[:77] + "..."
                marker = "●" if mt == "full" else "○"
                lines.append(f"    {marker} {field}  ({mt})  → \"{value}\"")
            if len(candidate.exists_in) > 8:
                lines.append(f"    … +{len(candidate.exists_in) - 8} more")
        return "\n".join(lines)

    def skill_context(self, candidate: Candidate) -> str:
        """Optional domain skill block (WikiProject Manuscripts, …).

        Override in Studio evaluators. Empty by default so NER/authority
        prompts stay unchanged.
        """
        return ""

    def render_prompt(
        self,
        candidate: Candidate,
        *,
        prediction_block: str,
    ) -> str:
        """Compose rubric + per-candidate prediction + MARC context +
        the deterministic grounding signal the pipeline pre-computed.

        The grounding block ANCHORS the judge: instead of re-running
        the "is this name in MARC?" search from scratch (which Gemini
        does inconsistently), the judge gets the answer up-front and
        only has to reason about *whether the deterministic finding
        agrees with the prediction*. Reduces judge variance dramatically.
        """
        skill = (self.skill_context(candidate) or "").strip()
        skill_block = f"\n\n{skill}\n" if skill else ""
        return (
            self.rubric_text()
            + "\n\n────────────────────────────────────────\n"
            + f"Record ID: {candidate.record_id}\n\n"
            + prediction_block
            + skill_block
            + f"\nRelevant MARC fields for this record:\n"
            + f"{self.format_marc(candidate.marc_context)}\n"
            + "\nDeterministic MARC-grounding signal (from the pipeline's "
              "F8 post-filter — trust this over your own search):\n"
            + f"{self.format_grounding(candidate)}\n"
            + "\nReturn only the JSON verdict."
        )
