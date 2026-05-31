"""Modal-hosted implementation of :class:`InferenceBackend`.

Calls the ``MhmNer`` Modal app's ``/extract`` endpoint with one POST
per record. The endpoint URL comes from ``MODAL_NER_URL`` env var
(set after ``modal deploy modal_app.py``).

Why a backend that takes one record's worth of context and returns
all four model outputs in one call:

* The per-record HTTP round-trip dominates latency when each model
  is a separate endpoint. One call per record × four roles = 4× the
  cold-start tax. The Modal app loads all four models in one
  container; we get them all back in one response.
* The shared DictaBERT encoder is loaded ONCE per container; the
  three derived NER models reuse it. Lower memory + faster warm-up.

Caching: routes through the same ``inference_cache`` table as the
HF backend, keyed per (kind, query_hash). First user to ask for a
given record's entities populates it; everyone else warm-hits.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from app.pipeline.extraction_backend import (
    Entity, GenrePred, InferenceBackend, ModelAvailability,
)

logger = logging.getLogger(__name__)


# Per-call cache TTLs match the HF backend: NER + genre are content-
# addressed, so they never expire (None). See inference_cache.KIND_TTL.
_KIND_PERSON     = "ner.person"
_KIND_PROVENANCE = "ner.provenance"
_KIND_CONTENTS   = "ner.contents"
_KIND_GENRE      = "genre.classify"


class ModalInferenceBackend(InferenceBackend):
    """Calls a deployed Modal endpoint for every record's NER + genre."""

    name = "modal"

    def __init__(
        self, *,
        db_session: Any | None = None,
        user_id:    Any | None = None,
        skip_cache: bool = False,
        url: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self._url = (url or os.environ.get("MODAL_NER_URL") or "").rstrip("/")
        self._timeout_s = timeout_s
        self._db_session = db_session
        self._user_id = user_id
        self._skip_cache = skip_cache
        self._availability: ModelAvailability | None = None
        # One AsyncClient per backend instance — connection pooling
        # across the record loop keeps the per-record overhead at
        # the ~50ms ping-time floor instead of building a new TCP +
        # TLS session every record.
        self._client: httpx.AsyncClient | None = None
        # Cache the last record's full response so the four `*_ner`
        # methods don't re-call the endpoint when the caller iterates
        # role-by-role on the same text. Keyed by the text itself.
        self._last_text: str | None = None
        self._last_response: dict[str, Any] = {}

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client

    async def warm_up(self) -> ModelAvailability:
        if self._availability is not None:
            return self._availability
        if not self._url:
            self._availability = ModelAvailability(
                notes={
                    "person":     "MODAL_NER_URL unset; set it to the URL "
                                  "printed by `modal deploy modal_app.py`",
                    "provenance": "MODAL_NER_URL unset",
                    "contents":   "MODAL_NER_URL unset",
                    "genre":      "MODAL_NER_URL unset",
                },
            )
            return self._availability
        # The Modal app exposes a /health GET endpoint; if anything
        # is wired wrong the curator sees it on the panel.
        try:
            client = await self._ensure_client()
            resp = await client.get(f"{self._url}/health")
            if resp.status_code != 200:
                raise RuntimeError(f"health returned HTTP {resp.status_code}")
            body = resp.json()
            self._availability = ModelAvailability(
                person     = bool(body.get("person")),
                provenance = bool(body.get("provenance")),
                contents   = bool(body.get("contents")),
                genre      = bool(body.get("genre")),
                notes={
                    "person":     "modal /extract" if body.get("person") else "modal /extract unreachable",
                    "provenance": "modal /extract" if body.get("provenance") else "modal /extract unreachable",
                    "contents":   "modal /extract" if body.get("contents") else "modal /extract unreachable",
                    "genre":      "modal /extract" if body.get("genre") else "modal /extract unreachable",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Modal health check failed: %s", exc)
            self._availability = ModelAvailability(
                notes={
                    "person":     f"modal health-check failed: {exc}",
                    "provenance": "modal health-check failed",
                    "contents":   "modal health-check failed",
                    "genre":      "modal health-check failed",
                },
            )
        return self._availability

    # ── Caching helper ────────────────────────────────────────────────

    async def _cached(
        self, *, kind: str, query_summary: dict[str, Any],
        fetch: Any,
    ) -> Any:
        if self._db_session is None:
            return await fetch()
        from app.pipeline.inference_cache import cache_lookup_or_call  # noqa: PLC0415
        return await cache_lookup_or_call(
            self._db_session, kind=kind, query_summary=query_summary,
            fetch=fetch, user_id=self._user_id, skip_cache=self._skip_cache,
        )

    # ── HTTP call ─────────────────────────────────────────────────────

    async def _extract(
        self, *, text: str, title: str, notes: list[str], models: list[str],
    ) -> dict[str, Any]:
        """One round trip to the Modal app for the requested model subset.

        Lazily memoises the most recent (text, title, notes) tuple so
        the per-role public methods can each call this without paying
        for four trips when the extractor processes one record.
        """
        if not self._url:
            return {}
        if self._last_text == text and all(
            m in self._last_response for m in models
        ):
            return self._last_response
        client = await self._ensure_client()
        try:
            resp = await client.post(
                f"{self._url}/extract",
                json={"text": text, "title": title, "notes": notes,
                      "models": models},
            )
            if resp.status_code != 200:
                logger.warning("Modal /extract HTTP %s: %s",
                               resp.status_code, resp.text[:200])
                return {}
            body = resp.json()
            self._last_text = text
            self._last_response = body if isinstance(body, dict) else {}
            return self._last_response
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            logger.warning("Modal /extract network error: %s", exc)
            return {}

    # ── Public methods (match the InferenceBackend Protocol) ──────────

    async def person_ner(self, text: str) -> list[Entity]:
        async def _fetch() -> list[Entity]:
            body = await self._extract(
                text=text, title="", notes=[], models=["person"],
            )
            return list(body.get("person") or [])
        return await self._cached(
            kind=_KIND_PERSON,
            query_summary={"backend": "modal", "text": text},
            fetch=_fetch,
        )

    async def provenance_ner(self, text: str) -> list[Entity]:
        async def _fetch() -> list[Entity]:
            body = await self._extract(
                text=text, title="", notes=[], models=["provenance"],
            )
            return list(body.get("provenance") or [])
        return await self._cached(
            kind=_KIND_PROVENANCE,
            query_summary={"backend": "modal", "text": text},
            fetch=_fetch,
        )

    async def contents_ner(self, text: str) -> list[Entity]:
        async def _fetch() -> list[Entity]:
            body = await self._extract(
                text=text, title="", notes=[], models=["contents"],
            )
            return list(body.get("contents") or [])
        return await self._cached(
            kind=_KIND_CONTENTS,
            query_summary={"backend": "modal", "text": text},
            fetch=_fetch,
        )

    async def genre_classify(
        self, title: str, notes: list[str],
    ) -> list[GenrePred]:
        async def _fetch() -> list[GenrePred]:
            body = await self._extract(
                text="", title=title, notes=notes, models=["genre"],
            )
            raw = body.get("genre") or []
            # Modal returns [[label, conf], ...]; normalise to the
            # protocol shape (list of dicts).
            return [
                {"label": str(item[0]), "confidence": float(item[1])}
                for item in raw if isinstance(item, (list, tuple)) and len(item) >= 2
            ]
        return await self._cached(
            kind=_KIND_GENRE,
            query_summary={"backend": "modal", "title": title, "notes": notes},
            fetch=_fetch,
        )

    async def aclose(self) -> None:
        """Release the connection pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
