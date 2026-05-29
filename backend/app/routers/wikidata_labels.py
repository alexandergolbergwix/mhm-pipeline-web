"""Batch English-label lookup against live Wikidata.

The Studio serialiser already enriches every statement / qualifier /
reference with the labels desktop's static dictionary
(``converter.wikidata.property_labels``) knows about. Everything else —
person Q-IDs from VIAF, place Q-IDs from KIMA, content QIDs the
property mapping doesn't carry — needs a live ``wbgetentities`` round-
trip. The frontend lazy-fetches them in batches via this endpoint and
patches the labels onto its in-memory items.

In-process dict cache so the second hit on Q5 (human) is free.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query

from app.auth.session import AuthContext, current_auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/wikidata", tags=["wikidata-labels"])

_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_MAX_PER_REQUEST = 50  # wbgetentities cap

# Process-wide cache: (qid_or_pid, lang) → label.
_LABEL_CACHE: dict[tuple[str, str], str] = {}


@router.get("/labels")
async def get_labels(
    ids: str = Query(..., description="Comma-separated list of Q / P ids."),
    lang: str = Query("en", pattern=r"^[a-z-]{2,7}$"),
    auth: AuthContext = Depends(current_auth),  # noqa: ARG001 — gate
) -> dict[str, str]:
    """Return ``{id: label}`` for every id in *ids* the live Wikidata
    API knows about. Unknown / deleted ids simply don't appear in the
    response."""
    raw = [s.strip() for s in ids.split(",") if s.strip()]
    out: dict[str, str] = {}
    misses: list[str] = []
    for i in raw:
        cached = _LABEL_CACHE.get((i, lang))
        if cached is not None:
            out[i] = cached
        else:
            misses.append(i)

    for chunk_start in range(0, len(misses), _MAX_PER_REQUEST):
        chunk = misses[chunk_start : chunk_start + _MAX_PER_REQUEST]
        labels = await _fetch(chunk, lang)
        for k, v in labels.items():
            out[k] = v
            _LABEL_CACHE[(k, lang)] = v
    return out


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
