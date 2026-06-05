"""Authority enrichment backend — Protocol + local/Modal implementations.

Mirrors ``extraction_backend.py``'s pattern. The ``DesktopMatcher`` in
``authority.py`` uses this to route Mazal/KIMA calls either to local
SQLite (dev, fallback) or to the deployed Modal app (production, when
``AUTHORITY_MODE=modal``).

The inference cache sits ABOVE this layer (in authority.py's
``cache_lookup_or_call`` wrappers) so cache hits never reach here —
only real misses call through.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)


class AuthorityBackend(Protocol):
    """Minimal interface for Mazal + KIMA lookups."""

    async def match_person(self, name: str) -> dict[str, Any] | None:
        """Return a dict with mazal_id + details, or None on no hit."""
        ...

    async def match_place(self, text: str) -> dict[str, Any] | None:
        """Return a dict with wikidata_uri + enrichment row, or None on no hit."""
        ...


class LocalAuthorityBackend:
    """Calls the local SQLite matchers directly (existing behaviour).

    Used when ``AUTHORITY_MODE != "modal"``. The ``MazalMatcher`` and
    ``KimaMatcher`` instances are owned by ``DesktopMatcher`` and passed
    in at construction.
    """

    def __init__(
        self,
        mazal_matcher: Any | None,
        kima_matcher: Any | None,
    ) -> None:
        self._mazal = mazal_matcher
        self._kima = kima_matcher

    async def match_person(self, name: str) -> dict[str, Any] | None:
        import asyncio  # noqa: PLC0415

        if self._mazal is None:
            return None
        mid = await asyncio.to_thread(self._mazal.match_person, name)
        if mid is None:
            return None
        details: dict = (
            await asyncio.to_thread(self._mazal.get_person_details, str(mid)) or {}
        )
        return {"mazal_id": str(mid), **details}

    async def match_place(self, text: str) -> dict[str, Any] | None:
        import asyncio  # noqa: PLC0415

        if self._kima is None:
            return None
        uri = await asyncio.to_thread(self._kima.match_place, text)
        if uri is None:
            return None
        row: dict = {}
        try:
            idx = self._kima.index
            if idx is not None and hasattr(idx, "lookup_place"):
                row = idx.lookup_place(text) or {}
        except AttributeError:
            pass
        return {"wikidata_uri": str(uri), **row}


class ModalAuthorityBackend:
    """Calls the deployed ``mhm-authority`` Modal app over HTTPS.

    Used when ``AUTHORITY_MODE=modal`` and ``MODAL_AUTHORITY_URL`` is set.
    Each HTTP call is wrapped by authority.py's ``cache_lookup_or_call``
    before reaching here — cache hits never call through to Modal.
    """

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)

    async def match_person(self, name: str) -> dict[str, Any] | None:
        try:
            r = await self._client.post(
                f"{self._base}/match_person",
                json={"name": name},
            )
            r.raise_for_status()
            data: dict = r.json()
            if not data.get("matched"):
                return None
            return data
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ModalAuthorityBackend.match_person failed for %r: %s", name, exc,
            )
            return None

    async def match_place(self, text: str) -> dict[str, Any] | None:
        try:
            r = await self._client.post(
                f"{self._base}/match_place",
                json={"text": text},
            )
            r.raise_for_status()
            data: dict = r.json()
            if not data.get("matched"):
                return None
            return data
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ModalAuthorityBackend.match_place failed for %r: %s", text, exc,
            )
            return None


def build_authority_backend(
    mazal_matcher: Any | None = None,
    kima_matcher: Any | None = None,
) -> AuthorityBackend:
    """Return the right backend based on ``AUTHORITY_MODE`` env var."""
    mode = os.getenv("AUTHORITY_MODE", "local").lower()
    if mode == "modal":
        url = os.getenv("MODAL_AUTHORITY_URL", "")
        if not url:
            logger.warning(
                "AUTHORITY_MODE=modal but MODAL_AUTHORITY_URL not set — "
                "falling back to local SQLite"
            )
        else:
            logger.info("Authority backend: Modal (%s)", url)
            return ModalAuthorityBackend(base_url=url)
    logger.info("Authority backend: local SQLite")
    return LocalAuthorityBackend(mazal_matcher=mazal_matcher, kima_matcher=kima_matcher)
