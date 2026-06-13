"""Modal-backed transliteration for the work-label waterfall (Tier 4).

Calls the ``MhmNer`` Modal app's ``/transliterate`` endpoint.
Used when ``MODAL_NER_URL`` is set (same URL as NER; the app serves both).

Returns the Latin transliteration of a Hebrew text, or None on any failure.
Caches results via the inference cache (kind="translit.hebrew_to_latin").
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_KIND = "translit.hebrew_to_latin"


async def modal_transliterate(
    text: str,
    *,
    db_session: Any | None = None,
    user_id: Any | None = None,
    skip_cache: bool = False,
    timeout_s: float = 30.0,
) -> str | None:
    """Transliterate Hebrew text to Latin via the Modal endpoint.

    Returns the Latin string, or None on any failure (including URL unset,
    network error, or empty response). Never raises.
    """
    url = (os.environ.get("MODAL_NER_URL") or "").rstrip("/")
    if not url:
        return None

    async def _fetch() -> str | None:
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(
                    f"{url}/transliterate",
                    json={"text": text},
                )
                if resp.status_code != 200:
                    logger.debug("Modal /transliterate HTTP %s", resp.status_code)
                    return None
                body = resp.json()
                latin = body.get("latin")
                return str(latin) if latin else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("Modal /transliterate error: %s", exc)
            return None

    if db_session is None:
        return await _fetch()

    from app.pipeline.inference_cache import cache_lookup_or_call  # noqa: PLC0415
    return await cache_lookup_or_call(
        db_session,
        kind=_KIND,
        query_summary={"backend": "modal", "text": text},
        fetch=_fetch,
        user_id=user_id,
        skip_cache=skip_cache,
    )
