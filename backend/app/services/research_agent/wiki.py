"""Authenticated Wikidata / Wikibase reads using the grant's ephemeral token.

Public SPARQL stays on the existing proxy. This module only talks to
MediaWiki Action API endpoints and never logs the token.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import httpx

from app.settings import get_settings

logger = logging.getLogger(__name__)

_USER_AGENT = "MHM-Pipeline-Web/1.0 (research-agent; contact via project admin)"
_TIMEOUT_S = 20.0
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"


def _wikibase_api_url() -> str:
    settings = get_settings()
    base = (settings.wikibase_cloud_base_url or "").rstrip("/")
    if not base:
        return ""
    return f"{base}/w/api.php"


async def _login(client: httpx.AsyncClient, api_url: str, bot_token: str) -> None:
    """MediaWiki bot-password login. ``bot_token`` is ``Username@BotName:password``."""
    if "@" not in bot_token or ":" not in bot_token:
        raise ValueError("Wikidata bot password must be Username@BotName:password.")
    lgname, lgpassword = bot_token.split(":", 1)
    token_resp = await client.get(
        api_url,
        params={"action": "query", "meta": "tokens", "type": "login", "format": "json"},
    )
    token_resp.raise_for_status()
    login_token = token_resp.json().get("query", {}).get("tokens", {}).get("logintoken")
    if not login_token:
        raise RuntimeError("Wiki login token missing.")
    login_resp = await client.post(
        api_url,
        data={
            "action": "login",
            "lgname": lgname,
            "lgpassword": lgpassword,
            "lgtoken": login_token,
            "format": "json",
        },
    )
    login_resp.raise_for_status()
    result = login_resp.json().get("login", {}).get("result")
    if result != "Success":
        raise RuntimeError(f"Wiki login failed ({result}).")


async def fetch_wikibase_entity(
    *,
    entity_id: str,
    api_url: str,
    bot_token: str | None,
) -> dict[str, Any]:
    """GET wbgetentities. Logs in when a bot token is present."""
    headers = {"User-Agent": _USER_AGENT}
    params = {
        "action": "wbgetentities",
        "ids": entity_id,
        "format": "json",
        "props": "labels|descriptions|aliases|claims|sitelinks",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT_S, headers=headers, follow_redirects=True) as client:
        if bot_token:
            await _login(client, api_url, bot_token)
        resp = await client.get(api_url, params=params)
        resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    entity = (data.get("entities") or {}).get(entity_id)
    if not entity or entity.get("missing") is not None:
        raise RuntimeError(f"Entity {entity_id} was not found.")
    return _slim_entity(entity)


def _claim_value(snak: dict[str, Any]) -> str | None:
    """Human-readable datavalue for one claim: QID, string, date, or amount."""
    datatype = snak.get("datatype")
    value = (snak.get("datavalue") or {}).get("value")
    if value is None:
        return None
    if datatype == "wikibase-item" and isinstance(value, dict):
        return str(value.get("id")) if value.get("id") else None
    if datatype == "time" and isinstance(value, dict):
        return str(value.get("time") or "")[:10] or None
    if datatype == "quantity" and isinstance(value, dict):
        return str(value.get("amount") or "") or None
    if isinstance(value, dict):  # monolingualtext
        return str(value.get("text") or "") or None
    return str(value) if str(value).strip() else None


def _slim_entity(entity: dict[str, Any]) -> dict[str, Any]:
    from app.services.research_agent.sanitize import quarantine_text

    def _clean(text: str, limit: int) -> str:
        return quarantine_text(text, max_chars=limit)["value"]

    labels = entity.get("labels") or {}
    descriptions = entity.get("descriptions") or {}
    aliases = entity.get("aliases") or {}
    claims = entity.get("claims") or {}
    claim_values: dict[str, list[str]] = {}
    for prop, statements in claims.items():
        values: list[str] = []
        for statement in statements[:4]:
            snak = statement.get("mainsnak") or {}
            if snak.get("snaktype") != "value":
                continue
            raw = _claim_value(snak)
            if raw:
                values.append(_clean(raw, 160))
        if values:
            claim_values[prop] = values
    return {
        "id": entity.get("id"),
        "labels": {
            lang: _clean(val.get("value"), 240)
            for lang, val in labels.items()
        },
        "descriptions": {
            lang: _clean(val.get("value"), 480)
            for lang, val in descriptions.items()
        },
        "aliases": {
            lang: [_clean(a.get("value"), 120) for a in items if a.get("value")]
            for lang, items in aliases.items()
        },
        "claim_properties": sorted(claims.keys())[:80],
        "claim_count": len(claims),
        "claim_values": claim_values,
        "sitelinks": sorted((entity.get("sitelinks") or {}).keys())[:40],
    }


async def wikidata_entity(qid: str, bot_token: str | None) -> dict[str, Any]:
    return await fetch_wikibase_entity(entity_id=qid, api_url=_WIKIDATA_API, bot_token=bot_token)


async def fetch_wikidata_entities_batch(
    qids: list[str], *, bot_token: str | None,
) -> dict[str, dict[str, Any]]:
    """GET wbgetentities for up to 50 QIDs per call; returns id → slim entity.

    Public data — no login required; the bot token is only used when given.
    A broken or malformed token degrades to an anonymous read instead of
    failing the fetch (reads never need credentials on www.wikidata.org).
    Missing entities are reported under their id with ``missing: True``.
    """
    if not qids:
        return {}
    out: dict[str, dict[str, Any]] = {}
    headers = {"User-Agent": _USER_AGENT}
    async with httpx.AsyncClient(timeout=_TIMEOUT_S, headers=headers, follow_redirects=True) as client:
        if bot_token:
            try:
                await _login(client, _WIKIDATA_API, bot_token)
            except (ValueError, RuntimeError) as exc:
                logger.warning("Wikidata batch fetch login failed; reading anonymously: %s", exc)
        for start in range(0, len(qids), 50):
            batch = qids[start:start + 50]
            resp = await client.get(_WIKIDATA_API, params={
                "action": "wbgetentities",
                "ids": "|".join(batch),
                "format": "json",
                "props": "labels|descriptions|claims",
            })
            resp.raise_for_status()
            data = resp.json()
            if data.get("error"):
                raise RuntimeError(str(data["error"]))
            for qid, entity in (data.get("entities") or {}).items():
                if entity.get("missing") is not None:
                    out[qid] = {"id": qid, "missing": True}
                else:
                    out[qid] = _slim_entity(entity)
    return out


async def project_wikibase_entity(qid: str, bot_token: str | None) -> dict[str, Any]:
    api_url = _wikibase_api_url()
    if not api_url:
        raise RuntimeError("Project Wikibase URL is not configured.")
    return await fetch_wikibase_entity(entity_id=qid, api_url=api_url, bot_token=bot_token)


def wikibase_item_url(qid: str) -> str:
    settings = get_settings()
    base = (settings.wikibase_cloud_base_url or "").rstrip("/")
    return urljoin(base + "/", f"wiki/Item:{qid}") if base else qid
