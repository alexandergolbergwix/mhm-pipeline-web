"""Batch English-label lookup against live Wikidata.

The Studio serialiser already enriches every statement / qualifier /
reference with the labels desktop's static dictionary
(``converter.wikidata.property_labels``) knows about. Everything else —
person Q-IDs from VIAF, place Q-IDs from KIMA, content QIDs the
property mapping doesn't carry — needs a live ``wbgetentities`` round-
trip. The frontend lazy-fetches them in batches via this endpoint and
patches the labels onto its in-memory items.

In-process dict cache so the second hit on Q5 (human) is free within one
dyno's lifetime; the Redis/Postgres ``inference_cache`` tier (kind
``wikidata.label``) backs it up so a label is fetched from live
Wikidata at most once across every dyno and every restart — labels are
about as immutable as external data gets.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.pipeline.inference_cache import read_from_inference_cache, write_to_inference_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/wikidata", tags=["wikidata-labels"])

_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_MAX_PER_REQUEST = 50  # wbgetentities cap
_CACHE_KIND = "wikidata.label"

# Process-wide cache: (qid_or_pid, lang) → label.
_LABEL_CACHE: dict[tuple[str, str], str] = {}


def _label_query_summary(qid_or_pid: str, lang: str) -> dict[str, Any]:
    return {"endpoint": _WIKIDATA_API, "id": qid_or_pid, "lang": lang}


async def resolve_labels(
    db: Any,
    ids: list[str],
    *,
    lang: str = "en",
) -> dict[str, str]:
    """``{id: label}`` for every id live Wikidata knows, through three cache tiers.

    Shared with the verify path (Rule W-80): the judge and the curator must see the
    same gloss, and a QID reconciled at runtime — a KIMA place, a matched person —
    has no static table to read one from. Three tiers, cheapest first: the
    process dict, the Redis/Postgres ``inference_cache`` (90-day TTL; labels are
    about as immutable as external data gets), then one batched ``wbgetentities``.

    Never raises: an unresolvable id is simply absent from the result, and the
    caller falls back to the bare QID rather than failing a build or a verify run.

    ``db`` may be a session (a request handler already has one) or a session
    FACTORY. Pass the factory off the verify path: the cache tiers then get their
    own short transactions and the ``wbgetentities`` call runs with none open
    (Rule W-40).
    """
    raw = [str(i).strip() for i in ids if str(i).strip()]
    out: dict[str, str] = {}
    process_misses: list[str] = []
    for i in raw:
        cached = _LABEL_CACHE.get((i, lang))
        if cached is not None:
            out[i] = cached
        else:
            process_misses.append(i)

    is_factory = not isinstance(db, AsyncSession)

    @contextlib.asynccontextmanager
    async def _session():
        if is_factory:
            async with db() as session:
                yield session
        else:
            yield db

    still_missing: list[str] = []
    try:
        async with _session() as session:
            for i in dict.fromkeys(process_misses):
                hit = await read_from_inference_cache(
                    session, kind=_CACHE_KIND,
                    query_summary=_label_query_summary(i, lang),
                )
                label = hit.get("label") if isinstance(hit, dict) else None
                if label:
                    out[i] = label
                    _LABEL_CACHE[(i, lang)] = label
                else:
                    still_missing.append(i)
    except Exception as exc:  # noqa: BLE001 — a gloss must never break a build
        logger.warning("Wikidata label cache read failed: %s", exc)
        still_missing = list(dict.fromkeys(process_misses))

    # The HTTP runs with no transaction open when a factory was supplied.
    fresh: dict[str, str] = {}
    for chunk_start in range(0, len(still_missing), _MAX_PER_REQUEST):
        chunk = still_missing[chunk_start : chunk_start + _MAX_PER_REQUEST]
        for k, v in (await _fetch(chunk, lang)).items():
            fresh[k] = v
            out[k] = v
            _LABEL_CACHE[(k, lang)] = v

    if fresh:
        try:
            async with _session() as session:
                for k, v in fresh.items():
                    await write_to_inference_cache(
                        session, kind=_CACHE_KIND,
                        query_summary=_label_query_summary(k, lang),
                        result={"label": v},
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Wikidata label cache write failed: %s", exc)
    return out


@router.get("/labels")
async def get_labels(
    ids: str = Query(..., description="Comma-separated list of Q / P ids."),
    lang: str = Query("en", pattern=r"^[a-z-]{2,7}$"),
    auth: AuthContext = Depends(current_auth),  # noqa: ARG001 — gate
    db: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Return ``{id: label}`` for every id in *ids* the live Wikidata
    API knows about. Unknown / deleted ids simply don't appear in the
    response."""
    return await resolve_labels(db, [s for s in ids.split(",")], lang=lang)


async def _fetch(ids: list[str], lang: str) -> dict[str, str]:
    if not ids:
        return {}
    params: dict[str, Any] = {
        "action": "wbgetentities",
        "format": "json",
        "ids": "|".join(ids),
        "props": "labels",
        "languages": lang,
        "languagefallback": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                _WIKIDATA_API, params=params,
                headers={"User-Agent": "mhm-pipeline-web/1.0 (research)"},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wikidata label batch failed (%s): %s", ids[:3], exc)
        return {}

    out: dict[str, str] = {}
    for qid, ent in (data.get("entities") or {}).items():
        labels = (ent or {}).get("labels") or {}
        label_obj = labels.get(lang) or next(iter(labels.values()), None)
        if isinstance(label_obj, dict) and label_obj.get("value"):
            out[qid] = label_obj["value"]
    return out
