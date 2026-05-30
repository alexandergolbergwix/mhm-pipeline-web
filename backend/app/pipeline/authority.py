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
        self._kima = _load_kima_matcher() if os.getenv("DISABLE_KIMA") != "1" else None

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

        # — KIMA (places only) —
        # KIMA is the desktop's geographic-authority adapter. It returns
        # a Wikidata URI for matched places, so we hand the QID straight
        # to the same wikidata_qid slot the persons path uses (with
        # source=kima).
        is_place = role in ("place", "subject") and _looks_like_place(text, marc_record)
        if is_place and self._kima is not None:
            try:
                uri = self._kima.match_place(text)
                if uri:
                    sources.append("kima")
                    # Pull the QID off the URI for the wikidata column.
                    qid = uri.rsplit("/", 1)[-1]
                    if qid.startswith("Q"):
                        wikidata_qid = qid
                    reasoning_parts.append(f"KIMA hit ({uri}).")
            except Exception as exc:  # noqa: BLE001
                logger.warning("KIMA matcher raised for %r: %s", text, exc)

        # Each matcher also surfaces birth/death years when the source
        # carries them. The web app used to drop these on the floor,
        # which made the MatchDetailDialog show "—" even on HIGH-
        # confidence matches AND caused the Stage 3 date guard to be a
        # no-op (it short-circuits when both years are None). We now
        # pull years from every source and OR them together — the
        # first source that knows the dates wins, ordered Mazal → VIAF
        # → Wikidata (most authoritative for medieval Hebrew → least).

        # — Mazal —
        if self._mazal is not None:
            try:
                mid = self._mazal.match_person(text)
                if mid:
                    mazal_id = str(mid)
                    sources.append("mazal")
                    reasoning_parts.append(f"Mazal hit ({mid}).")
                    # Pull the free-text "dates" column off the Mazal
                    # authority row and resolve it (handles Hebrew
                    # century, "נפטר 1628", "1542-1620", …).
                    try:
                        details = self._mazal.get_person_details(mid) or {}
                        dates_str = (details.get("dates") or "").strip()
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

        # — VIAF —
        if self._viaf is not None:
            try:
                # match_person_with_metadata wraps match_person + the
                # cluster fetch in one call, so we get the years (and
                # the GND/LCCN/ISNI/BnF cluster IDs the desktop pipeline
                # threads into person Wikidata items) without a second
                # round-trip per candidate.
                meta = self._viaf.match_person_with_metadata(text)
                if meta:
                    viaf_id = str(meta.get("viaf_id") or "")
                    if viaf_id:
                        sources.append("viaf")
                        reasoning_parts.append(f"VIAF hit ({viaf_id}).")
                        if birth_year is None and meta.get("birth_year"):
                            birth_year = int(meta["birth_year"])
                        if death_year is None and meta.get("death_year"):
                            death_year = int(meta["death_year"])
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
                    # Backfill via SPARQL — Rule 49 §B "Wikidata date
                    # backfill". Cheap with the on-disk cache.
                    try:
                        b, d = self._wikidata.find_dates_by_qid(wikidata_qid)
                        if birth_year is None and b is not None:
                            birth_year = int(b)
                        if death_year is None and d is not None:
                            death_year = int(d)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Wikidata date backfill failed for %s: %s", wikidata_qid, exc)
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

        # ── Stage 3 hardening guards (Rules 23–29) ──────────────────────
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

        prelim = {
            "matched_name": text,
            "entity_text": text,
            "entity_kind": "person" if role != "place" else "place",
            "confidence": confidence,
            "mazal_id": mazal_id,
            "viaf_id": viaf_id,
            "wikidata_qid": wikidata_qid,
            "payload": {
                "guard_flags": guards,
                "viaf_uri": f"https://viaf.org/viaf/{viaf_id}" if viaf_id else "",
            },
        }
        hardened = authority_hardening.apply_hardening_guards(
            prelim,
            context=authority_hardening.HardeningContext(
                siblings=[],
                preferred_name_lat=text,
                biographical_dates_in_marc=bool(birth_year or death_year),
                entity_kind=prelim["entity_kind"],
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
        # so the Review UI doesn't get an empty row.
        if not (mazal_id or viaf_id or wikidata_qid):
            return []

        # Re-derive the source label after guards may have stripped ids.
        sources_after = [
            s for s, val in (
                ("mazal", mazal_id), ("viaf", viaf_id), ("wikidata", wikidata_qid),
            ) if val
        ]
        if len(sources_after) >= 2:
            source_label = "cross_source"
        elif len(sources_after) == 1:
            source_label = sources_after[0]
        else:
            source_label = "heuristic"

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
