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


def _slim_entity(entity: dict[str, Any]) -> dict[str, Any]:
    from app.services.research_agent.sanitize import quarantine_text

    labels = entity.get("labels") or {}
    descriptions = entity.get("descriptions") or {}
    aliases = entity.get("aliases") or {}
    claims = entity.get("claims") or {}
    return {
        "id": entity.get("id"),
        "labels": {
            lang: quarantine_text(val.get("value"), max_chars=240)["value"]
            for lang, val in labels.items()
        },
        "descriptions": {
            lang: quarantine_text(val.get("value"), max_chars=480)
            for lang, val in descriptions.items()
        },
        "aliases": {
            lang: [quarantine_text(a.get("value"), max_chars=120)["value"] for a in items if a.get("value")]
            for lang, items in aliases.items()
        },
        "claim_properties": sorted(claims.keys())[:80],
        "claim_count": len(claims),
        "sitelinks": sorted((entity.get("sitelinks") or {}).keys())[:40],
    }


async def wikidata_entity(qid: str, bot_token: str | None) -> dict[str, Any]:
    return await fetch_wikibase_entity(entity_id=qid, api_url=_WIKIDATA_API, bot_token=bot_token)


async def project_wikibase_entity(qid: str, bot_token: str | None) -> dict[str, Any]:
    api_url = _wikibase_api_url()
    if not api_url:
        raise RuntimeError("Project Wikibase URL is not configured.")
    return await fetch_wikibase_entity(entity_id=qid, api_url=api_url, bot_token=bot_token)


def wikibase_item_url(qid: str) -> str:
    settings = get_settings()
    base = (settings.wikibase_cloud_base_url or "").rstrip("/")
    return urljoin(base + "/", f"wiki/Item:{qid}") if base else qid
