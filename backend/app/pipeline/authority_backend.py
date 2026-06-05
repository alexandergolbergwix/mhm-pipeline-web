"""Authority enrichment backend — Protocol + local/Modal/Postgres implementations.

Mirrors ``extraction_backend.py``'s pattern. The ``DesktopMatcher`` in
``authority.py`` uses this to route Mazal/KIMA calls to one of three backends:

* ``local``    — local SQLite matchers (dev / fallback).
* ``modal``    — deployed ``mhm-authority`` Modal app over HTTPS (legacy).
* ``postgres`` — Heroku Postgres tables ``mazal_authorities``,
                 ``mazal_name_index``, ``kima_places``, ``kima_name_index``
                 imported via ``scripts/import_{mazal,kima}_to_postgres.py``.
                 **This is the production default** once the import has run.

Set ``AUTHORITY_MODE=postgres`` on Heroku; ``DATABASE_URL`` is already
present.  The inference cache sits ABOVE this layer so cache hits never
reach here — only real misses call through.
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


class PostgresAuthorityBackend:
    """Queries Mazal / KIMA data from Heroku Postgres.

    Tables ``mazal_authorities``, ``mazal_name_index``, ``kima_places`` and
    ``kima_name_index`` are populated once via the import scripts.  Every
    lookup is a single indexed query — no SQLite file on disk, no Modal
    cold-start, no extra dyno costs.

    Normalization mirrors the original SQLite matchers exactly so the
    ``normalized_name`` column values are compatible.
    """

    def __init__(self, dsn: str) -> None:
        import re  # noqa: PLC0415
        import unicodedata  # noqa: PLC0415

        self._dsn = dsn
        self._conn: Any = None
        self._re = re
        self._uni = unicodedata

    def _get_conn(self) -> Any:
        import psycopg2  # noqa: PLC0415
        import psycopg2.extras  # noqa: PLC0415

        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self._dsn)
            self._conn.autocommit = True
        else:
            try:
                self._conn.cursor().execute("SELECT 1")
            except Exception:  # noqa: BLE001
                self._conn = psycopg2.connect(self._dsn)
                self._conn.autocommit = True
        return self._conn

    # ── Normalisation (mirrors MazalIndex.normalize_name) ─────────────

    def _normalize_mazal(self, text: str) -> str:
        """Strip niqqud, NFD, remove punctuation (keep hyphens), lowercase."""
        if not text:
            return ""
        text = text.strip()
        text = "".join(
            c for c in text
            if not (0x0591 <= ord(c) <= 0x05C7 and ord(c) not in range(0x05D0, 0x05EB))
        )
        text = self._uni.normalize("NFD", text)
        text = "".join(c for c in text if self._uni.category(c) != "Mn")
        text = self._re.sub(r"[^\w\s\u0590-\u05FF-]", "", text)
        return self._re.sub(r"\s+", " ", text).strip().lower()

    def _normalize_kima(self, text: str) -> str:
        """Strip trailing parens, remove niqqud, NFD, remove punct, lowercase."""
        if not text:
            return ""
        text = self._re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
        text = self._re.sub(r"[\u0591-\u05C7]", "", text)
        text = "".join(
            c for c in self._uni.normalize("NFD", text)
            if not self._uni.combining(c)
        )
        text = self._re.sub(r"[^\w\s\-']", "", text, flags=self._re.UNICODE)
        return self._re.sub(r"\s+", " ", text).strip().lower()

    # ── Public API (AuthorityBackend protocol) ─────────────────────────

    async def match_person(self, name: str) -> dict[str, Any] | None:
        import asyncio  # noqa: PLC0415

        FUZZY_MIN_SIM = 0.45

        def _sync() -> dict[str, Any] | None:
            norm = self._normalize_mazal(name)
            if not norm:
                return None
            conn = self._get_conn()
            cur = conn.cursor()
            try:
                # 1. Exact (fast path, uses the hash index)
                cur.execute(
                    """
                    SELECT n.nli_id, a.entity_type, a.preferred_name_heb,
                           a.preferred_name_lat, a.dates, a.aleph_id
                    FROM mazal_name_index n
                    JOIN mazal_authorities a ON n.nli_id = a.nli_id
                    WHERE n.normalized_name = %s AND n.entity_type = 'person'
                    LIMIT 1
                    """,
                    (norm,),
                )
                row = cur.fetchone()
                if row:
                    return {
                        "mazal_id": row[0],
                        "entity_type": row[1],
                        "preferred_name_heb": row[2],
                        "preferred_name_lat": row[3],
                        "dates": row[4],
                        "aleph_id": row[5],
                    }

                # 2. Fuzzy trigram fallback (for spelling variants / orthographic differences
                # after our normalization). Requires pg_trgm + GIN index on the column.
                # Graceful: if the extension/operator is unavailable we just return None.
                try:
                    cur.execute(
                        """
                        SELECT n.nli_id, a.entity_type, a.preferred_name_heb,
                               a.preferred_name_lat, a.dates, a.aleph_id,
                               similarity(n.normalized_name, %s) AS sim
                        FROM mazal_name_index n
                        JOIN mazal_authorities a ON n.nli_id = a.nli_id
                        WHERE n.normalized_name %% %s
                          AND n.entity_type = 'person'
                        ORDER BY sim DESC
                        LIMIT 1
                        """,
                        (norm, norm),
                    )
                    row = cur.fetchone()
                    if row and row[6] is not None and float(row[6]) >= FUZZY_MIN_SIM:
                        return {
                            "mazal_id": row[0],
                            "entity_type": row[1],
                            "preferred_name_heb": row[2],
                            "preferred_name_lat": row[3],
                            "dates": row[4],
                            "aleph_id": row[5],
                            # Mark that this was a fuzzy hit so downstream (date guard,
                            # UI) can surface lower confidence or reasoning if desired.
                            "_fuzzy": True,
                            "_fuzzy_sim": float(row[6]),
                        }
                except Exception:  # noqa: BLE001 — no pg_trgm, no index, or syntax
                    pass
                return None
            finally:
                cur.close()

        return await asyncio.to_thread(_sync)

    async def match_place(self, text: str) -> dict[str, Any] | None:
        import asyncio  # noqa: PLC0415

        FUZZY_MIN_SIM = 0.45

        def _sync() -> dict[str, Any] | None:
            norm = self._normalize_kima(text)
            if not norm:
                return None
            conn = self._get_conn()
            cur = conn.cursor()
            try:
                # 1. Exact
                cur.execute(
                    """
                    SELECT p.kima_id, p.primary_heb, p.primary_rom,
                           p.wikidata_id, p.viaf_id, p.geonames_id,
                           p.mazal_nli_id, p.lat, p.lon
                    FROM kima_name_index n
                    JOIN kima_places p ON n.kima_id = p.kima_id
                    WHERE n.normalized_name = %s
                    LIMIT 1
                    """,
                    (norm,),
                )
                row = cur.fetchone()
                if row:
                    wd = row[3]
                    if not wd:
                        return None
                    return {
                        "wikidata_uri": f"https://www.wikidata.org/entity/{wd}",
                        "kima_id": row[0],
                        "primary_heb": row[1],
                        "primary_rom": row[2],
                        "wikidata_id": wd,
                        "viaf_id": row[4],
                        "geonames_id": row[5],
                        "mazal_nli_id": row[6],
                        "lat": row[7],
                        "lon": row[8],
                    }

                # 2. Fuzzy trigram fallback (proximity for Hebrew place-name variants).
                try:
                    cur.execute(
                        """
                        SELECT p.kima_id, p.primary_heb, p.primary_rom,
                               p.wikidata_id, p.viaf_id, p.geonames_id,
                               p.mazal_nli_id, p.lat, p.lon,
                               similarity(n.normalized_name, %s) AS sim
                        FROM kima_name_index n
                        JOIN kima_places p ON n.kima_id = p.kima_id
                        WHERE n.normalized_name %% %s
                        ORDER BY sim DESC
                        LIMIT 1
                        """,
                        (norm, norm),
                    )
                    row = cur.fetchone()
                    if row and row[9] is not None and float(row[9]) >= FUZZY_MIN_SIM:
                        wd = row[3]
                        if not wd:
                            return None
                        res = {
                            "wikidata_uri": f"https://www.wikidata.org/entity/{wd}",
                            "kima_id": row[0],
                            "primary_heb": row[1],
                            "primary_rom": row[2],
                            "wikidata_id": wd,
                            "viaf_id": row[4],
                            "geonames_id": row[5],
                            "mazal_nli_id": row[6],
                            "lat": row[7],
                            "lon": row[8],
                            "_fuzzy": True,
                            "_fuzzy_sim": float(row[9]),
                        }
                        return res
                except Exception:  # noqa: BLE001
                    pass
                return None
            finally:
                cur.close()

        return await asyncio.to_thread(_sync)


def build_authority_backend(
    mazal_matcher: Any | None = None,
    kima_matcher: Any | None = None,
) -> AuthorityBackend:
    """Return the right backend based on ``AUTHORITY_MODE`` env var.

    Modes:
    * ``postgres`` — Heroku Postgres (production default once tables imported).
    * ``modal``    — Modal HTTPS endpoint (legacy).
    * ``local``    — local SQLite matchers (dev / fallback).
    """
    mode = os.getenv("AUTHORITY_MODE", "local").lower()

    if mode == "postgres":
        dsn = os.getenv("DATABASE_URL", "")
        if dsn.startswith("postgres://"):
            dsn = dsn.replace("postgres://", "postgresql://", 1)
        if not dsn:
            logger.warning(
                "AUTHORITY_MODE=postgres but DATABASE_URL not set — "
                "falling back to local SQLite"
            )
        else:
            logger.info("Authority backend: Postgres")
            return PostgresAuthorityBackend(dsn=dsn)

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
