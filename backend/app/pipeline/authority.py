"""Authority resolution — drives the real desktop matchers.

This file does NOT re-implement Mazal / VIAF / Wikidata matching. It
imports desktop's matcher classes from ``converter.authority.*`` and
calls them from the FastAPI event loop via ``run_in_threadpool`` (they
are synchronous — SQLite + ``requests`` — and would block otherwise).

The output ``Candidate`` shape mirrors the desktop pipeline's
``marc_authority_matches`` so the Review UI renders identically.

Configuration (env vars, optional — sensible local-dev defaults):

* ``MAZAL_DB_PATH``     path to ``mazal_index.db`` (defaults to the
                        desktop install's location)
* ``KIMA_DB_PATH``      path to ``kima_index.db`` (defaults to the
                        copied ``backend/data/kima/kima_index.db``)
* ``DISABLE_VIAF=1``    skip VIAF network calls (offline / dev)
* ``DISABLE_WIKIDATA=1``  skip Wikidata SPARQL calls
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.pipeline.authority_backend import AuthorityBackend, build_authority_backend

logger = logging.getLogger(__name__)

# Defaults that work on the dev machine.
_DEFAULT_MAZAL = (
    "/Users/alexandergo/Documents/Doctorat/pipeline/"
    "converter/authority/mazal_index.db"
)


@dataclass
class Candidate:
    """One authority candidate returned for an entity (UI-stable shape)."""

    matched_name: str
    confidence: str = "low"          # high | medium | low
    source: str = "heuristic"        # mazal | viaf | wikidata | cross_source | heuristic
    mazal_id: str = ""
    viaf_id: str = ""
    wikidata_qid: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class AuthorityMatcher(ABC):
    @abstractmethod
    async def match(
        self, entity: dict[str, str], marc_record: dict[str, Any],
    ) -> list[Candidate]:
        ...


# ── Real desktop-backed matcher ─────────────────────────────────────────


class DesktopMatcher(AuthorityMatcher):
    """Wraps the real ``converter.authority.*`` matchers.

    Each call runs the sync desktop code on a worker thread, then maps
    the desktop response (which is desktop's own match dict) onto our
    ``Candidate`` shape. If Mazal returns no hit but VIAF does, we still
    emit a candidate with source='viaf'. Cross-source hits emit a single
    candidate with source='cross_source' + the full ``sources`` list in
    ``payload`` so the Review UI's "X of 3 sources agree" reasoning
    renders correctly.
    """

    def __init__(self) -> None:
        self._mazal = _load_mazal_matcher()
        self._viaf = _load_viaf_matcher() if os.getenv("DISABLE_VIAF") != "1" else None
        self._wikidata = _load_wikidata_matcher() if os.getenv("DISABLE_WIKIDATA") != "1" else None
        self._kima = _load_kima_matcher() if os.getenv("DISABLE_KIMA") != "1" else None
        # Route Mazal/KIMA calls through local SQLite or the Modal HTTPS backend.
        self._authority_backend: AuthorityBackend = build_authority_backend(
            mazal_matcher=self._mazal,
            kima_matcher=self._kima,
        )
        # Per-request caches populated by match_* calls so that the companion
        # get_details / enrich_place methods avoid a second backend round-trip.
        self._mazal_detail_cache: dict[str, dict] = {}
        self._kima_detail_cache: dict[str, dict] = {}

    async def match(
        self, entity: dict[str, str], marc_record: dict[str, Any],
        *,
        # Optional inference-cache plumbing. When supplied, every
        # external authority lookup (Mazal / VIAF / Wikidata / KIMA)
        # routes through the shared inference_cache table. First call
        # across the team populates; everyone else hits cache.
        # ``skip_cache=True`` forces fresh upstream calls; the refresh
        # still lands in the cache so the next caller warm-hits.
        db_session: Any | None = None,
        user_id: Any | None = None,
        skip_cache: bool = False,
    ) -> list[Candidate]:
        raw = (entity.get("text") or "").strip()
        # Strip leading/trailing ASCII straight-quotes and Unicode curly-quotes
        # that sometimes wrap MARC-extracted entity strings (e.g. '"חביב, שמעון אבן"').
        # Mazal normalises them away internally so it still matched; VIAF/Wikidata
        # do not, causing them to miss persons that are genuinely in their indexes.
        text = raw.strip('\'""\u201c\u201d\u2018\u2019')
        if not text:
            return []
        role = entity.get("role", "")
        entity_kind = entity.get("kind", "")
        # MARC $d dates (e.g. "1138-1204") narrow Mazal homonym resolution.
        marc_dates = (entity.get("dates") or "").strip() or None
        marc_field = (entity.get("field") or "").strip() or None
        return await self._match_one(
            text=text, role=role, entity_kind=entity_kind, marc_record=marc_record,
            db_session=db_session, user_id=user_id, skip_cache=skip_cache,
            marc_dates=marc_dates,
            marc_field=marc_field,
        )

    # ── Cached per-matcher wrappers ────────────────────────────────────

    async def _cached(
        self, *, kind: str, query_summary: dict[str, Any],
        fetch: Any,
        db_session: Any, user_id: Any, skip_cache: bool,
    ) -> Any:
        """Route through inference_cache when db_session is set."""
        if db_session is None:
            return await fetch()
        from app.pipeline.inference_cache import cache_lookup_or_call  # noqa: PLC0415
        return await cache_lookup_or_call(
            db_session, kind=kind, query_summary=query_summary,
            fetch=fetch, user_id=user_id, skip_cache=skip_cache,
        )

    async def _kima_match_place(
        self, text: str, *, db_session: Any, user_id: Any, skip_cache: bool,
    ) -> str | None:
        _mode = os.getenv("AUTHORITY_MODE", "local").lower()
        # Guard: skip when no KIMA source is available at all.
        if self._kima is None and _mode not in ("modal", "postgres"):
            return None

        async def _f() -> str | None:
            result = await self._authority_backend.match_place(text)
            if result is None:
                return None
            # Side-load enrichment so _kima_enrich_place avoids a second call.
            self._kima_detail_cache[text] = result
            return result.get("wikidata_uri")

        return await self._cached(
            kind="authority.kima",
            query_summary={"op": "match_place", "text": text},
            fetch=_f, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
        )

    async def _kima_enrich_place(
        self, text: str, *, db_session: Any, user_id: Any, skip_cache: bool,
    ) -> dict[str, Any] | None:
        """Return the full KIMA index row for *text*, or None.

        When the Modal backend is active, ``_kima_match_place`` already
        fetches the enrichment row in the same HTTP call and stores it in
        ``_kima_detail_cache``.  We return that cached value directly to
        avoid a second backend round-trip.
        """
        # Fast path: Modal/Postgres backend populated this during match_place.
        if text in self._kima_detail_cache:
            return self._kima_detail_cache[text]

        _mode = os.getenv("AUTHORITY_MODE", "local").lower()
        if self._kima is None and _mode not in ("modal", "postgres"):
            return None

        async def _f() -> dict[str, Any] | None:
            if _mode == "postgres":
                # Delegate through the cached match path; it will populate
                # the instance cache as a side-effect and respect inference cache.
                try:
                    await self._kima_match_place(
                        text, db_session=db_session, user_id=user_id, skip_cache=skip_cache
                    )
                    return self._kima_detail_cache.get(text)
                except Exception:  # noqa: BLE001
                    return None

            if self._kima is None:
                return None
            def _sync() -> dict[str, Any] | None:
                idx = self._kima.index  # type: ignore[union-attr]
                if idx is None:
                    return None
                return idx.lookup_place(text)  # type: ignore[attr-defined]

            return await asyncio.to_thread(_sync)

        return await self._cached(
            kind="authority.kima",
            query_summary={"op": "lookup_place", "text": text},
            fetch=_f, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
        )

    async def _mazal_match_person(
        self,
        text: str,
        *,
        db_session: Any,
        user_id: Any,
        skip_cache: bool,
        marc_dates: str | None = None,
        ms_year: int | None = None,
        role: str = "",
    ) -> dict[str, Any] | None:
        _mode = os.getenv("AUTHORITY_MODE", "local").lower()
        if self._mazal is None and _mode not in ("modal", "postgres"):
            return None

        async def _f() -> dict[str, Any] | None:
            result = await self._authority_backend.match_person(
                text,
                dates=marc_dates,
                ms_year=ms_year,
                role=role,
            )
            if result is None:
                return None
            mid = result.get("mazal_id")
            if mid:
                self._mazal_detail_cache[str(mid)] = result
            return result

        query_summary: dict[str, Any] = {"op": "match_person", "text": text}
        if marc_dates:
            query_summary["dates"] = marc_dates
        if ms_year is not None:
            query_summary["ms_year"] = ms_year
        if role:
            query_summary["role"] = role
        return await self._cached(
            kind="authority.mazal",
            query_summary=query_summary,
            fetch=_f,
            db_session=db_session,
            user_id=user_id,
            skip_cache=skip_cache,
        )

    async def _mazal_match_place_authority(
        self, text: str, *, db_session: Any, user_id: Any, skip_cache: bool,
    ) -> dict[str, Any] | None:
        """Look up *text* as a place in the Mazal authority index.

        Distinct from KIMA (_kima_match_place): Mazal covers the NLI authority
        file and provides an NLI ID, while KIMA provides coordinates + Wikidata
        QID.  Both can fire for the same place string — they complement each other.
        """
        _mode = os.getenv("AUTHORITY_MODE", "local").lower()
        if self._mazal is None and _mode not in ("modal", "postgres"):
            return None

        async def _f() -> dict[str, Any] | None:
            return await self._authority_backend.match_mazal_place(text)

        return await self._cached(
            kind="authority.mazal",
            query_summary={"op": "match_place", "text": text},
            fetch=_f, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
        )

    async def _mazal_match_work(
        self, title: str, *, marc_record: dict[str, Any] | None,
        db_session: Any, user_id: Any, skip_cache: bool,
    ) -> str | None:
        """Match a work title against the Mazal authority index."""
        from app.pipeline.work_title_match import work_title_variants  # noqa: PLC0415

        _mode = os.getenv("AUTHORITY_MODE", "local").lower()
        if self._mazal is None and _mode not in ("postgres",):
            return None

        variants = work_title_variants(title, marc_record)

        async def _try_one(variant: str) -> str | None:
            async def _f() -> str | None:
                if _mode == "postgres":
                    row = await self._authority_backend.match_work(variant)
                    if row and row.get("mazal_id"):
                        self._mazal_detail_cache[str(row["mazal_id"])] = row
                        return str(row["mazal_id"])
                    return None
                if self._mazal is None:
                    return None
                nli_id = await asyncio.to_thread(self._mazal.match_work, variant)
                return str(nli_id) if nli_id else None

            return await self._cached(
                kind="authority.mazal",
                query_summary={"op": "match_work", "text": variant},
                fetch=_f, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
            )

        for variant in variants:
            hit = await _try_one(variant)
            if hit:
                return hit
        return None

    async def _mazal_match_corporate(
        self, name: str, *, db_session: Any, user_id: Any, skip_cache: bool,
    ) -> str | None:
        _mode = os.getenv("AUTHORITY_MODE", "local").lower()
        if self._mazal is None and _mode not in ("postgres",):
            return None

        async def _f() -> str | None:
            if _mode == "postgres":
                row = await self._authority_backend.match_corporate(name)
                if row and row.get("mazal_id"):
                    self._mazal_detail_cache[str(row["mazal_id"])] = row
                    return str(row["mazal_id"])
                return None
            if self._mazal is None:
                return None
            nli_id = await asyncio.to_thread(self._mazal.match_corporate, name)
            return str(nli_id) if nli_id else None

        return await self._cached(
            kind="authority.mazal",
            query_summary={"op": "match_corporate", "text": name},
            fetch=_f, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
        )

    async def _mazal_match_subject(
        self, name: str, *, db_session: Any, user_id: Any, skip_cache: bool,
    ) -> str | None:
        _mode = os.getenv("AUTHORITY_MODE", "local").lower()
        if self._mazal is None and _mode not in ("postgres",):
            return None

        async def _f() -> str | None:
            if _mode == "postgres":
                row = await self._authority_backend.match_subject(name)
                if row and row.get("mazal_id"):
                    self._mazal_detail_cache[str(row["mazal_id"])] = row
                    return str(row["mazal_id"])
                return None
            if self._mazal is None or self._mazal.index is None:
                return None
            nli_id = await asyncio.to_thread(
                self._mazal.index.lookup, name, "subject",
            )
            return str(nli_id) if nli_id else None

        return await self._cached(
            kind="authority.mazal",
            query_summary={"op": "match_subject", "text": name},
            fetch=_f, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
        )

    async def _mazal_get_details(
        self, mazal_id: str, *, db_session: Any, user_id: Any, skip_cache: bool,
    ) -> dict[str, Any] | None:
        """Return Mazal person detail dict for *mazal_id*.

        When the Modal backend is active, ``_mazal_match_person`` already
        fetches the full detail dict in the same HTTP call and stores it in
        ``_mazal_detail_cache``.  We return that cached value directly to
        avoid a second backend round-trip.
        """
        # Fast path: Modal/Postgres backend populated this during match_person.
        if mazal_id in self._mazal_detail_cache:
            return self._mazal_detail_cache[mazal_id]

        _mode = os.getenv("AUTHORITY_MODE", "local").lower()
        if self._mazal is None and _mode not in ("modal", "postgres"):
            return None

        async def _f() -> dict[str, Any]:
            if _mode == "postgres":
                # For postgres the full details were already returned by
                # match_person and stashed in the instance cache under the id.
                # If we reach here without it, fall back to a direct id lookup.
                try:
                    import psycopg2  # noqa: PLC0415

                    from app.pipeline.authority_backend import (  # noqa: PLC0415
                        _pg_dsn_for_psycopg2,
                    )

                    dsn = _pg_dsn_for_psycopg2(os.getenv("DATABASE_URL", ""))
                    if not dsn:
                        return {}
                    conn = psycopg2.connect(dsn)
                    conn.autocommit = True
                    cur = conn.cursor()
                    try:
                        cur.execute(
                            """
                            SELECT entity_type, preferred_name_heb, preferred_name_lat,
                                   dates, aleph_id
                            FROM mazal_authorities
                            WHERE nli_id = %s
                            LIMIT 1
                            """,
                            (mazal_id,),
                        )
                        row = cur.fetchone()
                        if row:
                            return {
                                "entity_type": row[0],
                                "preferred_name_heb": row[1],
                                "preferred_name_lat": row[2],
                                "dates": row[3],
                                "aleph_id": row[4],
                            }
                        return {}
                    finally:
                        cur.close()
                        conn.close()
                except Exception:  # noqa: BLE001
                    return {}
            return await asyncio.to_thread(self._mazal.get_person_details, mazal_id)

        return await self._cached(
            kind="authority.mazal",
            query_summary={"op": "get_details", "mazal_id": mazal_id},
            fetch=_f, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
        )

    async def _viaf_match_with_metadata(
        self,
        text: str,
        *,
        marc_dates: str | None = None,
        db_session: Any,
        user_id: Any,
        skip_cache: bool,
    ) -> dict[str, Any] | None:
        if self._viaf is None:
            return None
        async def _f() -> dict[str, Any] | None:
            return await asyncio.to_thread(
                self._viaf.match_person_with_metadata, text, marc_dates,
            )
        query_summary: dict[str, Any] = {"op": "match_person_with_metadata", "text": text}
        if marc_dates:
            query_summary["dates"] = marc_dates
        result = await self._cached(
            kind="authority.viaf",
            query_summary=query_summary,
            fetch=_f, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
        )
        # Schema-migration guard: cache entries written before birth_year /
        # death_year were added to match_person_with_metadata's return dict
        # lack those keys entirely (vs. having them with value None). Force a
        # one-time refresh so the new schema is stored and subsequent calls
        # return dates. Without this, the 30-day authority cache TTL keeps
        # returning stale entries that make the Dates tab show "—" forever.
        if result is not None and "birth_year" not in result:
            result = await self._cached(
                kind="authority.viaf",
                query_summary=query_summary,
                fetch=_f, db_session=db_session, user_id=user_id, skip_cache=True,
            )
        return result

    async def _wikidata_match_person(
        self, text: str, *, db_session: Any, user_id: Any, skip_cache: bool,
    ) -> str | None:
        if self._wikidata is None:
            return None
        async def _f() -> str | None:
            r = await asyncio.to_thread(self._wikidata.match_person, text)
            return str(r) if r else None
        return await self._cached(
            kind="authority.wikidata",
            query_summary={"op": "match_person", "text": text},
            fetch=_f, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
        )

    async def _wikidata_match_by_mazal(
        self, mazal_id: str, *, db_session: Any, user_id: Any, skip_cache: bool,
    ) -> str | None:
        """Resolve a Mazal/J9U authority id to a Wikidata QID via P8189."""
        if self._wikidata is None or not mazal_id:
            return None
        async def _f() -> str | None:
            r = await asyncio.to_thread(self._wikidata.find_qid_by_mazal, mazal_id)
            return str(r) if r else None
        return await self._cached(
            kind="authority.wikidata",
            query_summary={"op": "find_qid_by_mazal", "mazal_id": mazal_id},
            fetch=_f, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
        )

    async def _wikidata_match_by_viaf(
        self, viaf_id: str, *, db_session: Any, user_id: Any, skip_cache: bool,
    ) -> str | None:
        """Resolve a VIAF cluster id to a Wikidata QID via P214."""
        if self._wikidata is None or not viaf_id:
            return None

        async def _f() -> str | None:
            r = await asyncio.to_thread(self._wikidata.find_qid_by_viaf, viaf_id)
            return str(r) if r else None

        return await self._cached(
            kind="authority.wikidata",
            query_summary={"op": "find_qid_by_viaf", "viaf_id": viaf_id},
            fetch=_f,
            db_session=db_session,
            user_id=user_id,
            skip_cache=skip_cache,
        )

    @staticmethod
    def _viaf_name_type_allowed(op: str, name_type: str) -> bool:
        """Fail-closed: SRU hits must carry a matching VIAF nameType."""
        if not op.startswith("sru_"):
            return True
        if not name_type:
            return False
        expected: dict[str, frozenset[str]] = {
            "sru_geographic": frozenset({"Geographic"}),
            "sru_uniform_title": frozenset({"UniformTitleWork", "Title"}),
            "sru_corporate": frozenset({"Corporate"}),
        }
        allowed = expected.get(op)
        if allowed is None:
            return True
        return name_type in allowed

    async def _viaf_cluster_payload(
        self, viaf_id: str, *, resolve_op: str,
    ) -> dict[str, Any]:
        if not viaf_id or self._viaf is None:
            return {}
        try:
            ids = await asyncio.to_thread(self._viaf.get_cluster_identifiers, viaf_id)
            name_type = str(ids.get("name_type") or "").strip()
            if not name_type:
                name_type = await asyncio.to_thread(self._viaf.cluster_name_type, viaf_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("VIAF cluster payload failed for %s: %s", viaf_id, exc)
            return {}
        return {
            "viaf_id": viaf_id,
            "name_type": name_type,
            "viaf_resolve_op": resolve_op,
            "gnd": ids.get("gnd"),
            "lc": ids.get("lc"),
            "isni": ids.get("isni"),
            "bnf": ids.get("bnf"),
            "j9u": ids.get("j9u"),
        }

    async def _wikidata_dates(
        self, qid: str, *, db_session: Any, user_id: Any, skip_cache: bool,
    ) -> dict[str, int | None] | None:
        if self._wikidata is None:
            return None
        async def _f() -> dict[str, int | None]:
            b, d = await asyncio.to_thread(self._wikidata.find_dates_by_qid, qid)
            return {"birth_year": int(b) if b else None,
                    "death_year": int(d) if d else None}
        return await self._cached(
            kind="authority.wikidata",
            query_summary={"op": "find_dates_by_qid", "qid": qid},
            fetch=_f, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
        )

    async def _wikidata_enrich_qid(
        self, qid: str, *, db_session: Any, user_id: Any, skip_cache: bool,
    ) -> dict[str, Any] | None:
        """One SPARQL call per confirmed QID: fetches Hebrew label,
        English description, and P214 (VIAF) for VIAF cross-enrichment.

        Result shape:
            { "he_label": str|None, "en_description": str|None,
              "viaf_id": str|None }

        Cached 30 days under authority.wikidata / op=enrich_qid so the
        extra SPARQL hop only fires on first use (and on skip_cache=True).
        """
        if self._wikidata is None:
            return None

        async def _f() -> dict[str, Any]:
            import requests  # noqa: PLC0415

            # P214 (VIAF cluster ID) via the desktop matcher's
            # find_viaf_by_qid which handles its own on-disk cache.
            viaf_from_wd: str | None = await asyncio.to_thread(
                self._wikidata.find_viaf_by_qid, qid,
            )

            # Hebrew label + English description — one WDQS query.
            he_label: str | None = None
            en_description: str | None = None
            try:
                safe_qid = qid.strip()
                if safe_qid.startswith("Q") and safe_qid[1:].isdigit():
                    query = (
                        "SELECT ?heLabel ?enDesc WHERE { "
                        f"OPTIONAL {{ wd:{safe_qid} rdfs:label ?heLabel . "
                        "FILTER(LANG(?heLabel)=\"he\") }} "
                        f"OPTIONAL {{ wd:{safe_qid} schema:description ?enDesc . "
                        "FILTER(LANG(?enDesc)=\"en\") }} "
                        "} LIMIT 1"
                    )
                    resp = await asyncio.to_thread(
                        lambda: requests.get(
                            "https://query.wikidata.org/sparql",
                            params={"query": query, "format": "json"},
                            headers={
                                "Accept": "application/sparql-results+json",
                                "User-Agent": "mhm-pipeline-web/1.0",
                            },
                            timeout=10,
                        )
                    )
                    if resp.ok:
                        bindings = resp.json().get("results", {}).get("bindings", [])
                        if bindings:
                            b = bindings[0]
                            he_label = b.get("heLabel", {}).get("value")
                            en_description = b.get("enDesc", {}).get("value")
            except Exception as exc:  # noqa: BLE001
                logger.debug("_wikidata_enrich_qid SPARQL failed for %s: %s", qid, exc)

            return {
                "viaf_id": viaf_from_wd,
                "he_label": he_label,
                "en_description": en_description,
            }

        return await self._cached(
            kind="authority.wikidata",
            query_summary={"op": "enrich_qid", "qid": qid},
            fetch=_f, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
        )

    @staticmethod
    def _viaf_id_from_uri(uri: str | None) -> str:
        if not uri:
            return ""
        raw = str(uri).strip()
        if raw.isdigit():
            return raw
        match = re.search(r"/viaf/(\d+)", raw)
        return match.group(1) if match else ""

    @staticmethod
    def _primary_author_from_marc(marc_record: dict[str, Any]) -> str | None:
        for author in marc_record.get("authors") or []:
            if isinstance(author, dict):
                name = str(author.get("name") or "").strip()
                if name:
                    return name
        return None

    @staticmethod
    def _entity_kind_for_candidate(
        *,
        normalized_kind: str,
        is_place: bool,
        kima_hit: bool,
    ) -> str:
        if is_place or kima_hit:
            return "place"
        if normalized_kind in ("work", "corporate", "organization", "topic", "meeting"):
            return normalized_kind
        return "person"

    @staticmethod
    def _non_person_anchor_ready(
        *,
        normalized_kind: str,
        is_place: bool,
        mazal_id: str,
        kima_hit: bool,
        gazetteer_hit: bool,
    ) -> bool:
        if mazal_id:
            return True
        if is_place and (kima_hit or gazetteer_hit):
            return True
        return False

    async def _viaf_match_typed(
        self,
        *,
        op: str,
        text: str,
        matcher_name: str,
        db_session: Any,
        user_id: Any,
        skip_cache: bool,
    ) -> tuple[str, dict[str, Any]]:
        if self._viaf is None:
            return "", {}
        matcher = getattr(self._viaf, matcher_name, None)
        if matcher is None:
            return "", {}

        async def _f() -> str | None:
            return await asyncio.to_thread(matcher, text)

        uri = await self._cached(
            kind="authority.viaf",
            query_summary={"op": op, "text": text},
            fetch=_f,
            db_session=db_session,
            user_id=user_id,
            skip_cache=skip_cache,
        )
        viaf_id = self._viaf_id_from_uri(uri)
        if not viaf_id:
            return "", {}
        meta = await self._viaf_cluster_payload(viaf_id, resolve_op=op)
        return viaf_id, meta

    @staticmethod
    def _reject_human_qid(qid: str, *, normalized_kind: str) -> str:
        if normalized_kind == "person" or not qid:
            return qid
        if qid in ("Q5", "Q15632617"):
            return ""
        return qid

    async def _post_qid_enrich_viaf(
        self,
        *,
        wikidata_qid: str,
        viaf_id: str,
        sources: list[str],
        reasoning_parts: list[str],
        enrichment_meta: dict[str, str],
        db_session: Any,
        user_id: Any,
        skip_cache: bool,
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        wd_enrich: dict[str, Any] | None = None
        viaf_meta: dict[str, Any] | None = None
        if not wikidata_qid or self._wikidata is None:
            return viaf_id, wd_enrich, viaf_meta
        try:
            wd_enrich = await self._wikidata_enrich_qid(
                wikidata_qid,
                db_session=db_session,
                user_id=user_id,
                skip_cache=skip_cache,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Wikidata enrich_qid failed for %s: %s", wikidata_qid, exc)
            return viaf_id, wd_enrich, viaf_meta
        if wd_enrich and not viaf_id:
            wd_viaf = (wd_enrich.get("viaf_id") or "").strip()
            if wd_viaf:
                viaf_id = wd_viaf
                enrichment_meta["viaf_resolve_op"] = "p214"
                if "viaf" not in sources:
                    sources.append("viaf")
                reasoning_parts.append(
                    f"VIAF cross-enriched from Wikidata P214 ({wd_viaf}).",
                )
                viaf_meta = await self._viaf_cluster_payload(viaf_id, resolve_op="p214")
                enrichment_meta["viaf_name_type"] = str(viaf_meta.get("name_type") or "")
        return viaf_id, wd_enrich, viaf_meta

    async def _wikidata_match_typed(
        self,
        *,
        op: str,
        text: str,
        matcher_name: str,
        db_session: Any,
        user_id: Any,
        skip_cache: bool,
        author: str | None = None,
    ) -> str | None:
        if self._wikidata is None:
            return None
        matcher = getattr(self._wikidata, matcher_name, None)
        if matcher is None:
            return None

        async def _f() -> str | None:
            if matcher_name == "match_work":
                result = await asyncio.to_thread(matcher, text, author)
            else:
                result = await asyncio.to_thread(matcher, text)
            return str(result) if result else None

        query_summary: dict[str, Any] = {"op": op, "text": text}
        if author:
            query_summary["author"] = author
        return await self._cached(
            kind="authority.wikidata",
            query_summary=query_summary,
            fetch=_f,
            db_session=db_session,
            user_id=user_id,
            skip_cache=skip_cache,
        )

    async def _apply_non_person_external_enrichment(
        self,
        *,
        text: str,
        normalized_kind: str,
        is_place: bool,
        mazal_id: str,
        wikidata_qid: str,
        viaf_id: str,
        kima_hit: bool,
        gazetteer_hit: bool,
        marc_record: dict[str, Any],
        sources: list[str],
        reasoning_parts: list[str],
        wd_enrich: dict[str, Any] | None,
        viaf_meta: dict[str, Any] | None,
        db_session: Any,
        user_id: Any,
        skip_cache: bool,
    ) -> tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, dict[str, str]]:
        """Conservative VIAF/Wikidata enrichment for non-person entities."""
        enrichment_meta: dict[str, str] = {}
        anchored = self._non_person_anchor_ready(
            normalized_kind=normalized_kind,
            is_place=is_place,
            mazal_id=mazal_id,
            kima_hit=kima_hit,
            gazetteer_hit=gazetteer_hit,
        )

        if self._wikidata is not None and mazal_id and not wikidata_qid:
            qid_from_mazal = await self._wikidata_match_by_mazal(
                mazal_id,
                db_session=db_session,
                user_id=user_id,
                skip_cache=skip_cache,
            )
            if qid_from_mazal:
                wikidata_qid = self._reject_human_qid(
                    str(qid_from_mazal), normalized_kind=normalized_kind,
                )
                if wikidata_qid:
                    enrichment_meta["wikidata_resolve_op"] = "p8189"
                    if "wikidata" not in sources:
                        sources.append("wikidata")
                    reasoning_parts.append(
                        f"Wikidata hit via Mazal P8189 ({wikidata_qid}).",
                    )

        if wikidata_qid and self._wikidata is not None:
            viaf_id, wd_enrich, viaf_meta = await self._post_qid_enrich_viaf(
                wikidata_qid=wikidata_qid,
                viaf_id=viaf_id,
                sources=sources,
                reasoning_parts=reasoning_parts,
                enrichment_meta=enrichment_meta,
                db_session=db_session,
                user_id=user_id,
                skip_cache=skip_cache,
            )

        if self._viaf is not None and not viaf_id and anchored:
            viaf_op = ""
            matcher_name = ""
            if is_place:
                viaf_op = "sru_geographic"
                matcher_name = "match_place"
            elif normalized_kind == "work" and mazal_id:
                viaf_op = "sru_uniform_title"
                matcher_name = "match_work"
            elif normalized_kind in ("corporate", "organization", "meeting") and mazal_id:
                viaf_op = "sru_corporate"
                matcher_name = "match_corporate"

            if viaf_op and matcher_name:
                vid, meta = await self._viaf_match_typed(
                    op=viaf_op,
                    text=text,
                    matcher_name=matcher_name,
                    db_session=db_session,
                    user_id=user_id,
                    skip_cache=skip_cache,
                )
                if vid:
                    name_type = str(meta.get("name_type") or "")
                    if not self._viaf_name_type_allowed(viaf_op, name_type):
                        reasoning_parts.append(
                            f"VIAF {vid} rejected (nameType={name_type!r}).",
                        )
                    else:
                        viaf_id = vid
                        viaf_meta = meta
                        enrichment_meta["viaf_resolve_op"] = viaf_op
                        enrichment_meta["viaf_name_type"] = name_type
                        if "viaf" not in sources:
                            sources.append("viaf")
                        reasoning_parts.append(f"VIAF hit ({viaf_id}).")
                        if self._wikidata is not None and not wikidata_qid:
                            qid_from_viaf = await self._wikidata_match_by_viaf(
                                viaf_id,
                                db_session=db_session,
                                user_id=user_id,
                                skip_cache=skip_cache,
                            )
                            qid_from_viaf = self._reject_human_qid(
                                str(qid_from_viaf or ""),
                                normalized_kind=normalized_kind,
                            )
                            if qid_from_viaf:
                                wikidata_qid = qid_from_viaf
                                enrichment_meta["wikidata_resolve_op"] = "p214"
                                if "wikidata" not in sources:
                                    sources.append("wikidata")
                                reasoning_parts.append(
                                    f"Wikidata hit via VIAF P214 ({wikidata_qid}).",
                                )
                                viaf_id, wd_enrich, viaf_meta = await self._post_qid_enrich_viaf(
                                    wikidata_qid=wikidata_qid,
                                    viaf_id=viaf_id,
                                    sources=sources,
                                    reasoning_parts=reasoning_parts,
                                    enrichment_meta=enrichment_meta,
                                    db_session=db_session,
                                    user_id=user_id,
                                    skip_cache=skip_cache,
                                )

        if self._wikidata is not None and not wikidata_qid:
            label_allowed = False
            if normalized_kind == "work" and (mazal_id or viaf_id):
                label_allowed = True
            elif normalized_kind in ("corporate", "organization", "meeting") and mazal_id:
                label_allowed = True

            if label_allowed:
                matcher_name = (
                    "match_work"
                    if normalized_kind == "work"
                    else "match_corporate"
                )
                author = (
                    self._primary_author_from_marc(marc_record)
                    if normalized_kind == "work"
                    else None
                )
                qid = await self._wikidata_match_typed(
                    op=f"label_{matcher_name}",
                    text=text,
                    matcher_name=matcher_name,
                    db_session=db_session,
                    user_id=user_id,
                    skip_cache=skip_cache,
                    author=author,
                )
                if qid:
                    wikidata_qid = self._reject_human_qid(
                        str(qid), normalized_kind=normalized_kind,
                    )
                    if not wikidata_qid:
                        reasoning_parts.append(
                            f"Wikidata label hit {qid} rejected (human QID).",
                        )
                    else:
                        enrichment_meta["wikidata_resolve_op"] = "label"
                        if "wikidata" not in sources:
                            sources.append("wikidata")
                        reasoning_parts.append(f"Wikidata label hit ({wikidata_qid}).")
                        viaf_id, wd_enrich, viaf_meta = await self._post_qid_enrich_viaf(
                            wikidata_qid=wikidata_qid,
                            viaf_id=viaf_id,
                            sources=sources,
                            reasoning_parts=reasoning_parts,
                            enrichment_meta=enrichment_meta,
                            db_session=db_session,
                            user_id=user_id,
                            skip_cache=skip_cache,
                        )

        return wikidata_qid, viaf_id, wd_enrich, viaf_meta, enrichment_meta

    async def _match_one(
        self, *,
        text: str, role: str, entity_kind: str, marc_record: dict[str, Any],
        db_session: Any, user_id: Any, skip_cache: bool,
        marc_dates: str | None = None,
        marc_field: str | None = None,
    ) -> list[Candidate]:
        sources: list[str] = []
        mazal_id = ""
        viaf_id = ""
        wikidata_qid = ""
        birth_year: int | None = None
        death_year: int | None = None
        guards: list[str] = []
        reasoning_parts: list[str] = []
        kima_hit = False
        kima_payload: dict[str, Any] = {}
        mazal_details: dict[str, Any] | None = None
        viaf_meta: dict[str, Any] | None = None
        _wd_enrich: dict[str, Any] | None = None
        mazal_payload_extras: dict[str, Any] = {}
        work_match_meta: dict[str, Any] = {}
        ms_year = _record_year(marc_record)
        year_prov = _manuscript_year_provenance(marc_record)
        mazal_homonym_abstain = False

        # — KIMA (places only) —
        # KIMA is the desktop's geographic-authority adapter. It returns
        # a Wikidata URI for matched places, so we hand the QID straight
        # to the same wikidata_qid slot the persons path uses (with
        # source=kima).
        _mode = os.getenv("AUTHORITY_MODE", "local").lower()
        normalized_kind = entity_kind.lower().strip()
        from app.pipeline.entity_normalize import normalize_role_key  # noqa: PLC0415
        role_key = normalize_role_key(role)
        is_place = (
            normalized_kind in ("place", "location", "geographic")
            or role_key in ("place", "location", "geographic", "production_place")
            # Provenance-event places (541 $b acquisition, 583 $j conservation/
            # exhibition, and future ownership_place) must also fire KIMA.
            or role_key.endswith("_place")
            or (role_key == "subject" and _looks_like_place(text, marc_record, marc_field))
        )
        _non_person_kinds = frozenset(("work", "corporate", "organization", "topic", "meeting"))
        _is_person_entity = (
            not is_place and normalized_kind not in _non_person_kinds
        )
        if is_place and (self._kima is not None or _mode in ("modal", "postgres")):
            try:
                uri = await self._kima_match_place(
                    text, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
                )
                if uri:
                    kima_hit = True
                    sources.append("kima")
                    # Pull the QID off the URI for the wikidata column.
                    qid = uri.rsplit("/", 1)[-1]
                    if qid.startswith("Q"):
                        wikidata_qid = qid
                    reasoning_parts.append(f"KIMA hit ({uri}).")
                    kima_row = await self._kima_enrich_place(
                        text, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
                    )
                    if kima_row:
                        kima_payload = {
                            "kima_id":       kima_row.get("kima_id"),
                            "kima_heb":      kima_row.get("primary_heb"),
                            "kima_rom":      kima_row.get("primary_rom"),
                            "kima_lat":      kima_row.get("lat"),
                            "kima_lon":      kima_row.get("lon"),
                            "kima_geonames": kima_row.get("geonames_id"),
                            "kima_viaf_id":  kima_row.get("viaf_id") or "",
                            # mazal_nli_id links this KIMA place to the Mazal authority
                            # record (Rule W-29). Used below to backfill mazal_id when
                            # the Mazal place lookup misses.
                            "mazal_nli_id":  kima_row.get("mazal_nli_id") or "",
                            "_fuzzy":        kima_row.get("_fuzzy"),
                            "_fuzzy_sim":    kima_row.get("_fuzzy_sim"),
                        }
                        if kima_row.get("_fuzzy"):
                            reasoning_parts.append(
                                f"Fuzzy KIMA match (sim≈{kima_row.get('_fuzzy_sim', 0):.2f})."
                            )
            except Exception as exc:  # noqa: BLE001
                logger.warning("KIMA matcher raised for %r: %s", text, exc)

        # Ashkenazi-community fallback (Rule 60) — consulted ONLY after KIMA
        # misses, so a KIMA result is never overridden. Fills the diaspora
        # gap (Prague, Worms, Kraków …) KIMA is thin on. Produces the same
        # kima_lat/kima_lon payload slice the RDF + map code reads.
        gazetteer_hit = False
        if is_place and not kima_hit:
            try:
                from app.pipeline.ashkenazi_gazetteer import lookup as _ashk_lookup  # noqa: PLC0415

                gaz = _ashk_lookup(text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ashkenazi gazetteer raised for %r: %s", text, exc)
                gaz = None
            if gaz is not None:
                gazetteer_hit = True
                gaz_qid = str(gaz.get("wikidata_id") or "").strip()
                if gaz_qid.startswith("Q"):
                    wikidata_qid = gaz_qid
                kima_payload = {
                    "kima_lat": gaz["lat"],
                    "kima_lon": gaz["lon"],
                    "_source": "ashkenazi_gazetteer",
                }
                reasoning_parts.append("Ashkenazi gazetteer fallback hit.")

        # — Mazal work (works only) —
        if normalized_kind == "work" and (self._mazal is not None or _mode == "postgres"):
            try:
                work_mid = await self._mazal_match_work(
                    text,
                    marc_record=marc_record,
                    db_session=db_session, user_id=user_id, skip_cache=skip_cache,
                )
                if work_mid:
                    mazal_id = str(work_mid)
                    sources.append("mazal")
                    reasoning_parts.append(f"Mazal work hit ({work_mid}).")
                    mazal_details = self._mazal_detail_cache.get(str(work_mid)) or {}
                    cached = self._mazal_detail_cache.get(work_mid) or {}
                    work_match_meta = {
                        k: cached[k]
                        for k in (
                            "work_match_input",
                            "work_match_variant",
                            "_fuzzy_sim",
                        )
                        if cached.get(k) is not None
                    }
            except Exception as exc:  # noqa: BLE001
                logger.warning("Mazal work matcher raised for %r: %s", text, exc)

        # — Mazal corporate (institutions / collections / meetings) —
        if normalized_kind in ("corporate", "organization", "meeting") and (
            self._mazal is not None or _mode == "postgres"
        ):
            try:
                corp_mid = await self._mazal_match_corporate(
                    text, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
                )
                if corp_mid:
                    mazal_id = str(corp_mid)
                    mazal_details = self._mazal_detail_cache.get(str(corp_mid)) or {}
                    sources.append("mazal")
                    reasoning_parts.append(f"Mazal corporate hit ({corp_mid}).")
                    mazal_id, mazal_details, mazal_payload_extras = _apply_mazal_entity_type_gate(
                        is_place=is_place,
                        is_person_entity=_is_person_entity,
                        entity_kind=normalized_kind,
                        mazal_id=mazal_id,
                        mazal_details=mazal_details or None,
                        extras=mazal_payload_extras,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Mazal corporate matcher raised for %r: %s", text, exc)

        # — Mazal topical subject (650 / subject headings) —
        if normalized_kind == "topic" and (self._mazal is not None or _mode == "postgres"):
            try:
                sub_mid = await self._mazal_match_subject(
                    text, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
                )
                if sub_mid:
                    mazal_id = str(sub_mid)
                    mazal_details = self._mazal_detail_cache.get(str(sub_mid)) or {}
                    sources.append("mazal")
                    reasoning_parts.append(f"Mazal subject hit ({sub_mid}).")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Mazal subject matcher raised for %r: %s", text, exc)

        # — Mazal place (places only) —
        # When the entity is a place, look it up in the Mazal authority index to
        # obtain an NLI ID.  KIMA already provided coordinates + Wikidata QID;
        # this gives the NLI identifier for RDF owl:sameAs and Wikibase P8189.
        # We run this BEFORE the person matchers so the person guard below can
        # see that mazal_id is already filled for a place and skip cleanly.
        if is_place and (self._mazal is not None or _mode in ("modal", "postgres")):
            try:
                mazal_place_row = await self._mazal_match_place_authority(
                    text, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
                )
                if mazal_place_row and mazal_place_row.get("mazal_id"):
                    mazal_id = str(mazal_place_row["mazal_id"])
                    mazal_details = dict(mazal_place_row)
                    if "mazal" not in sources:
                        sources.append("mazal")
                    reasoning_parts.append(f"Mazal place hit ({mazal_id}).")
                    mazal_id, mazal_details, mazal_payload_extras = _apply_mazal_entity_type_gate(
                        is_place=is_place,
                        is_person_entity=_is_person_entity,
                        entity_kind=normalized_kind,
                        mazal_id=mazal_id,
                        mazal_details=mazal_details,
                        extras=mazal_payload_extras,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Mazal place matcher raised for %r: %s", text, exc)

        # If KIMA returned a mazal_nli_id (the row already links to the NLI
        # authority), and we don't have a Mazal ID from the lookup above, use it
        # as a free backfill (Rule W-29 completeness + Rule W-32 KIMA→Mazal).
        if not mazal_id and kima_payload.get("mazal_nli_id"):
            mazal_id = str(kima_payload["mazal_nli_id"])
            if "mazal" not in sources:
                sources.append("mazal")
            reasoning_parts.append(f"Mazal ID from KIMA link ({mazal_id}).")

        # Each matcher also surfaces birth/death years when the source
        # carries them. The web app used to drop these on the floor,
        # which made the MatchDetailDialog show "—" even on HIGH-
        # confidence matches AND caused the Authority Enrichment date guard to be a
        # no-op (it short-circuits when both years are None). We now
        # pull years from every source and OR them together — the
        # first source that knows the dates wins, ordered Mazal → VIAF
        # → Wikidata (most authoritative for medieval Hebrew → least).

        # — Mazal (persons only) —
        if _is_person_entity and (
            self._mazal is not None or _mode in ("modal", "postgres")
        ):
            try:
                mazal_result = await self._mazal_match_person(
                    text,
                    db_session=db_session, user_id=user_id, skip_cache=skip_cache,
                    marc_dates=marc_dates,
                    ms_year=ms_year,
                    role=role_key,
                )
                if mazal_result:
                    if mazal_result.get("homonym_candidates"):
                        mazal_payload_extras["homonym_candidates"] = (
                            mazal_result["homonym_candidates"]
                        )
                    if mazal_result.get("homonym_abstain_reason"):
                        mazal_payload_extras["homonym_abstain_reason"] = (
                            mazal_result["homonym_abstain_reason"]
                        )
                    if mazal_result.get("_abstain"):
                        mazal_homonym_abstain = True
                        mazal_payload_extras["homonym_abstain"] = True
                        reasoning_parts.append(
                            "Mazal homonym abstain — manual resolution required.",
                        )
                    elif mazal_result.get("mazal_id"):
                        mid = str(mazal_result["mazal_id"])
                        mazal_id = mid
                        mazal_details = dict(mazal_result)
                        sources.append("mazal")
                        reasoning_parts.append(f"Mazal hit ({mid}).")
                        if mazal_details.get("_fuzzy"):
                            reasoning_parts.append(
                                f"Fuzzy Mazal match (sim≈{mazal_details.get('_fuzzy_sim', 0):.2f}).",
                            )
                        dates_str = (mazal_details.get("dates") or "").strip()
                        if dates_str:
                            from converter.transformer.date_resolver import (  # noqa: PLC0415
                                resolve_person_dates,
                            )
                            parsed = resolve_person_dates(dates_str)
                            if birth_year is None and parsed.get("birth_year"):
                                birth_year = parsed["birth_year"]
                            if death_year is None and parsed.get("death_year"):
                                death_year = parsed["death_year"]
                        mazal_id, mazal_details, mazal_payload_extras = _apply_mazal_entity_type_gate(
                            is_place=is_place,
                            is_person_entity=_is_person_entity,
                            entity_kind=normalized_kind,
                            mazal_id=mazal_id,
                            mazal_details=mazal_details,
                            extras=mazal_payload_extras,
                        )
                        if (
                            mazal_id
                            and mazal_details
                            and _should_personality_rematch(role_key)
                        ):
                            main_tag = str(mazal_details.get("main_marc_tag") or "")
                            if main_tag and main_tag != "100":
                                try:
                                    rematch = await self._authority_backend.resolve_personality_mazal_id(
                                        text,
                                        dates=marc_dates,
                                        current_id=str(mazal_id),
                                        main_marc_tag=main_tag,
                                    )
                                except Exception as exc:  # noqa: BLE001
                                    logger.debug(
                                        "Personality rematch failed for %r: %s", text, exc,
                                    )
                                    rematch = None
                                if rematch and rematch.get("mazal_id"):
                                    old_id = mazal_id
                                    mazal_id = str(rematch["mazal_id"])
                                    mazal_details = dict(rematch)
                                    self._mazal_detail_cache[mazal_id] = mazal_details
                                    mazal_payload_extras["personality_rematch_from"] = (
                                        rematch.get("personality_rematch_from") or old_id
                                    )
                                    reasoning_parts.append(
                                        f"Personality rematch {old_id} → {mazal_id} (tag 100).",
                                    )
                                else:
                                    from app.pipeline.entity_normalize import (  # noqa: PLC0415
                                        normalize_entity_key,
                                    )
                                    mazal_payload_extras["suggested_personality_lookup"] = (
                                        normalize_entity_key(text)
                                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Mazal matcher raised for %r: %s", text, exc)

        mazal_personality_confirmed = bool(
            mazal_id
            and str((mazal_details or {}).get("main_marc_tag") or "") == "100"
        )

        # — VIAF (persons only) —
        # Skip SRU when Mazal אישיות is confirmed — VIAF may arrive via P214 later.
        if _is_person_entity and self._viaf is not None and not mazal_homonym_abstain:
            if not mazal_personality_confirmed:
                try:
                    viaf_meta = await self._viaf_match_with_metadata(
                        text,
                        marc_dates=marc_dates,
                        db_session=db_session,
                        user_id=user_id,
                        skip_cache=skip_cache,
                    )
                    if viaf_meta:
                        viaf_id = str(viaf_meta.get("viaf_id") or "")
                        if viaf_id:
                            sources.append("viaf")
                            reasoning_parts.append(f"VIAF hit ({viaf_id}).")
                            if birth_year is None and viaf_meta.get("birth_year"):
                                birth_year = int(viaf_meta["birth_year"])
                            if death_year is None and viaf_meta.get("death_year"):
                                death_year = int(viaf_meta["death_year"])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("VIAF matcher raised for %r: %s", text, exc)

        # — Wikidata (persons only) —
        # Wikidata person-name search must not fire on place/work entities;
        # place QIDs are already resolved by KIMA / Ashkenazi gazetteer.
        # When Mazal already matched, triangulate via P8189 before label
        # search — avoids wrong high-QID label hits (e.g. Allony, Nehemia).
        if _is_person_entity and self._wikidata is not None and not mazal_homonym_abstain:
            try:
                if mazal_id and not wikidata_qid:
                    qid_from_mazal = await self._wikidata_match_by_mazal(
                        mazal_id,
                        db_session=db_session, user_id=user_id, skip_cache=skip_cache,
                    )
                    if qid_from_mazal:
                        wikidata_qid = str(qid_from_mazal)
                        sources.append("wikidata")
                        reasoning_parts.append(
                            f"Wikidata hit via Mazal P8189 ({qid_from_mazal}).",
                        )

                if not wikidata_qid and not mazal_personality_confirmed:
                    qid = await self._wikidata_match_person(
                        text, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
                    )
                    if qid:
                        wikidata_qid = str(qid)
                        sources.append("wikidata")
                        reasoning_parts.append(f"Wikidata hit ({qid}).")

                if wikidata_qid:
                    # Backfill via SPARQL — Rule 49 §B "Wikidata date
                    # backfill". Cheap with the on-disk cache.
                    try:
                        dates = await self._wikidata_dates(
                            wikidata_qid, db_session=db_session, user_id=user_id,
                            skip_cache=skip_cache,
                        ) or {}
                        b = dates.get("birth_year")
                        d = dates.get("death_year")
                        if birth_year is None and b is not None:
                            birth_year = int(b)
                        if death_year is None and d is not None:
                            death_year = int(d)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Wikidata date backfill failed for %s: %s", wikidata_qid, exc)

                    # Extra enrichment: Hebrew label, English description,
                    # and VIAF P214 cross-reference (when VIAF matcher missed).
                    # One SPARQL call, cached 30 days.
                    try:
                        _wd_enrich = await self._wikidata_enrich_qid(
                            wikidata_qid, db_session=db_session, user_id=user_id,
                            skip_cache=skip_cache,
                        )
                        if _wd_enrich and viaf_id and _wd_enrich.get("viaf_id"):
                            from converter.authority.evidence import normalize_viaf_id  # noqa: PLC0415
                            source_viaf = normalize_viaf_id(viaf_id)
                            qid_viaf = normalize_viaf_id(_wd_enrich.get("viaf_id"))
                            if source_viaf and qid_viaf and source_viaf != qid_viaf:
                                reasoning_parts.append(
                                    f"Wikidata {wikidata_qid} rejected: VIAF mismatch "
                                    f"({source_viaf} != {qid_viaf}).",
                                )
                                wikidata_qid = ""
                        if _wd_enrich and not viaf_id:
                            wd_viaf = (_wd_enrich.get("viaf_id") or "").strip()
                            if wd_viaf:
                                viaf_id = wd_viaf
                                if "viaf" not in sources:
                                    sources.append("viaf")
                                reasoning_parts.append(
                                    f"VIAF cross-enriched from Wikidata P214 ({wd_viaf}).",
                                )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Wikidata enrich_qid failed for %s: %s", wikidata_qid, exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Wikidata matcher raised for %r: %s", text, exc)

        enrichment_meta: dict[str, str] = {}
        if not _is_person_entity:
            (
                wikidata_qid,
                viaf_id,
                _wd_enrich,
                viaf_meta,
                enrichment_meta,
            ) = await self._apply_non_person_external_enrichment(
                text=text,
                normalized_kind=normalized_kind,
                is_place=is_place,
                mazal_id=mazal_id,
                wikidata_qid=wikidata_qid,
                viaf_id=viaf_id,
                kima_hit=kima_hit,
                gazetteer_hit=gazetteer_hit,
                marc_record=marc_record,
                sources=sources,
                reasoning_parts=reasoning_parts,
                wd_enrich=_wd_enrich,
                viaf_meta=viaf_meta,
                db_session=db_session,
                user_id=user_id,
                skip_cache=skip_cache,
            )

        if mazal_homonym_abstain and not sources:
            sources.append("unresolved")

        if not sources:
            reasoning_parts.append("No authority source matched this heading.")
            confidence = "low"
            source_label = ""
        elif mazal_homonym_abstain:
            confidence = "low"
            source_label = sources[0] if sources else "unresolved"
        elif len(sources) >= 3:
            confidence = "high"
            source_label = "cross_source"
            reasoning_parts.append(
                f"Cross-sourced across all {len(sources)} authorities — strong match.",
            )
        elif len(sources) == 2:
            confidence = "high"
            source_label = "cross_source"
            reasoning_parts.append(
                f"Two-source agreement ({', '.join(sources)}) — strong match.",
            )
        else:
            # Single source — refine on signal richness instead of a flat
            # "medium". A long name with a patronymic + role compatible
            # with the manuscript date is much higher confidence than a
            # bare first name like "אליהו".
            confidence = _single_source_bucket(text, role=role)
            source_label = sources[0]
            reasoning_parts.append(
                f"Single-source ({sources[0]}) match; confidence={confidence}"
                f"{' — long name + patronymic suggests unambiguous identification' if confidence == 'high' else ''}"
                f"{' — short or generic surface form, confirm manually' if confidence == 'low' else ''}.",
            )

        if mazal_id and not mazal_personality_confirmed and viaf_id and confidence == "high":
            confidence = "medium"

        # Authority Enrichment hardening guards (date + homonym + placeholder).

        if not sources and not mazal_homonym_abstain:
            # No candidate row at all — keep returning nothing so the
            # Review UI's per-entity counter still tracks "matched vs
            # unmatched" honestly.
            return []

        # ── Authority Enrichment hardening guards (Rules 23–29) ──────────────────────
        # The seven guards mirror the desktop's
        # ``AuthorityWorker._match_marc_person_entry`` post-pass:
        # placeholder filter, short-name homonym, cluster collapse,
        # NLI-strict, Wikidata cross-check, Mazal-pair collision,
        # corporate / meeting routing. Each guard is pure; the
        # orchestrator accumulates fired flags into payload.guard_flags
        # and downgrades confidence to the lowest fired bucket.
        #
        # Cross-row guards (cluster collapse, mazal-pair collision) need
        # sibling matches in the same record. At first-pass match time
        # we don't yet know the siblings (matches stream into the DB
        # row-by-row), so we pass ``[]``. The ``/authority/rebuild``
        # endpoint re-runs the guards after every match is persisted,
        # at which point sibling context is complete.
        from app.pipeline import authority_hardening  # noqa: PLC0415

        # Carry main_marc_tag from the Mazal result so guard_mazal_subject_heading
        # can distinguish אישיות (tag 100) from נושא (tag 150) matches.
        _mazal_main_tag = (mazal_details or {}).get("main_marc_tag") if mazal_details else None
        marc_dates_overlap = False
        if marc_dates and (mazal_details or {}).get("dates"):
            from app.pipeline.homonym_scoring import parse_authority_dates  # noqa: PLC0415
            from converter.transformer.date_resolver import dates_overlap  # noqa: PLC0415

            marc_dates_overlap = dates_overlap(
                parse_authority_dates(marc_dates),
                parse_authority_dates(str(mazal_details.get("dates") or "")),
            )
        resolved_entity_kind = self._entity_kind_for_candidate(
            normalized_kind=normalized_kind,
            is_place=is_place,
            kima_hit=kima_hit,
        )

        prelim = {
            "matched_name": text,
            "entity_text": text,
            "entity_kind": resolved_entity_kind,
            "confidence": confidence,
            "mazal_id": mazal_id,
            "viaf_id": viaf_id,
            "wikidata_qid": wikidata_qid,
            "payload": {
                "guard_flags": guards,
                "viaf_uri": f"https://viaf.org/viaf/{viaf_id}" if viaf_id else "",
                "main_marc_tag": _mazal_main_tag,
                "personality_count": int(mazal_payload_extras.get("personality_count") or 0),
                "marc_dates_overlap": marc_dates_overlap,
                "marc_dates": marc_dates or "",
                "viaf_name_type": (
                    (viaf_meta or {}).get("name_type")
                    or enrichment_meta.get("viaf_name_type")
                    or None
                ),
                "viaf_resolve_op": enrichment_meta.get("viaf_resolve_op"),
                "wikidata_resolve_op": enrichment_meta.get("wikidata_resolve_op"),
                **mazal_payload_extras,
            },
        }
        editorial_meta = marc_record.get("editorial_metadata")
        if isinstance(editorial_meta, dict) and editorial_meta:
            prelim["payload"]["editorial_metadata"] = editorial_meta
        hardened = authority_hardening.apply_hardening_guards(
            prelim,
            context=authority_hardening.HardeningContext(
                siblings=[],
                preferred_name_lat=text,
                biographical_dates_in_marc=bool(marc_dates),
                entity_kind=resolved_entity_kind,
                role=role,
                ms_year=ms_year,
                birth_year=birth_year,
                death_year=death_year,
                marc_dates=marc_dates,
                enable_wikidata_crosscheck=_wikidata_crosscheck_enabled(),
            ),
        )
        confidence = str(hardened["confidence"])
        mazal_id = hardened["mazal_id"] or ""
        viaf_id = hardened["viaf_id"] or ""
        wikidata_qid = hardened["wikidata_qid"] or ""
        guards = list(hardened["payload"].get("guard_flags") or [])
        from converter.authority.stage3_guards import authority_payload_blocked  # noqa: PLC0415

        if authority_payload_blocked({"guard_flags": guards}):
            birth_year = None
            death_year = None
        hard_reasoning = (hardened["payload"].get("reasoning") or "").strip()
        if hard_reasoning:
            reasoning_parts.append(hard_reasoning)

        # If the placeholder guard cleared every id, drop the candidate
        # so the Review UI doesn't get an empty row. A coord-bearing place
        # (KIMA / Ashkenazi-gazetteer hit) is a valid match even without an
        # external id — its coordinates are the payload (Rule 60).
        has_place_coords = (
            kima_payload.get("kima_lat") is not None
            and kima_payload.get("kima_lon") is not None
        )
        if not (mazal_id or viaf_id or wikidata_qid or has_place_coords):
            if mazal_homonym_abstain or mazal_payload_extras.get("homonym_candidates"):
                pass
            else:
                return []

        # Re-derive the source label after guards may have stripped ids.
        # KIMA resolves via wikidata_qid but must stay attributed to kima,
        # not wikidata, when the SPARQL matcher did not fire.
        sources_after = []
        if mazal_id:
            sources_after.append("mazal")
        if viaf_id:
            sources_after.append("viaf")
        if kima_hit:
            sources_after.append("kima")
        # Wikidata QID from SPARQL matcher, or from the Ashkenazi gazetteer JSON
        # (coords-only gazetteer hits stay heuristic — no fifth public source).
        if wikidata_qid and (
            "wikidata" in sources or (gazetteer_hit and not kima_hit)
        ):
            sources_after.append("wikidata")
        if len(sources_after) >= 2:
            source_label = "cross_source"
        elif len(sources_after) == 1:
            source_label = sources_after[0]
        else:
            source_label = "heuristic"

        cluster_ids = {
            k: viaf_meta.get(k)
            for k in ("gnd", "lc", "isni", "bnf", "j9u")
            if viaf_meta and viaf_meta.get(k)
        }
        viaf_name_type = (viaf_meta.get("name_type") or "") if viaf_meta else ""
        preferred_name_lat = (
            (viaf_meta.get("preferred_name_lat") if viaf_meta else None)
            or (mazal_details.get("preferred_name_lat") if mazal_details else None)
            or text
        )

        biodata_payload_slice: dict[str, Any] = {}
        if _is_person_entity and (mazal_details or viaf_id):
            cluster_raw: dict[str, Any] | None = None
            if viaf_id and self._viaf is not None:
                try:
                    cluster_raw = await asyncio.to_thread(
                        self._viaf.get_cluster_biodata, viaf_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("VIAF biodata cluster fetch failed for %s: %s", viaf_id, exc)
            from converter.authority.biodata_enrich import build_biodata_payload_slice  # noqa: PLC0415

            biodata_payload_slice = build_biodata_payload_slice(
                mazal_entry=mazal_details,
                viaf_cluster_raw=cluster_raw,
            )
        elif is_place and kima_payload:
            from converter.authority.biodata_enrich import build_biodata_payload_slice  # noqa: PLC0415

            biodata_payload_slice = build_biodata_payload_slice(kima_entry=kima_payload)

        primary = Candidate(
            matched_name=text,
            confidence=confidence,
            source=source_label or "heuristic",
            mazal_id=mazal_id,
            viaf_id=viaf_id,
            wikidata_qid=wikidata_qid,
            payload={
                "sources": sources_after or sources,
                "source_count": len(sources_after) or len(sources),
                "guard_flags": guards,
                "birth_year": birth_year,
                "death_year": death_year,
                "ms_year": ms_year,
                "catalog_year": year_prov.get("catalog_year"),
                "colophon_year": year_prov.get("colophon_year"),
                "colophon_hebrew_year": year_prov.get("colophon_hebrew_year"),
                "ms_year_source": year_prov.get("ms_year_source"),
                "preferred_name_lat": preferred_name_lat,
                "preferred_name_heb": (
                    (mazal_details.get("preferred_name_heb") if mazal_details else None)
                    or (_wd_enrich.get("he_label") if _wd_enrich else None)
                ),
                # Fuzzy/proximity indicators (populated only when trigram fallback was used
                # in PostgresAuthorityBackend because exact normalized missed).
                "mazal_fuzzy": (mazal_details.get("_fuzzy") if mazal_details else None),
                "mazal_fuzzy_sim": (mazal_details.get("_fuzzy_sim") if mazal_details else None),
                "kima_fuzzy": (kima_payload.get("_fuzzy") if kima_payload else None),
                "kima_fuzzy_sim": (kima_payload.get("_fuzzy_sim") if kima_payload else None),
                # Canonical URIs for owl:sameAs in RDF + Wikidata/Wikibase import
                "viaf_uri": f"https://viaf.org/viaf/{viaf_id}" if viaf_id else None,
                "wikidata_uri": f"https://www.wikidata.org/entity/{wikidata_qid}" if wikidata_qid else None,
                # Mazal extra fields
                "mazal_aleph_id": (mazal_details.get("aleph_id") if mazal_details else None),
                "mazal_dates_raw": (mazal_details.get("dates") if mazal_details else None),
                # Wikidata enrichment (SPARQL-fetched, cached 30d)
                "wikidata_he_label": _wd_enrich.get("he_label") if _wd_enrich else None,
                "wikidata_en_description": _wd_enrich.get("en_description") if _wd_enrich else None,
                "cluster_ids": cluster_ids,
                "viaf_name_type": (
                    viaf_name_type
                    or enrichment_meta.get("viaf_name_type")
                    or None
                ),
                "viaf_resolve_op": enrichment_meta.get("viaf_resolve_op"),
                "wikidata_resolve_op": enrichment_meta.get("wikidata_resolve_op"),
                **kima_payload,
                "role_kind": _role_kind(role),
                "reasoning": " ".join(reasoning_parts),
                "ai_verdict": None,
                "matcher": "desktop",
                "main_marc_tag": _mazal_main_tag,
                **work_match_meta,
                **mazal_payload_extras,
                **biodata_payload_slice,
            },
        )
        return [primary]


# ── Loader helpers ──────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _load_mazal_matcher():  # type: ignore[no-untyped-def]
    path = os.getenv("MAZAL_DB_PATH") or _DEFAULT_MAZAL
    if not Path(path).exists():
        logger.warning("MAZAL_DB_PATH %s does not exist; Mazal matching disabled", path)
        return None
    try:
        from converter.authority.mazal_matcher import MazalMatcher  # noqa: PLC0415

        return MazalMatcher(index_path=path, track_stats=False)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not initialise MazalMatcher: %s", exc)
        return None


@lru_cache(maxsize=1)
def _load_viaf_matcher():  # type: ignore[no-untyped-def]
    try:
        from converter.authority.viaf_matcher import VIAFMatcher  # noqa: PLC0415

        return VIAFMatcher()
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not initialise VIAFMatcher: %s", exc)
        return None


@lru_cache(maxsize=1)
def _load_wikidata_matcher():  # type: ignore[no-untyped-def]
    try:
        from converter.authority.wikidata_matcher import WikidataMatcher  # noqa: PLC0415

        return WikidataMatcher()
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not initialise WikidataMatcher: %s", exc)
        return None


@lru_cache(maxsize=1)
def _load_kima_matcher():  # type: ignore[no-untyped-def]
    """KIMA places — desktop's geographic-authority adapter. Reads from
    ``data/kima/kima_index.db`` (we ship the 15 MB DB inside the web
    backend; override with KIMA_DB_PATH if you want a different copy)."""
    try:
        from pathlib import Path  # noqa: PLC0415

        from converter.authority.kima_matcher import KimaMatcher  # noqa: PLC0415

        db_path = os.getenv("KIMA_DB_PATH")
        if not db_path:
            # backend/data/kima/kima_index.db — copied at adopt-time.
            default = (
                Path(__file__).resolve().parents[2]
                / "data" / "kima" / "kima_index.db"
            )
            db_path = str(default) if default.exists() else None
        return KimaMatcher(index_path=db_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not initialise KimaMatcher: %s", exc)
        return None


def _looks_like_place(
    text: str, record: dict[str, Any], marc_field: str | None = None,
) -> bool:
    """Heuristic — was *text* a place entity in the source record?

    KIMA gets called for entities whose role is 'place'/'subject' AND
    the surface form appears in one of the place-typed slots
    (subjects[type=place], 651, 752, related_places). When in doubt
    we DO call KIMA — it's a cheap SQLite read.
    """
    field = (marc_field or "").strip()
    if field in ("651", "752", "260", "264"):
        return True
    s = text.strip()
    if not s:
        return False
    # Direct hit in any place-typed subject.
    for sub in record.get("subjects") or []:
        if isinstance(sub, dict):
            kind = sub.get("type") or sub.get("kind") or ""
            name = sub.get("name") or sub.get("term") or ""
            if kind in ("place", "geographic") and isinstance(name, str) and s in name:
                return True
    # MARC 651 / 752 / related_places / production place slots.
    prod = record.get("place")
    if isinstance(prod, str) and prod.strip() and s in prod.strip():
        return True
    for slot in ("related_places", "places"):
        for entry in record.get(slot) or []:
            if isinstance(entry, str) and s in entry:
                return True
            if isinstance(entry, dict) and s in str(entry.get("name") or entry.get("term") or ""):
                return True
    return False


def get_default_matcher() -> AuthorityMatcher:
    return DesktopMatcher()


# ── small helpers shared with the Review UI ─────────────────────────────


_PROD_ROLES = {"scribe", "transcriber", "copyist", "editor"}
_AUTHORSHIP_ROLES = {"author", "translator", "commentator"}
_PIPELINE_QID_THRESHOLD = 138_000_000


def _should_personality_rematch(role_key: str) -> bool:
    from app.pipeline.entity_normalize import MAZAL_PERSONALITY_PREFER_ROLE_KEYS  # noqa: PLC0415

    return role_key in MAZAL_PERSONALITY_PREFER_ROLE_KEYS


def _apply_mazal_entity_type_gate(
    *,
    is_place: bool,
    is_person_entity: bool,
    entity_kind: str,
    mazal_id: str,
    mazal_details: dict[str, Any] | None,
    extras: dict[str, Any],
) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
    if not mazal_id or not mazal_details:
        return mazal_id, mazal_details, extras
    et = str(mazal_details.get("entity_type") or "").lower().strip()
    kind = (entity_kind or "").lower().strip()
    out_extras = dict(extras)
    if is_place and et != "place":
        out_extras.update({
            "mazal_entity_type_mismatch": True,
            "mazal_expected_entity_type": "place",
            "mazal_got_entity_type": et or "unknown",
        })
        return "", None, out_extras
    if is_person_entity and et in ("place", "work", "corporate", "subject"):
        out_extras.update({
            "mazal_entity_type_mismatch": True,
            "mazal_expected_entity_type": "person",
            "mazal_got_entity_type": et,
        })
        return "", None, out_extras
    if kind in ("corporate", "organization", "meeting") and et not in (
        "corporate", "organization", "meeting",
    ):
        out_extras.update({
            "mazal_entity_type_mismatch": True,
            "mazal_expected_entity_type": "corporate",
            "mazal_got_entity_type": et or "unknown",
        })
        return "", None, out_extras
    if kind == "work" and et != "work":
        out_extras.update({
            "mazal_entity_type_mismatch": True,
            "mazal_expected_entity_type": "work",
            "mazal_got_entity_type": et or "unknown",
        })
        return "", None, out_extras
    return mazal_id, mazal_details, out_extras


def _wikidata_crosscheck_enabled() -> bool:
    import os  # noqa: PLC0415

    return os.getenv("MHM_DISABLE_WIKIDATA_CROSSCHECK", "0").strip().lower() not in (
        "1",
        "true",
        "yes",
    )


def _manuscript_year_provenance(record: dict[str, Any]) -> dict[str, Any]:
    from app.pipeline.marc_date_sources import manuscript_year_provenance  # noqa: PLC0415

    return manuscript_year_provenance(record)


def _record_year(record: dict[str, Any]) -> int | None:
    from app.pipeline.marc_date_sources import manuscript_production_year  # noqa: PLC0415

    canonical = manuscript_production_year(record)
    if canonical is not None:
        return canonical

    dates = record.get("dates")
    if isinstance(dates, dict):
        for k in ("year", "production", "publication"):
            v = dates.get(k)
            if isinstance(v, int):
                return v
            if isinstance(v, str):
                try:
                    return int(v[:4])
                except ValueError:
                    pass
    # Legacy scalar keys on records ingested before canonical date wiring.
    for key in ("date", "year", "production_year"):
        v = record.get(key)
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            # Strip surrounding quotes from stored strings like '"1612"'
            v = v.strip("\"' ")
            try:
                return int(v[:4])
            except ValueError:
                pass
    return None


def _role_kind(role: str) -> str:
    # Map raw MARC $e (Hebrew or English) to our canonical English role labels
    # so the date-guard classification below can treat "סופר" as "scribe" etc.
    try:
        from converter.config.vocabularies import ROLE_MAPPINGS  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        ROLE_MAPPINGS = {}
    raw = (role or "").strip().rstrip(".").lower()
    mapped = ROLE_MAPPINGS.get(raw, raw)
    r = mapped.lower()
    if r in _PROD_ROLES:
        return "production"
    if r in _AUTHORSHIP_ROLES:
        return "authorship"
    if r == "subject":
        return "subject"
    return "other"


def _single_source_bucket(text: str, *, role: str) -> str:
    """Tier a single-source match without cross-source agreement.

    Signals (Hebrew-name aware — desktop's curators care about these):
    * length              — longer surface form has more identifying power
    * patronymic ("בן ")  — "X בן Y" is far less ambiguous than "X"
    * inverted form ", "  — "Surname, Given" is a catalog heading
                            (curator already vetted)
    * role known          — a known role narrows the candidate space
    """
    n = len(text.strip())
    has_patronymic = " בן " in text or " בת " in text or " ben " in text.lower()
    has_inverted = "," in text
    has_role = bool((role or "").strip())

    score = 0
    if n >= 20:           score += 2
    elif n >= 12:         score += 1
    if has_patronymic:    score += 2
    if has_inverted:      score += 1
    if has_role:          score += 1
    if n < 6:             score -= 2

    if score >= 4: return "high"
    if score >= 2: return "medium"
    return "low"
