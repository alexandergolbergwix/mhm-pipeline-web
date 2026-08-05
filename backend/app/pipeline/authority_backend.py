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

    async def match_person(
        self,
        name: str,
        dates: str | None = None,
        *,
        ms_year: int | None = None,
        role: str = "",
    ) -> dict[str, Any] | None:
        """Return a dict with mazal_id + details, abstain metadata, or None.

        When homonyms cannot be disambiguated the dict carries ``_abstain``
        and ``homonym_candidates`` (no ``mazal_id``).
        """
        ...

    async def match_person_candidates(
        self,
        name: str,
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """Return Mazal person rows for *name* (exact normalized name match)."""
        ...

    async def match_place(self, text: str) -> dict[str, Any] | None:
        """Return a dict with wikidata_uri + enrichment row, or None on no hit."""
        ...

    async def match_mazal_place(self, text: str) -> dict[str, Any] | None:
        """Return a Mazal authority row for a place name, or None on no hit.

        Separate from match_place (KIMA) so place entities can obtain an NLI
        ID even when KIMA has no coordinates for them.
        """
        ...

    async def match_work(self, title: str) -> dict[str, Any] | None:
        """Return a Mazal authority row for a work title (entity_type=work)."""
        ...

    async def match_corporate(self, name: str) -> dict[str, Any] | None:
        """Return a Mazal authority row for a corporate body."""
        ...

    async def match_subject(self, name: str) -> dict[str, Any] | None:
        """Return a Mazal authority row for a topical subject heading."""
        ...

    async def resolve_personality_mazal_id(
        self,
        name: str,
        *,
        dates: str | None,
        current_id: str,
        main_marc_tag: str | None,
    ) -> dict[str, Any] | None:
        """Re-query for tag-100 אישיות when *current_id* is a subject/work heading."""
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

    async def match_person_candidates(
        self,
        name: str,
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        import asyncio  # noqa: PLC0415

        if self._mazal is None or self._mazal.index is None:
            return []

        def _sync() -> list[dict[str, Any]]:
            idx = self._mazal.index
            norm = idx.normalize_name(name)
            if not norm:
                return []
            cur = idx.conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT a.nli_id, a.entity_type, a.preferred_name_heb,
                           a.preferred_name_lat, a.dates, a.aleph_id,
                           a.main_marc_tag
                    FROM name_index n
                    JOIN authorities a ON n.nli_id = a.nli_id
                    WHERE n.normalized_name = ? AND n.entity_type = 'person'
                    ORDER BY
                      CASE a.main_marc_tag
                        WHEN '100' THEN 1
                        WHEN '400' THEN 2
                        ELSE 3
                      END,
                      (a.dates IS NOT NULL AND a.dates != '') DESC,
                      a.nli_id ASC
                    LIMIT ?
                    """,
                    (norm, limit),
                )
                rows = cur.fetchall()
                return [_person_row_to_dict(r) for r in rows]
            except Exception:  # noqa: BLE001
                return []
            finally:
                cur.close()

        return await asyncio.to_thread(_sync)

    async def match_person(
        self,
        name: str,
        dates: str | None = None,
        *,
        ms_year: int | None = None,
        role: str = "",
    ) -> dict[str, Any] | None:
        import asyncio  # noqa: PLC0415

        from app.pipeline.homonym_scoring import pick_mazal_candidate  # noqa: PLC0415

        if self._mazal is None:
            return None

        def _sync() -> dict[str, Any] | None:
            idx = self._mazal.index
            if idx is None:
                return None
            norm = idx.normalize_name(name)
            if not norm:
                return None
            cur = idx.conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT a.nli_id, a.entity_type, a.preferred_name_heb,
                           a.preferred_name_lat, a.dates, a.aleph_id,
                           a.main_marc_tag
                    FROM name_index n
                    JOIN authorities a ON n.nli_id = a.nli_id
                    WHERE n.normalized_name = ? AND n.entity_type = 'person'
                    ORDER BY
                      CASE a.main_marc_tag
                        WHEN '100' THEN 1
                        WHEN '400' THEN 2
                        ELSE 3
                      END,
                      (a.dates IS NOT NULL AND a.dates != '') DESC,
                      a.nli_id ASC
                    LIMIT 8
                    """,
                    (norm,),
                )
                rows = cur.fetchall()
                candidates = [_person_row_to_dict(r) for r in rows]
            finally:
                cur.close()

            if candidates:
                decision = pick_mazal_candidate(
                    candidates,
                    marc_name=name,
                    marc_dates=dates,
                    ms_year=ms_year,
                    role=role,
                )
                result = _match_person_from_decision(decision)
                if result is not None:
                    return result

            # Fuzzy substring fallback (legacy behaviour when no exact name hit).
            if len(norm) < 6:
                return None
            cur = idx.conn.cursor()
            try:
                row = cur.execute(
                    """
                    SELECT a.nli_id, a.entity_type, a.preferred_name_heb,
                           a.preferred_name_lat, a.dates, a.aleph_id,
                           a.main_marc_tag
                    FROM name_index n
                    JOIN authorities a ON n.nli_id = a.nli_id
                    WHERE n.entity_type = 'person'
                      AND (
                        n.normalized_name LIKE ?
                        OR ? LIKE '%' || n.normalized_name || '%'
                      )
                    ORDER BY
                      (a.dates IS NOT NULL AND a.dates != '') DESC,
                      abs(length(n.normalized_name) - length(?)),
                      length(n.normalized_name),
                      n.nli_id
                    LIMIT 1
                    """,
                    (f"%{norm}%", norm, norm),
                ).fetchone()
                if row is None:
                    return None
                return _person_row_to_dict(row, fuzzy=True)
            finally:
                cur.close()

        return await asyncio.to_thread(_sync)

    async def resolve_personality_mazal_id(
        self,
        name: str,
        *,
        dates: str | None,
        current_id: str,
        main_marc_tag: str | None,
    ) -> dict[str, Any] | None:
        import asyncio  # noqa: PLC0415

        if not current_id or (main_marc_tag or "").strip() == "100":
            return None

        def _sync() -> dict[str, Any] | None:
            if self._mazal is None or self._mazal.index is None:
                return None
            idx = self._mazal.index
            norm = idx.normalize_name(name)
            if not norm:
                return None
            cur = idx.conn.cursor()
            try:
                if dates:
                    cur.execute(
                        """
                        SELECT a.nli_id, a.entity_type, a.preferred_name_heb,
                               a.preferred_name_lat, a.dates, a.aleph_id,
                               a.main_marc_tag
                        FROM name_index n
                        JOIN authorities a ON n.nli_id = a.nli_id
                        WHERE n.normalized_name = ?
                          AND n.entity_type = 'person'
                          AND a.main_marc_tag = '100'
                          AND a.dates = ?
                        LIMIT 1
                        """,
                        (norm, dates.strip()),
                    )
                    row = cur.fetchone()
                    if row:
                        return _personality_row_from_sqlite(row, current_id)
                cur.execute(
                    """
                    SELECT a.nli_id, a.entity_type, a.preferred_name_heb,
                           a.preferred_name_lat, a.dates, a.aleph_id,
                           a.main_marc_tag
                    FROM name_index n
                    JOIN authorities a ON n.nli_id = a.nli_id
                    WHERE n.normalized_name = ?
                      AND n.entity_type = 'person'
                      AND a.main_marc_tag = '100'
                    ORDER BY (a.dates IS NOT NULL AND a.dates != '') DESC,
                             a.nli_id ASC
                    LIMIT 1
                    """,
                    (norm,),
                )
                row = cur.fetchone()
                if row:
                    return _personality_row_from_sqlite(row, current_id)
            except Exception:  # noqa: BLE001 — main_marc_tag column may be absent
                return None
            finally:
                cur.close()
            return None

        return await asyncio.to_thread(_sync)

    async def match_mazal_place(self, text: str) -> dict[str, Any] | None:
        import asyncio  # noqa: PLC0415

        if self._mazal is None:
            return None

        def _sync() -> dict[str, Any] | None:
            if self._mazal.index is None:
                return None
            nli_id = self._mazal.match_place(text)
            if not nli_id:
                return None
            record = self._mazal.get_record(nli_id) or {}
            return {
                "mazal_id": nli_id,
                "entity_type": "place",
                "preferred_name_heb": record.get("preferred_name_heb", ""),
                "preferred_name_lat": record.get("preferred_name_lat", ""),
            }

        return await asyncio.to_thread(_sync)

    async def match_work(self, title: str) -> dict[str, Any] | None:
        return await self._match_mazal_typed(title, "work")

    async def match_corporate(self, name: str) -> dict[str, Any] | None:
        return await self._match_mazal_typed(name, "corporate")

    async def match_subject(self, name: str) -> dict[str, Any] | None:
        return await self._match_mazal_typed(name, "subject")

    async def _match_mazal_typed(
        self, text: str, entity_type: str,
    ) -> dict[str, Any] | None:
        import asyncio  # noqa: PLC0415

        if self._mazal is None:
            return None

        def _sync() -> dict[str, Any] | None:
            if self._mazal.index is None:
                return None
            lookup = {
                "work": self._mazal.match_work,
                "corporate": self._mazal.match_corporate,
                "subject": lambda n: self._mazal.index.lookup(n, "subject"),
            }.get(entity_type)
            if lookup is None:
                return None
            nli_id = lookup(text)
            if not nli_id:
                return None
            record = self._mazal.get_record(nli_id) or {}
            return {
                "mazal_id": nli_id,
                "entity_type": entity_type,
                "preferred_name_heb": record.get("preferred_name_heb", ""),
                "preferred_name_lat": record.get("preferred_name_lat", ""),
                "main_marc_tag": record.get("main_marc_tag"),
            }

        return await asyncio.to_thread(_sync)

    async def match_place(self, text: str) -> dict[str, Any] | None:
        import asyncio  # noqa: PLC0415

        if self._kima is None:
            return None

        def _sync() -> dict[str, Any] | None:
            idx = self._kima.index
            if idx is None or not hasattr(idx, "lookup_place"):
                return None
            from converter.authority.kima_disambiguate import pick_kima_place_row  # noqa: PLC0415

            row = idx.lookup_place(text) or {}
            fuzzy = False
            if not row:
                norm = idx.normalize_name(text)
                if len(norm) < 4:
                    return None
                cur = idx.conn.cursor()
                cur.execute(
                    """
                    SELECT p.kima_id, p.primary_heb, p.primary_rom,
                           p.wikidata_id, p.viaf_id, p.geonames_id, p.lat, p.lon
                    FROM name_index n
                    JOIN places p ON n.kima_id = p.kima_id
                    WHERE n.normalized_name LIKE ?
                       OR ? LIKE '%' || n.normalized_name || '%'
                    ORDER BY abs(length(n.normalized_name) - length(?)),
                             length(n.normalized_name)
                    LIMIT 12
                    """,
                    (f"%{norm}%", norm, norm),
                )
                hits = [dict(h) for h in cur.fetchall()]
                if not hits:
                    return None
                picked = pick_kima_place_row(
                    hits,
                    norm,
                    normalize_primary=idx.normalize_name,
                )
                if picked is None:
                    return None
                row = dict(picked)
                fuzzy = True
            wd = row.get("wikidata_id")
            if not wd:
                return None
            if fuzzy:
                row["_fuzzy"] = True
            return {"wikidata_uri": f"https://www.wikidata.org/entity/{wd}", **row}

        return await asyncio.to_thread(_sync)


class ModalAuthorityBackend:
    """Calls the deployed ``mhm-authority`` Modal app over HTTPS.

    Used when ``AUTHORITY_MODE=modal`` and ``MODAL_AUTHORITY_URL`` is set.
    Each HTTP call is wrapped by authority.py's ``cache_lookup_or_call``
    before reaching here — cache hits never call through to Modal.
    """

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)

    async def match_person(
        self,
        name: str,
        dates: str | None = None,
        *,
        ms_year: int | None = None,
        role: str = "",
    ) -> dict[str, Any] | None:
        try:
            r = await self._client.post(
                f"{self._base}/match_person",
                json={"name": name, "dates": dates},
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

    async def match_person_candidates(
        self,
        name: str,
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        return []

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

    async def match_mazal_place(self, text: str) -> dict[str, Any] | None:
        # Modal legacy backend does not expose a dedicated Mazal place endpoint.
        return None

    async def match_work(self, title: str) -> dict[str, Any] | None:
        return None

    async def match_corporate(self, name: str) -> dict[str, Any] | None:
        return None

    async def match_subject(self, name: str) -> dict[str, Any] | None:
        return None

    async def resolve_personality_mazal_id(
        self,
        name: str,
        *,
        dates: str | None,
        current_id: str,
        main_marc_tag: str | None,
    ) -> dict[str, Any] | None:
        return None


def _person_row_to_dict(
    row: tuple[Any, ...] | Any,
    *,
    fuzzy: bool = False,
    fuzzy_sim: float | None = None,
) -> dict[str, Any]:
    if hasattr(row, "keys"):
        keys = row.keys()
        main_tag = row["main_marc_tag"] if "main_marc_tag" in keys else None
        out = {
            "mazal_id": row["nli_id"],
            "entity_type": row["entity_type"],
            "preferred_name_heb": row["preferred_name_heb"],
            "preferred_name_lat": row["preferred_name_lat"],
            "dates": row["dates"],
            "aleph_id": row["aleph_id"],
            "main_marc_tag": main_tag,
        }
    else:
        out = {
            "mazal_id": row[0],
            "entity_type": row[1],
            "preferred_name_heb": row[2],
            "preferred_name_lat": row[3],
            "dates": row[4],
            "aleph_id": row[5],
            "main_marc_tag": row[6] if len(row) > 6 else None,
        }
    if fuzzy:
        out["_fuzzy"] = True
        if fuzzy_sim is not None:
            out["_fuzzy_sim"] = fuzzy_sim
    return out


def _match_person_from_decision(decision: Any) -> dict[str, Any] | None:
    from app.pipeline.homonym_scoring import MazalMatchDecision  # noqa: PLC0415

    if not isinstance(decision, MazalMatchDecision):
        return None
    meta = {
        "homonym_candidates": decision.homonym_candidates,
        "homonym_abstain_reason": decision.reason,
        "personality_count": decision.personality_count,
    }
    if decision.abstain:
        return {"_abstain": True, **meta}
    if decision.winner:
        out = dict(decision.winner)
        out.update(meta)
        return out
    return None


def _personality_row_from_sqlite(row: Any, previous_id: str) -> dict[str, Any] | None:
    """Build personality rematch dict from a SQLite row; None if same id."""
    nli_id = row["nli_id"] if hasattr(row, "keys") else row[0]
    if not nli_id or str(nli_id) == str(previous_id):
        return None
    if hasattr(row, "keys"):
        return {
            "mazal_id": nli_id,
            "entity_type": row["entity_type"],
            "preferred_name_heb": row["preferred_name_heb"],
            "preferred_name_lat": row["preferred_name_lat"],
            "dates": row["dates"],
            "aleph_id": row["aleph_id"],
            "main_marc_tag": row["main_marc_tag"],
            "personality_rematch_from": str(previous_id),
        }
    return {
        "mazal_id": row[0],
        "entity_type": row[1],
        "preferred_name_heb": row[2],
        "preferred_name_lat": row[3],
        "dates": row[4],
        "aleph_id": row[5],
        "main_marc_tag": row[6],
        "personality_rematch_from": str(previous_id),
    }


def _personality_row_from_pg(row: tuple[Any, ...], previous_id: str) -> dict[str, Any] | None:
    if not row or not row[0] or str(row[0]) == str(previous_id):
        return None
    return {
        "mazal_id": row[0],
        "entity_type": row[1],
        "preferred_name_heb": row[2],
        "preferred_name_lat": row[3],
        "dates": row[4],
        "aleph_id": row[5],
        "main_marc_tag": row[6],
        "personality_rematch_from": str(previous_id),
    }


class PostgresAuthorityBackend:
    """Queries Mazal / KIMA data from Heroku Postgres.

    Tables ``mazal_authorities``, ``mazal_name_index``, ``kima_places`` and
    ``kima_name_index`` are populated once via the import scripts.  Every
    lookup is a single indexed query — no SQLite file on disk, no Modal
    cold-start, no extra dyno costs.

    Normalization mirrors the original SQLite matchers exactly so the
    ``normalized_name`` column values are compatible.
    """

    def __init__(
        self,
        dsn: str,
        fallback: LocalAuthorityBackend | None = None,
    ) -> None:
        import re  # noqa: PLC0415
        import unicodedata  # noqa: PLC0415

        self._dsn = dsn
        self._fallback = fallback
        self._conn: Any = None
        self._re = re
        self._uni = unicodedata

    def _get_conn(self) -> Any:
        import psycopg2  # noqa: PLC0415

        def _open() -> Any:
            conn = psycopg2.connect(self._dsn)
            conn.autocommit = True
            return conn

        if self._conn is None or self._conn.closed:
            self._conn = _open()
        else:
            try:
                cur = self._conn.cursor()
                cur.execute("SELECT 1")
                cur.close()
            except Exception:  # noqa: BLE001
                self._conn = _open()
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

    async def match_person_candidates(
        self,
        name: str,
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        import asyncio  # noqa: PLC0415

        def _sync() -> list[dict[str, Any]]:
            norm = self._normalize_mazal(name)
            if not norm:
                return []
            conn = self._get_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT n.nli_id, a.entity_type, a.preferred_name_heb,
                           a.preferred_name_lat, a.dates, a.aleph_id,
                           a.main_marc_tag
                    FROM mazal_name_index n
                    JOIN mazal_authorities a ON n.nli_id = a.nli_id
                    WHERE n.normalized_name = %s AND n.entity_type = 'person'
                    ORDER BY
                      CASE a.main_marc_tag
                        WHEN '100' THEN 1
                        WHEN '400' THEN 2
                        ELSE 3
                      END,
                      (a.dates IS NOT NULL AND a.dates <> '') DESC,
                      a.nli_id ASC
                    LIMIT %s
                    """,
                    (norm, limit),
                )
                return [_person_row_to_dict(r) for r in cur.fetchall()]
            finally:
                cur.close()

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Postgres Mazal candidates failed for %r: %s", name, exc)
            if self._fallback is not None:
                return await self._fallback.match_person_candidates(name, limit=limit)
            return []

    async def match_person(
        self,
        name: str,
        dates: str | None = None,
        *,
        ms_year: int | None = None,
        role: str = "",
    ) -> dict[str, Any] | None:
        import asyncio  # noqa: PLC0415

        from app.pipeline.homonym_scoring import pick_mazal_candidate  # noqa: PLC0415

        FUZZY_MIN_SIM = 0.45

        def _sync() -> dict[str, Any] | None:
            norm = self._normalize_mazal(name)
            if not norm:
                return None
            conn = self._get_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT n.nli_id, a.entity_type, a.preferred_name_heb,
                           a.preferred_name_lat, a.dates, a.aleph_id,
                           a.main_marc_tag
                    FROM mazal_name_index n
                    JOIN mazal_authorities a ON n.nli_id = a.nli_id
                    WHERE n.normalized_name = %s AND n.entity_type = 'person'
                    ORDER BY
                      CASE a.main_marc_tag
                        WHEN '100' THEN 1
                        WHEN '400' THEN 2
                        ELSE 3
                      END,
                      (a.dates IS NOT NULL AND a.dates <> '') DESC,
                      a.nli_id ASC
                    LIMIT 8
                    """,
                    (norm,),
                )
                candidates = [_person_row_to_dict(r) for r in cur.fetchall()]
            finally:
                cur.close()

            if candidates:
                decision = pick_mazal_candidate(
                    candidates,
                    marc_name=name,
                    marc_dates=dates,
                    ms_year=ms_year,
                    role=role,
                )
                result = _match_person_from_decision(decision)
                if result is not None:
                    return result

            # Fuzzy trigram fallback when no exact normalized-name hit.
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT n.nli_id, a.entity_type, a.preferred_name_heb,
                           a.preferred_name_lat, a.dates, a.aleph_id,
                           a.main_marc_tag,
                           similarity(n.normalized_name, %s) AS sim
                    FROM mazal_name_index n
                    JOIN mazal_authorities a ON n.nli_id = a.nli_id
                    WHERE n.normalized_name %% %s
                      AND n.entity_type = 'person'
                    ORDER BY sim DESC,
                      CASE a.main_marc_tag
                        WHEN '100' THEN 1
                        WHEN '400' THEN 2
                        ELSE 3
                      END,
                      (a.dates IS NOT NULL AND a.dates <> '') DESC,
                      a.nli_id ASC
                    LIMIT 1
                    """,
                    (norm, norm),
                )
                row = cur.fetchone()
                if row and row[7] is not None and float(row[7]) >= FUZZY_MIN_SIM:
                    return _person_row_to_dict(row[:7], fuzzy=True, fuzzy_sim=float(row[7]))
            except Exception:  # noqa: BLE001
                pass
            finally:
                cur.close()
            return None

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Postgres Mazal lookup failed for %r: %s", name, exc)
            if self._fallback is not None:
                return await self._fallback.match_person(
                    name, dates=dates, ms_year=ms_year, role=role,
                )
            return None

    async def resolve_personality_mazal_id(
        self,
        name: str,
        *,
        dates: str | None,
        current_id: str,
        main_marc_tag: str | None,
    ) -> dict[str, Any] | None:
        import asyncio  # noqa: PLC0415

        FUZZY_MIN_SIM = 0.45
        if not current_id or (main_marc_tag or "").strip() == "100":
            return None

        def _sync() -> dict[str, Any] | None:
            norm = self._normalize_mazal(name)
            if not norm:
                return None
            conn = self._get_conn()
            cur = conn.cursor()
            try:
                if dates:
                    cur.execute(
                        """
                        SELECT n.nli_id, a.entity_type, a.preferred_name_heb,
                               a.preferred_name_lat, a.dates, a.aleph_id,
                               a.main_marc_tag
                        FROM mazal_name_index n
                        JOIN mazal_authorities a ON n.nli_id = a.nli_id
                        WHERE n.normalized_name = %s
                          AND n.entity_type = 'person'
                          AND a.main_marc_tag = '100'
                          AND a.dates = %s
                        LIMIT 1
                        """,
                        (norm, dates.strip()),
                    )
                    row = cur.fetchone()
                    if row:
                        return _personality_row_from_pg(row, current_id)

                cur.execute(
                    """
                    SELECT n.nli_id, a.entity_type, a.preferred_name_heb,
                           a.preferred_name_lat, a.dates, a.aleph_id,
                           a.main_marc_tag
                    FROM mazal_name_index n
                    JOIN mazal_authorities a ON n.nli_id = a.nli_id
                    WHERE n.normalized_name = %s
                      AND n.entity_type = 'person'
                      AND a.main_marc_tag = '100'
                    ORDER BY (a.dates IS NOT NULL AND a.dates <> '') DESC,
                             a.nli_id ASC
                    LIMIT 1
                    """,
                    (norm,),
                )
                row = cur.fetchone()
                if row:
                    return _personality_row_from_pg(row, current_id)

                try:
                    cur.execute(
                        """
                        SELECT n.nli_id, a.entity_type, a.preferred_name_heb,
                               a.preferred_name_lat, a.dates, a.aleph_id,
                               a.main_marc_tag,
                               similarity(n.normalized_name, %s) AS sim
                        FROM mazal_name_index n
                        JOIN mazal_authorities a ON n.nli_id = a.nli_id
                        WHERE n.normalized_name %% %s
                          AND n.entity_type = 'person'
                          AND a.main_marc_tag = '100'
                        ORDER BY sim DESC,
                          (a.dates IS NOT NULL AND a.dates <> '') DESC,
                          a.nli_id ASC
                        LIMIT 1
                        """,
                        (norm, norm),
                    )
                    row = cur.fetchone()
                    if row and row[7] is not None and float(row[7]) >= FUZZY_MIN_SIM:
                        result = _personality_row_from_pg(row[:7], current_id)
                        if result:
                            result["_fuzzy"] = True
                            result["_fuzzy_sim"] = float(row[7])
                        return result
                except Exception:  # noqa: BLE001
                    pass
                return None
            finally:
                cur.close()

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Postgres personality rematch failed for %r: %s", name, exc,
            )
            if self._fallback is not None:
                return await self._fallback.resolve_personality_mazal_id(
                    name,
                    dates=dates,
                    current_id=current_id,
                    main_marc_tag=main_marc_tag,
                )
            return None

    async def match_mazal_place(self, text: str) -> dict[str, Any] | None:
        """Look up a place in the Mazal authority index by name."""
        import asyncio  # noqa: PLC0415

        def _sync() -> dict[str, Any] | None:
            norm = self._normalize_mazal(text)
            if not norm:
                return None
            conn = self._get_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT n.nli_id, a.preferred_name_heb, a.preferred_name_lat
                    FROM mazal_name_index n
                    JOIN mazal_authorities a ON n.nli_id = a.nli_id
                    WHERE n.normalized_name = %s AND n.entity_type = 'place'
                    ORDER BY a.nli_id ASC
                    LIMIT 1
                    """,
                    (norm,),
                )
                row = cur.fetchone()
                if row:
                    return {
                        "mazal_id": row[0],
                        "entity_type": "place",
                        "preferred_name_heb": row[1] or "",
                        "preferred_name_lat": row[2] or "",
                    }
                return None
            finally:
                cur.close()

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Postgres Mazal place lookup failed for %r: %s", text, exc)
            if self._fallback is not None:
                return await self._fallback.match_mazal_place(text)
            return None

    async def match_place(self, text: str) -> dict[str, Any] | None:
        import asyncio  # noqa: PLC0415

        from converter.authority.kima_disambiguate import pick_kima_place_row  # noqa: PLC0415

        FUZZY_MIN_SIM = 0.45
        FUZZY_CANDIDATE_LIMIT = 12

        def _row_dict(row: tuple[Any, ...]) -> dict[str, Any]:
            return {
                "kima_id": row[0],
                "primary_heb": row[1],
                "primary_rom": row[2],
                "wikidata_id": row[3],
                "viaf_id": row[4],
                "geonames_id": row[5],
                "mazal_nli_id": row[6],
                "lat": row[7],
                "lon": row[8],
            }

        def _accepted(row: dict[str, Any], *, fuzzy: bool = False, sim: float | None = None) -> dict[str, Any] | None:
            wd = str(row.get("wikidata_id") or "").strip()
            if not wd:
                return None
            out = {
                "wikidata_uri": f"https://www.wikidata.org/entity/{wd}",
                **row,
                "wikidata_id": wd,
            }
            if fuzzy:
                out["_fuzzy"] = True
                if sim is not None:
                    out["_fuzzy_sim"] = sim
            return out

        def _sync() -> dict[str, Any] | None:
            norm = self._normalize_kima(text)
            if not norm:
                return None
            conn = self._get_conn()
            cur = conn.cursor()
            try:
                # 1. Exact — all rows; abstain on conflicting Wikidata QIDs (W-84).
                cur.execute(
                    """
                    SELECT p.kima_id, p.primary_heb, p.primary_rom,
                           p.wikidata_id, p.viaf_id, p.geonames_id,
                           p.mazal_nli_id, p.lat, p.lon
                    FROM kima_name_index n
                    JOIN kima_places p ON n.kima_id = p.kima_id
                    WHERE n.normalized_name = %s
                    """,
                    (norm,),
                )
                exact_rows = [_row_dict(r) for r in cur.fetchall()]
                picked = pick_kima_place_row(
                    exact_rows,
                    norm,
                    normalize_primary=self._normalize_kima,
                )
                if picked is not None:
                    return _accepted(picked)
                if exact_rows:
                    # Conflicting QIDs with no unique primary-name disambiguation.
                    return None

                # 2. Fuzzy trigram fallback — top candidates, same QID abstain.
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
                        LIMIT %s
                        """,
                        (norm, norm, FUZZY_CANDIDATE_LIMIT),
                    )
                    fuzzy_hits: list[dict[str, Any]] = []
                    best_sim: float | None = None
                    for r in cur.fetchall():
                        if r[9] is None or float(r[9]) < FUZZY_MIN_SIM:
                            continue
                        sim = float(r[9])
                        if best_sim is None:
                            best_sim = sim
                        entry = _row_dict(r)
                        entry["_sim"] = sim
                        fuzzy_hits.append(entry)
                    if not fuzzy_hits:
                        return None
                    # Deduplicate by kima_id keeping highest similarity.
                    by_kima: dict[Any, dict[str, Any]] = {}
                    for hit in fuzzy_hits:
                        kid = hit.get("kima_id")
                        prev = by_kima.get(kid)
                        if prev is None or float(hit.get("_sim") or 0) > float(prev.get("_sim") or 0):
                            by_kima[kid] = hit
                    candidates = list(by_kima.values())
                    picked_fuzzy = pick_kima_place_row(
                        candidates,
                        norm,
                        normalize_primary=self._normalize_kima,
                    )
                    if picked_fuzzy is None:
                        return None
                    return _accepted(
                        {k: v for k, v in picked_fuzzy.items() if k != "_sim"},
                        fuzzy=True,
                        sim=float(picked_fuzzy.get("_sim") or best_sim or 0),
                    )
                except Exception:  # noqa: BLE001
                    pass
                return None
            finally:
                cur.close()

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Postgres KIMA lookup failed for %r: %s", text, exc)
            if self._fallback is not None:
                return await self._fallback.match_place(text)
            return None

    async def match_work(self, title: str) -> dict[str, Any] | None:
        return await self._match_work_tiered(title)

    async def _match_work_tiered(self, title: str) -> dict[str, Any] | None:
        import asyncio  # noqa: PLC0415

        from app.pipeline.work_title_match import (  # noqa: PLC0415
            normalize_work_title_for_match,
            work_title_variants,
        )

        FUZZY_MIN_SIM = 0.45

        def _row_dict(row: tuple[Any, ...], *, fuzzy: bool = False, sim: float | None = None) -> dict[str, Any]:
            out = {
                "mazal_id": row[0],
                "entity_type": row[1],
                "preferred_name_heb": row[2],
                "preferred_name_lat": row[3],
                "dates": row[4],
                "aleph_id": row[5],
                "main_marc_tag": row[6],
            }
            if fuzzy and sim is not None:
                out["_fuzzy"] = True
                out["_fuzzy_sim"] = sim
            return out

        def _sync_for_variant(norm: str) -> dict[str, Any] | None:
            if not norm:
                return None
            conn = self._get_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT n.nli_id, a.entity_type, a.preferred_name_heb,
                           a.preferred_name_lat, a.dates, a.aleph_id,
                           a.main_marc_tag
                    FROM mazal_name_index n
                    JOIN mazal_authorities a ON n.nli_id = a.nli_id
                    WHERE n.normalized_name = %s AND n.entity_type = 'work'
                    ORDER BY a.nli_id ASC
                    LIMIT 1
                    """,
                    (norm,),
                )
                row = cur.fetchone()
                if row:
                    return _row_dict(row)

                cur.execute(
                    """
                    SELECT n.nli_id, a.entity_type, a.preferred_name_heb,
                           a.preferred_name_lat, a.dates, a.aleph_id,
                           a.main_marc_tag
                    FROM mazal_name_index n
                    JOIN mazal_authorities a ON n.nli_id = a.nli_id
                    WHERE n.entity_type = 'work'
                      AND (
                        n.normalized_name LIKE %s
                        OR %s LIKE '%%' || n.normalized_name || '%%'
                      )
                    ORDER BY length(n.normalized_name) ASC, a.nli_id ASC
                    LIMIT 1
                    """,
                    (f"%{norm}%", norm),
                )
                row = cur.fetchone()
                if row:
                    result = _row_dict(row)
                    result["work_match_variant"] = "containment"
                    return result

                try:
                    cur.execute(
                        """
                        SELECT n.nli_id, a.entity_type, a.preferred_name_heb,
                               a.preferred_name_lat, a.dates, a.aleph_id,
                               a.main_marc_tag,
                               similarity(n.normalized_name, %s) AS sim
                        FROM mazal_name_index n
                        JOIN mazal_authorities a ON n.nli_id = a.nli_id
                        WHERE n.normalized_name %% %s
                          AND n.entity_type = 'work'
                        ORDER BY sim DESC, a.nli_id ASC
                        LIMIT 1
                        """,
                        (norm, norm),
                    )
                    row = cur.fetchone()
                    if row and row[7] is not None and float(row[7]) >= FUZZY_MIN_SIM:
                        result = _row_dict(row[:7], fuzzy=True, sim=float(row[7]))
                        result["work_match_variant"] = "fuzzy"
                        return result
                except Exception:  # noqa: BLE001
                    pass
                return None
            finally:
                cur.close()

        def _sync() -> dict[str, Any] | None:
            for variant in work_title_variants(title):
                norm = self._normalize_mazal(variant)
                hit = _sync_for_variant(norm)
                if hit:
                    hit["work_match_input"] = variant
                    if variant != title:
                        hit["work_match_variant"] = hit.get("work_match_variant") or "normalized"
                    return hit
            core = normalize_work_title_for_match(title)
            if core:
                return _sync_for_variant(self._normalize_mazal(core))
            return None

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Postgres Mazal work lookup failed for %r: %s", title, exc)
            if self._fallback is not None:
                return await self._fallback.match_work(title)
            return None

    async def match_corporate(self, name: str) -> dict[str, Any] | None:
        return await self._match_mazal_entity_type(name, "corporate")

    async def match_subject(self, name: str) -> dict[str, Any] | None:
        return await self._match_mazal_entity_type(
            name, "subject",
            order_sql=(
                "CASE a.main_marc_tag WHEN '150' THEN 1 WHEN '450' THEN 2 ELSE 3 END, "
                "a.nli_id ASC"
            ),
        )

    async def _match_mazal_entity_type(
        self,
        text: str,
        entity_type: str,
        *,
        order_sql: str = "a.nli_id ASC",
    ) -> dict[str, Any] | None:
        import asyncio  # noqa: PLC0415

        def _sync() -> dict[str, Any] | None:
            norm = self._normalize_mazal(text)
            if not norm:
                return None
            conn = self._get_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    f"""
                    SELECT n.nli_id, a.entity_type, a.preferred_name_heb,
                           a.preferred_name_lat, a.dates, a.aleph_id,
                           a.main_marc_tag
                    FROM mazal_name_index n
                    JOIN mazal_authorities a ON n.nli_id = a.nli_id
                    WHERE n.normalized_name = %s AND n.entity_type = %s
                    ORDER BY {order_sql}
                    LIMIT 1
                    """,
                    (norm, entity_type),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "mazal_id": row[0],
                    "entity_type": row[1],
                    "preferred_name_heb": row[2],
                    "preferred_name_lat": row[3],
                    "dates": row[4],
                    "aleph_id": row[5],
                    "main_marc_tag": row[6],
                }
            finally:
                cur.close()

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Postgres Mazal %s lookup failed for %r: %s", entity_type, text, exc,
            )
            if self._fallback is not None:
                fb = {
                    "work": self._fallback.match_work,
                    "corporate": self._fallback.match_corporate,
                    "subject": self._fallback.match_subject,
                }.get(entity_type)
                return await fb(text) if fb else None
            return None


