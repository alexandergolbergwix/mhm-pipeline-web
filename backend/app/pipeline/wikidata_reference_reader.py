"""Read exact Wikidata item snapshots without an edit session."""

import asyncio
import re
from collections.abc import Callable

import httpx

from app.pipeline.wikidata_mirror import validate_entity

_ORIGIN = "https://www.wikidata.org"
_API = f"{_ORIGIN}/w/api.php"
_ENTITY_FIELDS = "info|labels|descriptions|aliases|claims|sitelinks"
_QID = re.compile(r"Q[1-9][0-9]*\Z")
WIKIDATA_PUBLIC_USER_AGENT = "MHMPipeline/1.0 (https://mhm-pipeline.org; Wikidata evidence reader)"
_READ_TIMEOUT = 12.0


class _ReadUnavailableError(ValueError):
    """A temporary read failure can use the separate entity endpoint."""


class ReferenceItemReader:
    """Use public GET endpoints only. Reject an unconfirmed item or revision."""

    def __init__(self, *, client_factory: Callable[[], httpx.AsyncClient] | None = None):
        self._client_factory = client_factory or self._public_client

    @staticmethod
    def _public_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": WIKIDATA_PUBLIC_USER_AGENT, "Cache-Control": "no-cache"},
            follow_redirects=False,
        )

    async def fetch(self, qid: str) -> dict[str, object]:
        if not _QID.fullmatch(qid):
            raise ValueError("The reference read requires an exact item QID.")
        async with self._client_factory() as client:
            try:
                payload = await self._get(client, _API, {
                    "action": "wbgetentities",
                    "ids": qid,
                    "props": _ENTITY_FIELDS,
                    "format": "json",
                    "maxage": "0",
                    "smaxage": "0",
                    "curtimestamp": "1",
                })
                return self._entity(payload, qid)
            except _ReadUnavailableError:
                pass

            revision = await self._current_revision(client, qid)
            try:
                payload = await self._get(
                    client, f"{_ORIGIN}/wiki/Special:EntityData/{qid}.json",
                    {"revision": str(revision)},
                )
            except _ReadUnavailableError as error:
                raise ValueError("The exact Wikidata snapshot is unavailable.") from error
            entity = self._entity(payload, qid)
            if entity["lastrevid"] != revision:
                raise ValueError("The Wikidata snapshot does not match the current revision.")
            return entity

    async def fetch_batch(self, client: httpx.AsyncClient, qids: list[str]) -> dict[str, dict]:
        """Read up to 50 exact public items. Omit missing and redirected items."""
        if not 1 <= len(qids) <= 50 or len(set(qids)) != len(qids) or not all(
            _QID.fullmatch(qid) for qid in qids
        ):
            raise ValueError("A batch needs 1 to 50 distinct QIDs.")
        payload = await self._get(client, _API, {
            "action": "wbgetentities", "ids": "|".join(qids), "props": _ENTITY_FIELDS,
            "format": "json", "maxage": "0", "smaxage": "0", "curtimestamp": "1",
            "redirects": "yes",
        })
        rows = payload.get("entities")
        if not isinstance(rows, dict):
            raise ValueError("Wikidata did not return an item batch.")
        return {
            qid: self._entity(payload, qid)
            for qid in qids
            if isinstance(rows.get(qid), dict)
            and rows[qid].get("id") == qid
            and "missing" not in rows[qid]
            and "redirects" not in rows[qid]
        }

    async def creator_uid(self, qid: str) -> int | None:
        if not _QID.fullmatch(qid):
            raise ValueError("The creator check requires an exact QID.")
        async with self._client_factory() as client:
            payload = await self._get(client, _API, {
                "action": "query", "titles": qid, "prop": "revisions", "rvdir": "newer",
                "rvlimit": "1", "rvprop": "ids|userid", "format": "json", "formatversion": "2",
                "maxage": "0", "smaxage": "0",
            })
        query = payload.get("query", {})
        pages = query.get("pages", [])
        if len(pages) != 1 or query.get("redirects"):
            return None
        page = pages[0]
        revisions = page.get("revisions", [])
        if (
            page.get("title") != qid or page.get("ns") != 0 or page.get("redirect")
            or type(page.get("pageid")) is not int or page["pageid"] <= 0
            or len(revisions) != 1
        ):
            return None
        revision = revisions[0]
        uid = revision.get("userid")
        revision_id = revision.get("revid")
        if (
            type(uid) is not int or uid <= 0 or "userhidden" in revision
            or type(revision_id) is not int or revision_id <= 0
        ):
            return None
        return uid

    async def current_revision(self, qid: str) -> int:
        if not _QID.fullmatch(qid):
            raise ValueError("The revision check requires an exact item QID.")
        async with self._client_factory() as client:
            return await self._current_revision(client, qid)

    @staticmethod
    async def _get(client: httpx.AsyncClient, url: str, params: dict[str, str]) -> dict:
        try:
            async with asyncio.timeout(_READ_TIMEOUT):
                client.cookies.clear()
                response = await client.get(url, params=params)
        except (httpx.TransportError, TimeoutError) as error:
            raise _ReadUnavailableError("The Wikidata read is temporarily unavailable.") from error
        if response.status_code == 429 or "Retry-After" in response.headers:
            raise ValueError("Wikidata requested a delay for the read.")
        if response.status_code in {500, 502, 503, 504}:
            raise _ReadUnavailableError("The Wikidata read is temporarily unavailable.")
        if not response.is_success:
            raise ValueError("Wikidata rejected the snapshot request.")
        try:
            data = response.json()
        except (ValueError, RecursionError) as error:
            raise _ReadUnavailableError("The Wikidata read is temporarily unavailable.") from error
        if not isinstance(data, dict) or "error" in data or "errors" in data:
            raise ValueError("Wikidata did not confirm the snapshot request.")
        return data

    @staticmethod
    def _entity(payload: dict, qid: str) -> dict[str, object]:
        entities = payload.get("entities")
        entity = entities.get(qid) if isinstance(entities, dict) else None
        if not isinstance(entity, dict) or entity.get("id") != qid or "redirects" in entity:
            raise ValueError("The exact Wikidata item is unavailable.")
        validate_entity(entity)
        return entity

    async def _current_revision(self, client: httpx.AsyncClient, qid: str) -> int:
        try:
            data = await self._get(client, _API, {
                "action": "query",
                "titles": qid,
                "prop": "revisions",
                "rvprop": "ids",
                "format": "json",
                "formatversion": "2",
                "maxage": "0",
                "smaxage": "0",
                "curtimestamp": "1",
            })
        except _ReadUnavailableError as error:
            raise ValueError("The current Wikidata revision is unavailable.") from error
        query = data.get("query")
        pages = query.get("pages") if isinstance(query, dict) else None
        if not isinstance(pages, list) or len(pages) != 1 or not isinstance(pages[0], dict):
            raise ValueError("The current Wikidata revision is unavailable.")
        page = pages[0]
        revisions = page.get("revisions")
        if (
            page.get("title") != qid or page.get("ns") != 0
            or "redirect" in page or "missing" in page or "invalid" in page
            or not isinstance(revisions, list) or len(revisions) != 1
            or not isinstance(revisions[0], dict)
        ):
            raise ValueError("The current Wikidata revision is unavailable.")
        revision = revisions[0].get("revid")
        if type(revision) is not int or revision <= 0:
            raise ValueError("The current Wikidata revision is unavailable.")
        return revision
