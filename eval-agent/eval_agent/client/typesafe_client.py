"""TypeSafe Jev (System One) judge client.

Implements the Judge protocol against ``POST https://api.typesafe.ai/v1/systemone``.
Jev answers typed Choice / noul questions instead of emitting a JSON verdict
object, so the client:

- rewrites only the trailing "Return only the JSON verdict." line of the
  tier-1 prompt into a Jev instruction — the rest of the state stays
  byte-identical (the parity the certification measured);
- picks the tuned per-evaluator question set (ported from the bake-off
  harness) when the session supplies one via ``context``, else falls back to
  generic schema-driven questions (authority / hmo-schema channels in v1);
- maps answers → verdict axes and computes ``overall`` IN CODE from the
  universal table — never asked of the model. The session's deterministic
  gates (``eval_agent.jev_gates``) finalize axes + overall afterwards.

Known limits: no free text; per-claim statement checks only for
``wikidata_item`` (≤ ``max_claims`` = 8).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any

from eval_agent.client.judge_interface import JudgeResponse
from eval_agent.client.rate_limiter import RateLimiter
from eval_agent.client.typesafe_questions import (
    JEV_CLOSE,
    NAXIS,
    RAXIS,
    TAXIS,
    TUNED_EVALUATORS,
    generic_questions,
    questions_for,
)
from eval_agent.jev_gates import norm_axis, synthesize_reasoning, universal_overall
from eval_agent.logging_setup import get_logger, redact, truncate

log = get_logger(__name__)

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
_JUDGMENT_MARKER = "\nReturn only the JSON verdict."


class TypesafeJudge:
    """Judge via the TypeSafe System One API (model ``jev-1.13.0``)."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        rate_limiter: RateLimiter,
        max_retries: int = 3,
        retry_base_seconds: float = 2.0,
        max_claims: int = 8,
    ) -> None:
        if not api_key:
            raise ValueError("API key required")
        self.id = model
        self._model = model.split("/")[-1]  # registry id → bare API model
        self._api_key = api_key
        self._rate_limiter = rate_limiter
        self._max_retries = max_retries
        self._retry_base_seconds = retry_base_seconds
        self._max_claims = max_claims
        log.debug(
            "typesafe.init model=%s api_model=%s api_key=%s",
            model, self._model, redact(api_key),
        )

    # ── Judge protocol ────────────────────────────────────────────────

    def judge(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        timeout: int = 120,
        context: dict[str, Any] | None = None,
    ) -> JudgeResponse:
        questions = self._questions_for(schema, context)
        # Byte-parity with the certified bake-off state: only the trailing
        # "Return only the JSON verdict." line changes.
        state = prompt.replace(_JUDGMENT_MARKER, f"\n{JEV_CLOSE}")
        payload = {"state": state, "model": self._model, "questions": questions}
        try:
            body = self._post(payload, timeout=timeout)
        except RuntimeError as exc:
            log.debug("typesafe.transport_error %s", truncate(str(exc), 400))
            return JudgeResponse(
                verdict=None, raw_text=None, error=str(exc), judge_id=self.id,
            )
        answers = body.get("answers") or {}
        for axis in (NAXIS, TAXIS):
            ans = answers.get(axis)
            if not isinstance(ans, dict) or not str(ans.get("choice") or "").strip():
                return JudgeResponse(
                    verdict=None, raw_text=None,
                    error=f"TypeSafe response missing '{axis}' answer",
                    judge_id=self.id,
                )
        return self._verdict_from_answers(answers, body.get("usage"))

    # ── Internals ─────────────────────────────────────────────────────

    def _questions_for(
        self,
        schema: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> dict[str, dict[str, Any]]:
        evaluator_id = str((context or {}).get("evaluator_id") or "")
        if evaluator_id in TUNED_EVALUATORS:
            payload = (context or {}).get("payload")
            candidate = (
                SimpleNamespace(payload=payload) if payload is not None else None
            )
            return questions_for(evaluator_id, candidate, self._max_claims)
        # Generic schema→questions fallback (authority / hmo-schema channels).
        return generic_questions(schema)

    def _min_axis_confidence(self, answers: dict[str, Any]) -> float | None:
        confs = []
        for q in (NAXIS, TAXIS, RAXIS):
            ans = answers.get(q)
            if isinstance(ans, dict) and isinstance(ans.get("confidence"), (int, float)):
                confs.append(float(ans["confidence"]))
        return min(confs) if confs else None

    def _verdict_from_answers(
        self,
        answers: dict[str, Any],
        usage: Any,
    ) -> JudgeResponse:
        axes = {
            NAXIS: norm_axis((answers.get(NAXIS) or {}).get("choice")),
            TAXIS: norm_axis((answers.get(TAXIS) or {}).get("choice")),
            RAXIS: (
                norm_axis((answers.get(RAXIS) or {}).get("choice"))
                if RAXIS in answers else "n/a"
            ),
        }
        evidence = (answers.get("evidence_field") or {}).get("choice") or "unknown"
        match_kind = (answers.get("match_kind") or {}).get("choice") or ""
        verdict = {
            "name_ok": axes[NAXIS],
            "type_ok": axes[TAXIS],
            "role_ok": axes[RAXIS],
            "overall": universal_overall(axes),
            "reasoning": synthesize_reasoning(axes, evidence, match_kind, answers),
            # No free text — Jev proposes no span fix.
            "suggested_fix": None,
        }
        confidence = self._min_axis_confidence(answers)
        in_tok: int | None = None
        if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), (int, float)):
            in_tok = int(usage["input_tokens"])
        return JudgeResponse(
            verdict=verdict,
            raw_text=None,
            error=None,
            judge_id=self.id,
            input_tokens=in_tok,
            output_tokens=0,
            # Raw answers travel out-of-band: the session's jev_gates need
            # per-question confidences and the verdict must stay schema-clean.
            meta={"answers": answers, "confidence": confidence},
        )

    def _post(self, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        delay = self._retry_base_seconds
        last_err = ""
        for attempt in range(self._max_retries + 1):
            self._rate_limiter.acquire()
            req = urllib.request.Request(
                TYPESAFE_URL,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
                method="POST",
            )
            try:
                from eval_agent.client.step_heartbeat import StepHeartbeat  # noqa: PLC0415

                with StepHeartbeat(f"waiting on {self.id} HTTP (attempt {attempt + 1})"):
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body_text = exc.read().decode("utf-8", errors="ignore")
                log.debug(
                    "typesafe.http_error code=%d attempt=%d body=%s",
                    exc.code, attempt + 1, truncate(body_text, 300),
                )
                if exc.code == 429 or exc.code >= 500:
                    last_err = f"HTTP {exc.code}: {body_text[:200]}"
                    retry_after = exc.headers.get("retry-after") if exc.headers else None
                    try:
                        wait = float(retry_after) if retry_after else delay
                    except ValueError:
                        wait = delay
                    wait = min(wait, 60.0)
                    print(
                        f"[STEP] judge retry after HTTP {exc.code} "
                        f"(wait {wait:.0f}s, attempt {attempt + 1})",
                        flush=True,
                    )
                    time.sleep(wait)
                    delay *= 2
                    continue
                # A 4xx is a per-row problem (e.g. oversized request) — fail
                # that row, never the batch.
                raise RuntimeError(f"HTTP {exc.code}: {body_text[:300]}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                if attempt < self._max_retries:
                    wait = min(delay, 60.0)
                    print(
                        f"[STEP] judge retry after network error "
                        f"(wait {wait:.0f}s, attempt {attempt + 1})",
                        flush=True,
                    )
                    time.sleep(wait)
                    delay *= 2
        raise RuntimeError(
            f"TypeSafe request failed after {self._max_retries + 1} attempts: "
            f"{last_err}"
        )
