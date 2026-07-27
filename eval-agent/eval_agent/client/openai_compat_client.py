"""OpenAI-compatible chat-completions judge (Qubrid Kimi / DeepSeek, etc.)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from eval_agent.client.gemini_client import _parse_json_object
from eval_agent.client.judge_interface import JudgeResponse
from eval_agent.client.rate_limiter import RateLimiter
from eval_agent.logging_setup import get_logger, redact, truncate

log = get_logger(__name__)


class OpenAICompatJudge:
    """Judge via ``/chat/completions`` on an OpenAI-compatible API."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        rate_limiter: RateLimiter,
        extra_body: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_output_tokens: int = 4096,
        max_retries: int = 6,
        retry_base_seconds: int = 5,
    ) -> None:
        if not api_key:
            raise ValueError("API key required")
        if not base_url:
            raise ValueError("base_url required")
        self.id = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._rate_limiter = rate_limiter
        self._extra_body = dict(extra_body or {})
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._max_retries = max_retries
        self._retry_base_seconds = retry_base_seconds
        log.debug(
            "openai_compat.init model=%s base_url=%s api_key=%s",
            model, self._base_url, redact(api_key),
        )

    def judge(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        timeout: int = 120,
    ) -> JudgeResponse:
        schema_hint = json.dumps(schema, ensure_ascii=False)
        full_prompt = (
            f"{prompt}\n\n"
            "Return a single JSON object only (no markdown fences) matching this schema:\n"
            f"{schema_hint}"
        )
        payload: dict[str, Any] = {
            "model": self.id,
            "messages": [{"role": "user", "content": full_prompt}],
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        payload.update(self._extra_body)

        try:
            data = self._post(payload, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            log.debug("openai_compat.transport_error %s", truncate(str(exc), 400))
            return JudgeResponse(
                verdict=None, raw_text=None, error=str(exc), judge_id=self.id,
            )

        raw_text, in_tok, out_tok = _extract_chat_completion(data)
        verdict, parse_err = _parse_json_object(raw_text or "")
        return JudgeResponse(
            verdict=verdict,
            raw_text=raw_text,
            error=parse_err,
            judge_id=self.id,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )

    def _post(self, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
        url = f"{self._base_url}/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_err: Exception | None = None
        for attempt in range(self._max_retries):
            self._rate_limiter.acquire()
            req = urllib.request.Request(
                url,
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
                    "openai_compat.http_error code=%d attempt=%d body=%s",
                    exc.code, attempt + 1, truncate(body_text, 300),
                )
                if exc.code == 429 and attempt < self._max_retries - 1:
                    wait = min(
                        self._retry_base_seconds * (2 ** attempt),
                        90,
                    )
                    print(
                        f"[STEP] judge retry after HTTP 429 "
                        f"(wait {wait}s, attempt {attempt + 1})",
                        flush=True,
                    )
                    time.sleep(wait)
                    last_err = RuntimeError(f"HTTP 429 (retried after {wait}s): {body_text[:200]}")
                    continue
                raise RuntimeError(f"HTTP {exc.code}: {body_text[:500]}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < self._max_retries - 1:
                    wait = min(
                        self._retry_base_seconds * (2 ** attempt),
                        90,
                    )
                    print(
                        f"[STEP] judge retry after network error "
                        f"(wait {wait}s, attempt {attempt + 1})",
                        flush=True,
                    )
                    time.sleep(wait)
                    last_err = RuntimeError(f"transient network: {exc}")
                    continue
                raise RuntimeError(f"network error: {exc}") from exc
        raise RuntimeError(f"max retries exhausted: {last_err}")


def _extract_chat_completion(data: dict[str, Any]) -> tuple[str | None, int | None, int | None]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None, None, None
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    raw = message.get("content") if isinstance(message, dict) else None
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    in_tok = usage.get("prompt_tokens")
    out_tok = usage.get("completion_tokens")
    return (
        str(raw) if raw is not None else None,
        int(in_tok) if in_tok is not None else None,
        int(out_tok) if out_tok is not None else None,
    )