def _pg_dsn_for_psycopg2(raw: str) -> str:
    """Convert a Heroku DATABASE_URL to a psycopg2-compatible DSN with SSL.

    psycopg2 uses ``postgresql://`` not ``postgres://``, and Heroku's
    RDS-backed instances require ``sslmode=require`` for ALL connections
    (even from the same dyno).  This helper normalises both.
    """
    dsn = raw.strip().replace("postgresql+asyncpg://", "postgresql://", 1)
    if dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql://", 1)
    if dsn and "localhost" not in dsn and "127.0.0.1" not in dsn:
        sep = "&" if "?" in dsn else "?"
        if "sslmode" not in dsn:
            dsn = dsn + sep + "sslmode=require"
    return dsn


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
        from app.settings import get_settings  # noqa: PLC0415

        dsn = _pg_dsn_for_psycopg2(get_settings().database_url)
        if not dsn or "localhost" in dsn or "127.0.0.1" in dsn:
            logger.warning(
                "AUTHORITY_MODE=postgres but database_url is missing or local — "
                "falling back to local SQLite"
            )
        else:
            logger.info("Authority backend: Postgres")
            return PostgresAuthorityBackend(
                dsn=dsn,
                fallback=LocalAuthorityBackend(
                    mazal_matcher=mazal_matcher,
                    kima_matcher=kima_matcher,
                ),
            )

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
