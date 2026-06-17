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
        return await self._match_one(
            text=text, role=role, entity_kind=entity_kind, marc_record=marc_record,
            db_session=db_session, user_id=user_id, skip_cache=skip_cache,
            marc_dates=marc_dates,
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
        self, text: str, *, db_session: Any, user_id: Any, skip_cache: bool,
        marc_dates: str | None = None,
    ) -> str | None:
        _mode = os.getenv("AUTHORITY_MODE", "local").lower()
        # Guard: skip when no Mazal source is available at all.
        if self._mazal is None and _mode not in ("modal", "postgres"):
            return None

        async def _f() -> str | None:
            result = await self._authority_backend.match_person(text, dates=marc_dates)
            if result is None:
                return None
            mid = result.get("mazal_id")
            if mid:
                # Side-load details so _mazal_get_details avoids a second call.
                self._mazal_detail_cache[str(mid)] = result
            return str(mid) if mid else None

        # Include dates in the cache key so different date inputs resolve separately.
        query_summary: dict[str, Any] = {"op": "match_person", "text": text}
        if marc_dates:
            query_summary["dates"] = marc_dates
        return await self._cached(
            kind="authority.mazal",
            query_summary=query_summary,
            fetch=_f, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
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
        self, title: str, *, db_session: Any, user_id: Any, skip_cache: bool,
    ) -> str | None:
        """Match a work title against the Mazal authority index.

        Calls the underlying SQLite/Postgres `lookup_work` path.
        Returns an NLI ID string, or None on no hit.
        """
        _mode = os.getenv("AUTHORITY_MODE", "local").lower()
        if self._mazal is None and _mode not in ("postgres",):
            return None

        async def _f() -> str | None:
            if self._mazal is None:
                return None
            nli_id = await asyncio.to_thread(self._mazal.match_work, title)
            return str(nli_id) if nli_id else None

        return await self._cached(
            kind="authority.mazal",
            query_summary={"op": "match_work", "text": title},
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
        self, text: str, *, db_session: Any, user_id: Any, skip_cache: bool,
    ) -> dict[str, Any] | None:
        if self._viaf is None:
            return None
        async def _f() -> dict[str, Any] | None:
            return await asyncio.to_thread(self._viaf.match_person_with_metadata, text)
        result = await self._cached(
            kind="authority.viaf",
            query_summary={"op": "match_person_with_metadata", "text": text},
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
                query_summary={"op": "match_person_with_metadata", "text": text},
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

    async def _match_one(
        self, *,
        text: str, role: str, entity_kind: str, marc_record: dict[str, Any],
        db_session: Any, user_id: Any, skip_cache: bool,
        marc_dates: str | None = None,
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

        # — KIMA (places only) —
        # KIMA is the desktop's geographic-authority adapter. It returns
        # a Wikidata URI for matched places, so we hand the QID straight
        # to the same wikidata_qid slot the persons path uses (with
        # source=kima).
        _mode = os.getenv("AUTHORITY_MODE", "local").lower()
        normalized_kind = entity_kind.lower().strip()
        normalized_role = role.lower().strip()
        is_place = (
            normalized_kind in ("place", "location", "geographic")
            or normalized_role in ("place", "location", "geographic", "production_place")
            # Provenance-event places (541 $b acquisition, 583 $j conservation/
            # exhibition, and future ownership_place) must also fire KIMA.
            or normalized_role.endswith("_place")
            or (normalized_role == "subject" and _looks_like_place(text, marc_record))
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
                sources.append("ashkenazi")
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
        # Match work titles extracted from notes / contents / colophon against
        # the Mazal authority index (tag 130/430 "work" entity_type).
        if normalized_kind == "work" and (self._mazal is not None or _mode == "postgres"):
            try:
                work_mid = await self._mazal_match_work(
                    text, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
                )
                if work_mid:
                    mazal_id = str(work_mid)
                    sources.append("mazal")
                    reasoning_parts.append(f"Mazal work hit ({work_mid}).")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Mazal work matcher raised for %r: %s", text, exc)

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
                    if "mazal" not in sources:
                        sources.append("mazal")
                    reasoning_parts.append(f"Mazal place hit ({mazal_id}).")
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
        # Guard: skip Mazal person matcher for place/work entities to prevent a
        # place name or work title that coincidentally matches a person heading
        # from receiving a wrong NLI person ID.
        if not is_place and normalized_kind != "work" and (
            self._mazal is not None or _mode in ("modal", "postgres")
        ):
            try:
                mid = await self._mazal_match_person(
                    text,
                    db_session=db_session, user_id=user_id, skip_cache=skip_cache,
                    marc_dates=marc_dates,
                )
                if mid:
                    mazal_id = str(mid)
                    sources.append("mazal")
                    reasoning_parts.append(f"Mazal hit ({mid}).")
                    # Pull the free-text "dates" column off the Mazal
                    # authority row and resolve it (handles Hebrew
                    # century, "נפטר 1628", "1542-1620", …).
                    try:
                        mazal_details = await self._mazal_get_details(
                            mid, db_session=db_session, user_id=user_id,
                            skip_cache=skip_cache,
                        ) or {}
                        if mazal_details.get("_fuzzy"):
                            reasoning_parts.append(
                                f"Fuzzy Mazal match (sim≈{mazal_details.get('_fuzzy_sim', 0):.2f})."
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
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Mazal date lookup failed for %s: %s", mid, exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Mazal matcher raised for %r: %s", text, exc)

        # — VIAF (persons only) —
        # VIAF SRU's personal-name index should not be queried for places or works.
        if not is_place and normalized_kind != "work" and self._viaf is not None:
            try:
                # match_person_with_metadata wraps match_person + the
                # cluster fetch in one call, so we get the years (and
                # the GND/LCCN/ISNI/BnF cluster IDs the desktop pipeline
                # threads into person Wikidata items) without a second
                # round-trip per candidate.
                viaf_meta = await self._viaf_match_with_metadata(
                    text, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
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
        if not is_place and self._wikidata is not None:
            try:
                qid = await self._wikidata_match_person(
                    text, db_session=db_session, user_id=user_id, skip_cache=skip_cache,
                )
                if qid:
                    wikidata_qid = str(qid)
                    sources.append("wikidata")
                    reasoning_parts.append(f"Wikidata hit ({qid}).")
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
                        if _wd_enrich and not viaf_id:
                            wd_viaf = (_wd_enrich.get("viaf_id") or "").strip()
                            if wd_viaf:
                                viaf_id = wd_viaf
                                if "viaf" not in sources:
                                    sources.append("viaf")
                                reasoning_parts.append(
                                    f"VIAF cross-enriched from Wikidata P214 ({wd_viaf})."
                                )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Wikidata enrich_qid failed for %s: %s", wikidata_qid, exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Wikidata matcher raised for %r: %s", text, exc)

        if not sources:
            reasoning_parts.append("No authority source matched this heading.")
            confidence = "low"
            source_label = ""
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

        # Authority Enrichment date guard via desktop's stage3_guards.
        ms_year = _record_year(marc_record)
        try:
            from converter.authority import stage3_guards  # noqa: PLC0415

            if ms_year is not None and (birth_year or death_year):
                verdict = stage3_guards.evaluate_date_conflict(
                    role=role,
                    candidate_birth=birth_year,
                    candidate_death=death_year,
                    ms_year=ms_year,
                )
                if getattr(verdict, "fired", False):
                    guards.append("date_conflict")
                    if confidence == "high":
                        confidence = "medium"
                    if "date_conflict" in guards:
                        confidence = "low"
                    reasoning_parts.append(
                        f"⚠ Authority Enrichment date guard fired: {getattr(verdict, 'reason', '')}",
                    )
        except Exception:  # noqa: BLE001 — desktop guards evolve; never let one kill ingest
            logger.debug("stage3_guards unavailable for this candidate", exc_info=True)

        if not sources:
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

        prelim = {
            "matched_name": text,
            "entity_text": text,
            "entity_kind": "place" if kima_hit or is_place else "person",
            "confidence": confidence,
            "mazal_id": mazal_id,
            "viaf_id": viaf_id,
            "wikidata_qid": wikidata_qid,
            "payload": {
                "guard_flags": guards,
                "viaf_uri": f"https://viaf.org/viaf/{viaf_id}" if viaf_id else "",
                "main_marc_tag": _mazal_main_tag,
            },
        }
        hardened = authority_hardening.apply_hardening_guards(
            prelim,
            context=authority_hardening.HardeningContext(
                siblings=[],
                preferred_name_lat=text,
                biographical_dates_in_marc=bool(birth_year or death_year),
                entity_kind=prelim["entity_kind"],
                role=role,
                enable_wikidata_crosscheck=False,
            ),
        )
        confidence = str(hardened["confidence"])
        mazal_id = hardened["mazal_id"] or ""
        viaf_id = hardened["viaf_id"] or ""
        wikidata_qid = hardened["wikidata_qid"] or ""
        guards = list(hardened["payload"].get("guard_flags") or [])
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
        if gazetteer_hit:
            sources_after.append("ashkenazi")
        if wikidata_qid and "wikidata" in sources:
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
                "viaf_name_type": viaf_name_type or None,
                **kima_payload,
                "role_kind": _role_kind(role),
                "reasoning": " ".join(reasoning_parts),
                "ai_verdict": None,
                "matcher": "desktop",
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


def _looks_like_place(text: str, record: dict[str, Any]) -> bool:
    """Heuristic — was *text* a place entity in the source record?

    KIMA gets called for entities whose role is 'place'/'subject' AND
    the surface form appears in one of the place-typed slots
    (subjects[type=place], 651, 752, related_places). When in doubt
    we DO call KIMA — it's a cheap SQLite read.
    """
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
    # MARC 651 / 752 / related_places slots.
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


def _record_year(record: dict[str, Any]) -> int | None:
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
    # "date" (singular) is the key the NLI MARC ingest populates from the 008 field.
    # "year" and "production_year" are alternate names from other ingest paths.
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
