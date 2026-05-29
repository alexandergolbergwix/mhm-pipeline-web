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

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi.concurrency import run_in_threadpool

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

    async def match(
        self, entity: dict[str, str], marc_record: dict[str, Any],
    ) -> list[Candidate]:
        text = (entity.get("text") or "").strip()
        if not text:
            return []
        role = entity.get("role", "")

        # Run all three lookups on a worker thread so the sync sqlite +
        # requests calls don't block uvicorn.
        return await run_in_threadpool(
            self._match_sync, text=text, role=role, marc_record=marc_record,
        )

    def _match_sync(
        self, *, text: str, role: str, marc_record: dict[str, Any],
    ) -> list[Candidate]:
        sources: list[str] = []
        mazal_id = ""
        viaf_id = ""
        wikidata_qid = ""
        birth_year: int | None = None
        death_year: int | None = None
        guards: list[str] = []
        reasoning_parts: list[str] = []

        # — Mazal —
        if self._mazal is not None:
            try:
                mid = self._mazal.match_person(text)
                if mid:
                    mazal_id = str(mid)
                    sources.append("mazal")
                    reasoning_parts.append(f"Mazal hit ({mid}).")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Mazal matcher raised for %r: %s", text, exc)

        # — VIAF —
        if self._viaf is not None:
            try:
                vid = self._viaf.match_person(text)
                if vid:
                    viaf_id = str(vid)
                    sources.append("viaf")
                    reasoning_parts.append(f"VIAF hit ({vid}).")
            except Exception as exc:  # noqa: BLE001
                logger.warning("VIAF matcher raised for %r: %s", text, exc)

        # — Wikidata —
        if self._wikidata is not None:
            try:
                qid = self._wikidata.match_person(text)
                if qid:
                    wikidata_qid = str(qid)
                    sources.append("wikidata")
                    reasoning_parts.append(f"Wikidata hit ({qid}).")
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

        # Stage 3 date guard via desktop's stage3_guards.
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
                        f"⚠ Stage 3 date guard fired: {getattr(verdict, 'reason', '')}",
                    )
        except Exception:  # noqa: BLE001 — desktop guards evolve; never let one kill ingest
            logger.debug("stage3_guards unavailable for this candidate", exc_info=True)

        if not sources:
            # No candidate row at all — keep returning nothing so the
            # Review UI's per-entity counter still tracks "matched vs
            # unmatched" honestly.
            return []

        primary = Candidate(
            matched_name=text,
            confidence=confidence,
            source=source_label or "heuristic",
            mazal_id=mazal_id,
            viaf_id=viaf_id,
            wikidata_qid=wikidata_qid,
            payload={
                "sources": sources,
                "source_count": len(sources),
                "guard_flags": guards,
                "birth_year": birth_year,
                "death_year": death_year,
                "ms_year": ms_year,
                "preferred_name_lat": text,        # desktop's real romanisation
                                                    # lives in hebrew_translit and is
                                                    # called by item_builder, not here
                "cluster_ids": {},
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
    for key in ("year", "production_year"):
        v = record.get(key)
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            try:
                return int(v[:4])
            except ValueError:
                pass
    return None


def _role_kind(role: str) -> str:
    r = role.lower()
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
