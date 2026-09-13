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
    """Return a short AI title, or None when Qubrid is unavailable."""
    settings = get_settings()
    api_key = settings.qubrid_api_key
    if not api_key:
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
    model = settings.research_agent_title_model
    base_url = settings.qubrid_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 512,
                },
            )
            if resp.status_code >= 400:
                logger.warning("Thread title generation failed: %s %s", resp.status_code, resp.text[:120])
                return None
            content = str(resp.json()["choices"][0]["message"]["content"] or "")
    except Exception as exc:
        logger.warning("Thread title generation failed: %s", exc)
        return None
    title = re.sub(r"^[\s\"'`*]+|[\s\"'`*]+$", "", content.splitlines()[0] if content.strip() else "")
    title = title[:_MAX_TITLE_LEN] or None
    if len(_TITLE_CACHE) >= _TITLE_CACHE_MAX:
        _TITLE_CACHE.clear()
    _TITLE_CACHE[text] = title
    return title
