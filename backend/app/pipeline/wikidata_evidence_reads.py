"""Pace fresh Wikidata reads for one evidence batch."""

import asyncio
import logging
from contextlib import contextmanager
from contextvars import ContextVar

import httpx

from converter.wikidata.retry_policy import retry_after_seconds

logger = logging.getLogger(__name__)

_MIN_INTERVAL = 1.0
_MAX_WAIT = 90.0
_ATTEMPTS = 3
_TEMPORARY = {408, 429, 500, 502, 503, 504}


class _ReadWindow:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.next_request = 0.0

    async def get(self, client, url, params, timeout):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _MAX_WAIT
        for attempt in range(_ATTEMPTS):
            async with self.lock:
                now = loop.time()
                delay = self.next_request - now
                if max(now, self.next_request) > deadline:
                    raise ValueError("Wikidata requested a delay beyond the evidence read budget.")
                if delay > 0:
                    await asyncio.sleep(delay)
                self.next_request = loop.time() + _MIN_INTERVAL
                try:
                    async with asyncio.timeout(timeout):
                        response = await client.get(url, params=params, timeout=timeout)
                except (httpx.TransportError, TimeoutError):
                    if attempt + 1 == _ATTEMPTS:
                        raise
                    self.next_request = loop.time() + 2 ** (attempt + 1)
                    continue

                code = _delay_code(response)
                if code is None:
                    return response
                delay = max(_MIN_INTERVAL, retry_after_seconds(
                    response.headers.get("Retry-After"), default=5.0 * 2 ** attempt,
                ))
                self.next_request = loop.time() + delay
                logger.info(
                    "Wikidata evidence read deferred action=%s reason=%s delay=%.1fs",
                    params.get("action"), code, delay,
                )
                if attempt + 1 == _ATTEMPTS:
                    return response


def _delay_code(response: httpx.Response) -> str | None:
    if response.status_code in _TEMPORARY:
        return str(response.status_code)
    if response.is_success:
        if "Retry-After" in response.headers:
            return "retry_after"
        try:
            data = response.json()
        except (ValueError, RecursionError):
            return None
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict) and error.get("code") in ("maxlag", "ratelimited", "readonly"):
            return error["code"]
    return None


_window: ContextVar[_ReadWindow | None] = ContextVar("wikidata_evidence_read_window", default=None)


@contextmanager
def paced_wikidata_evidence_reads():
    token = _window.set(_ReadWindow())
    try:
        yield
    finally:
        _window.reset(token)


async def wikidata_evidence_get(client, url, *, params, timeout=12.0):
    window = _window.get()
    if window is not None:
        return await window.get(client, url, params, timeout)
    async with asyncio.timeout(timeout):
        return await client.get(url, params=params, timeout=timeout)
