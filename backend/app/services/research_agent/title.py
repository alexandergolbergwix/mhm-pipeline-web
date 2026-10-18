"""AI-generated titles for research-agent threads.

One short Qubrid Chat Completions call per thread (glm-5.3-flash). Any
failure degrades to a truncated first-user-message title — a title is
cosmetic and must never break a chat turn.
"""
from __future__ import annotations

import logging
import re

import httpx

from app.settings import get_settings

logger = logging.getLogger(__name__)

_MAX_TITLE_LEN = 80
_MAX_PROMPT_TEXT = 400
# Process-global memo: the same first message never pays for a second
# title call (the reply-cache equivalent for title generation).
_TITLE_CACHE: dict[str, str | None] = {}
_TITLE_CACHE_MAX = 500


def fallback_title(first_user_text: str) -> str:
    text = " ".join((first_user_text or "").split())
    if not text:
        return "Research session"
    title = text[:_MAX_TITLE_LEN].rstrip()
    return title or "Research session"


async def generate_thread_title(first_user_text: str) -> str | None:
    """Return a short AI title, or None when every model is unavailable."""
    from app.services.research_agent.model_fallback import (  # noqa: PLC0415
        resolve_model_configs,
    )

    configs = resolve_model_configs()
    if not configs:
        return None
    text = " ".join((first_user_text or "").split())[:_MAX_PROMPT_TEXT]
    if not text:
        return None
    if text in _TITLE_CACHE:
        return _TITLE_CACHE[text]
    prompt = (
        "Write a 3-6 word title for a research chat that starts with the "
        f'message below. Reply with the title text only, no quotes.\n\n"{text}"'
    )
    content = ""
    for config in configs:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{config.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {config.api_key}"},
                    json={
                        "model": config.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 512,
                    },
                )
                if resp.status_code >= 400:
                    # 402 insufficient_quota: try the fallback model instead
                    # of dying silently (2026-10-17).
                    logger.warning(
                        "Thread title generation failed on %s: %s %s",
                        config.source, resp.status_code, resp.text[:120],
                    )
                    continue
                content = str(resp.json()["choices"][0]["message"]["content"] or "")
                break
        except Exception as exc:  # noqa: BLE001
            logger.warning("Thread title generation failed on %s: %s", config.source, exc)
            continue
    if not content:
        return None
    title = re.sub(r"^[\s\"'`*]+|[\s\"'`*]+$", "", content.splitlines()[0] if content.strip() else "")
    title = title[:_MAX_TITLE_LEN] or None
    if len(_TITLE_CACHE) >= _TITLE_CACHE_MAX:
        _TITLE_CACHE.clear()
    _TITLE_CACHE[text] = title
    return title
