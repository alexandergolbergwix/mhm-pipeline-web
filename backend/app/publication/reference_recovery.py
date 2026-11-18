"""Read-only lag and mirror checks for the reviewed reference queue."""

from collections import Counter
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal

import httpx
from sqlalchemy import select

from app.db import session_scope
from app.models.wikidata_mirror import WikidataMirrorEntity
from app.pipeline.wikidata_gila_repairs import build_gila_repair_proposals
from app.pipeline.wikidata_mirror import validate_entity
from converter.wikidata.retry_policy import BULK_MAXLAG, retry_after_seconds

LAG_PROBE_INTERVAL = 60.0
_API = "https://www.wikidata.org/w/api.php"


@dataclass(slots=True)
class LagProbeSchedule:
    next_monotonic: float = float("-inf")


@dataclass(frozen=True, slots=True)
class LagProbeResult:
    status: Literal["ready", "delayed", "unavailable"]
    retry_after: float = 0.0


async def probe_reference_lag(client: httpx.AsyncClient) -> LagProbeResult:
    """Ask the edit endpoint about lag without credentials or an edit payload."""
    try:
        response = await client.post(
            _API,
            data={
                "action": "query",
                "meta": "siteinfo",
                "siprop": "general",
                "format": "json",
                "maxlag": str(BULK_MAXLAG),
                "maxage": "0",
                "smaxage": "0",
            },
        )
    except httpx.RequestError:
        return LagProbeResult("unavailable")
    delay = retry_after_seconds(response.headers.get("Retry-After"), default=0)
    if not response.is_success:
        return LagProbeResult("unavailable", delay)
    try:
        data = response.json()
    except (ValueError, RecursionError):
        return LagProbeResult("unavailable", delay)
    if not isinstance(data, dict):
        return LagProbeResult("unavailable", delay)
    error = data.get("error")
    if isinstance(error, dict) and error.get("code") == "maxlag":
        return LagProbeResult("delayed", delay)
    query = data.get("query")
    if (
        "error" not in data and "errors" not in data and not delay
        and isinstance(query, dict) and isinstance(query.get("general"), dict)
    ):
        return LagProbeResult("ready")
    return LagProbeResult("unavailable", delay)


def classify_reference_snapshot(
    qid: str, revision: int, document: dict,
) -> Literal["candidates", "seen_clean", "unchecked"]:
    if not isinstance(document, dict) or document.get("id") != qid:
        return "unchecked"
    if document.get("lastrevid") != revision or "redirects" in document:
        return "unchecked"
    try:
        validate_entity(document)
        proposals = build_gila_repair_proposals({qid: document})
    except ValueError:
        return "unchecked"
    return "candidates" if proposals else "seen_clean"


async def scan_reference_mirror(qids: Sequence[str]) -> AsyncIterator[dict[str, int]]:
    """Classify saved snapshots in bounded reads. No result grants write authority."""
    counts: Counter[str] = Counter()
    for start in range(0, len(qids), 50):
        batch = qids[start : start + 50]
        async with session_scope() as db:
            snapshot = WikidataMirrorEntity
            rows = (
                await db.execute(
                    select(snapshot.qid, snapshot.revision, snapshot.document)
                    .where(snapshot.qid.in_(batch))
                )
            ).all()
            await db.rollback()
        for qid, revision, document in rows:
            counts[classify_reference_snapshot(qid, revision, document)] += 1
        counts["unchecked"] += len(batch) - len(rows)
        yield {**counts, "reviewed": start + len(batch), "total": len(qids)}
