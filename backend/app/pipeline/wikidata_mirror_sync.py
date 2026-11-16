"""Bounded public reads for the local candidate mirror; no edit credential."""

import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta

import requests
from sqlalchemy import delete, select

from app.models.wikidata_mirror import WikidataMirrorEntity, WikidataMirrorFact, WikidataMirrorScope
from app.pipeline.inference_cache import cache_http_call, canonical_hash
from app.pipeline.wikidata_mirror import QID, store_entities, validate_entity

API = "https://www.wikidata.org/w/api.php"
PROPS = "info|labels|descriptions|aliases|claims|sitelinks"
_request_lock = threading.Lock()
_last_request = 0.0


class MirrorReadUnavailableError(RuntimeError):
    pass


def _request_json(params: dict) -> dict:
    global _last_request
    action = params.get("action")
    if action == "wbgetentities":
        allowed = {"action", "ids", "props"}
        qids = str(params.get("ids", "")).split("|")
        if not 1 <= len(qids) <= 50 or any(not QID.fullmatch(qid) for qid in qids):
            raise ValueError("A mirror read needs 1 to 50 exact QIDs.")
        if params.get("props") != PROPS:
            raise ValueError("A mirror read requires the complete entity properties.")
    elif action == "query":
        allowed = {"action", "list", "srsearch", "srnamespace", "srlimit", "sroffset"}
        if params.get("list") != "search" or params.get("srnamespace") != 0:
            raise ValueError("The mirror only discovers public item candidates.")
    else:
        raise ValueError("The mirror permits only public read actions.")
    if set(params) - allowed:
        raise ValueError("The mirror request contains unsupported parameters.")
    with _request_lock:
        time.sleep(max(0, 2.0 - (time.monotonic() - _last_request)))
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(
                API,
                params={**params, "format": "json", "maxlag": 5},
                headers={
                    "User-Agent": "MHM-Pipeline/1.0 "
                    "(public candidate mirror; User:Alexander_Goldberg_IL)"
                },
                timeout=(10, 35),
                allow_redirects=False,
            )
        _last_request = time.monotonic()
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict) or body.get("error"):
        code = (
            (body.get("error") or {}).get("code", "invalid_response")
            if isinstance(body, dict)
            else "invalid_response"
        )
        raise MirrorReadUnavailableError(f"Wikidata mirror read unavailable: {code}")
    if action == "wbgetentities":
        entities = body.get("entities")
        if not isinstance(entities, dict) or set(entities) != set(qids):
            raise MirrorReadUnavailableError("The entity response has incomplete coverage.")
        for qid in qids:
            entity = entities[qid]
            if not isinstance(entity, dict) or entity.get("id") != qid:
                raise MirrorReadUnavailableError("The entity response has a different identity.")
            if "missing" not in entity:
                validate_entity(entity)
    else:
        rows = body.get("query", {}).get("search")
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not QID.fullmatch(str(row.get("title", "")))
            for row in rows
        ):
            raise MirrorReadUnavailableError("The search response is incomplete.")
        offset = body.get("continue", {}).get("sroffset")
        if offset is not None and (type(offset) is not int or offset <= params["sroffset"]):
            raise MirrorReadUnavailableError("The search continuation is invalid.")
    return {"observed_at": datetime.now(UTC).isoformat(), "response": body}


async def cached_public_read(db_factory, params: dict, *, refresh: bool = False) -> dict:
    kind = (
        "wikidata.mirror_entities"
        if params["action"] == "wbgetentities"
        else "wikidata.mirror_search"
    )
    async with db_factory() as db:

        async def fetch():
            await db.rollback()
            return await asyncio.to_thread(_request_json, params)

        return await cache_http_call(
            db, kind=kind, endpoint=API, payload=params, fetch=fetch, skip_cache=refresh
        )


async def refresh_qids(
    db_factory, qids: list[str], *, refresh: bool = False, on_progress=None
) -> dict:
    identifiers = sorted(set(qids))
    if any(not QID.fullmatch(qid) for qid in identifiers):
        raise ValueError("The refresh scope contains an invalid QID.")
    stats = {"requested": len(identifiers), "stored": 0, "missing": 0, "failed": 0, "cached": 0}
    scope_key = canonical_hash({"schema": "mirror-qids-v1", "qids": identifiers})
    completed = []
    async with db_factory() as db:
        if not refresh:
            for start in range(0, len(identifiers), 500):
                completed.extend(
                    (
                        await db.execute(
                            select(WikidataMirrorEntity.qid).where(
                                WikidataMirrorEntity.qid.in_(identifiers[start : start + 500]),
                                WikidataMirrorEntity.observed_at
                                >= datetime.now(UTC) - timedelta(days=1),
                            )
                        )
                    ).scalars()
                )
        stats["cached"] = len(completed)
        await db.merge(
            WikidataMirrorScope(
                scope_key=scope_key,
                source="selected_qids",
                status="selected_qids_complete"
                if len(completed) == len(identifiers)
                else "in_progress",
                requested_count=len(identifiers),
                stored_count=len(completed),
                progress={"qids": identifiers, "completed": completed},
                updated_at=datetime.now(UTC),
            )
        )
        await db.commit()
    already_cached = set(completed)
    remaining = [qid for qid in identifiers if qid not in already_cached]
    for start in range(0, len(remaining), 50):
        chunk = remaining[start : start + 50]
        try:
            envelope = await cached_public_read(
                db_factory,
                {"action": "wbgetentities", "ids": "|".join(chunk), "props": PROPS},
                refresh=refresh,
            )
            observed = datetime.fromisoformat(envelope["observed_at"])
            entities = envelope["response"].get("entities")
            if not isinstance(entities, dict) or set(entities) != set(chunk):
                raise MirrorReadUnavailableError(
                    "The entity response does not cover the requested QIDs."
                )
            live = []
            missing = []
            for qid in chunk:
                entity = entities[qid]
                if not isinstance(entity, dict) or entity.get("id") != qid:
                    raise MirrorReadUnavailableError(
                        "The entity response has a different identity."
                    )
                if "missing" in entity:
                    missing.append(qid)
                else:
                    live.append(entity)
            async with db_factory() as db:
                stats["stored"] += await store_entities(db, live, observed_at=observed, source=API)
                if missing:
                    # A current complete missing response invalidates an older positive.
                    expired = [
                        row.qid
                        for row in (
                            await db.execute(
                                select(WikidataMirrorEntity).where(
                                    WikidataMirrorEntity.qid.in_(missing),
                                    WikidataMirrorEntity.observed_at <= observed,
                                )
                            )
                        ).scalars()
                    ]
                    await db.execute(
                        delete(WikidataMirrorFact).where(WikidataMirrorFact.qid.in_(expired))
                    )
                    await db.execute(
                        delete(WikidataMirrorEntity).where(WikidataMirrorEntity.qid.in_(expired))
                    )
                completed.extend(chunk)
                stats["missing"] += len(missing)
                scope = await db.get(WikidataMirrorScope, scope_key)
                scope.progress = {"qids": identifiers, "completed": completed}
                scope.stored_count = len(completed)
                scope.status = (
                    "selected_qids_complete"
                    if len(completed) == len(identifiers)
                    else "in_progress"
                )
                scope.updated_at = datetime.now(UTC)
                await db.commit()
        except Exception as error:
            stats["failed"] = len(identifiers) - len(completed)
            async with db_factory() as db:
                scope = await db.get(WikidataMirrorScope, scope_key)
                scope.status = "partial"
                scope.progress = {
                    "qids": identifiers,
                    "completed": completed,
                    "error_type": type(error).__name__,
                }
                scope.updated_at = datetime.now(UTC)
                await db.commit()
            raise MirrorReadUnavailableError(
                f"The mirror refresh stopped: {type(error).__name__}"
            ) from error
        if on_progress:
            on_progress({**stats, "completed": len(completed), "coverage": "selected_qids_only"})
    return stats


def discovery_queries(items: list[dict]) -> list[str]:
    """Use broad seeds remotely; class, author, holder and title checks stay local."""
    from app.pipeline.wikidata_duplicate_probe import identity_probes

    identifiers = set()
    names = set()
    for item in items:
        for probe in identity_probes(item):
            pid, value = probe["pid"], probe["value"]
            if pid.startswith("label:"):
                names.add(value)
            elif pid == "title+P31":
                names.add(value.partition("␟")[0])
            elif "+" in pid:
                values = value.split("␟")
                properties = pid.split("+")
                if len(values) == len(properties):
                    identifiers.add(f"{properties[-1]}={values[-1]}")
            else:
                identifiers.add(f"{pid}={value}")
    seeds = sorted(identifiers)
    return [
        "haswbstatement:" + json.dumps("|".join(seeds[start : start + 20]), ensure_ascii=False)
        for start in range(0, len(seeds), 20)
    ] + [
        json.dumps(name, ensure_ascii=False) for name in sorted(names) if name.strip()
    ]


async def discover_candidates(
    db_factory,
    items: list[dict],
    *,
    max_pages: int = 5,
    max_queries: int = 100,
    refresh: bool = False,
    provider: str = "wikidata",
    on_progress=None,
) -> dict:
    if not 1 <= max_pages <= 100 or not 1 <= max_queries <= 10000:
        raise ValueError("The discovery limits are invalid.")
    if provider not in {"wikidata", "qlever"}:
        raise ValueError("The discovery provider is unsupported.")
    qlever_batches = {}
    if provider == "qlever":
        from app.pipeline import wikidata_mirror_qlever

        qlever_batches = {
            batch.sparql(): batch for batch in wikidata_mirror_qlever.discovery_batches(items)
        }
    qids = set()
    for item in items:
        existing = item.get("existing_qid")
        if isinstance(existing, str) and QID.fullmatch(existing):
            qids.add(existing)
        for statement in item.get("statements", []):
            value = statement.get("value") if isinstance(statement, dict) else None
            if isinstance(value, str) and QID.fullmatch(value):
                qids.add(value)
    queries = [
        (query, canonical_hash(
            {"schema": "mirror-qlever-v1", "endpoint": wikidata_mirror_qlever.API, "query": query}
            if provider == "qlever" else {"schema": "mirror-search-v1", "query": query}
        ))
        for query in (qlever_batches if provider == "qlever" else discovery_queries(items))
    ]
    saved_scopes = {}
    if not refresh:
        cutoff = datetime.now(UTC) - timedelta(days=1)
        async with db_factory() as db:
            for start in range(0, len(queries), 500):
                keys = [key for _, key in queries[start : start + 500]]
                rows = (
                    await db.execute(
                        select(
                            WikidataMirrorScope.scope_key,
                            WikidataMirrorScope.status,
                            WikidataMirrorScope.progress,
                            WikidataMirrorScope.updated_at,
                        ).where(WikidataMirrorScope.scope_key.in_(keys))
                    )
                ).all()
                for key, status, progress, updated_at in rows:
                    observed = (
                        updated_at.replace(tzinfo=UTC)
                        if updated_at.tzinfo is None
                        else updated_at.astimezone(UTC)
                    )
                    if observed >= cutoff:
                        saved_scopes[key] = (status, dict(progress))
            await db.rollback()
    issued = 0
    partial = 0
    for query, key in queries:
        status, progress = saved_scopes.get(key, ("", {}))
        qids.update(progress.get("qids", []))
        if status == "query_complete":
            continue
        if provider == "qlever" and status == "query_capped":
            partial += 1
            continue
        if issued >= max_queries:
            partial += 1
            continue
        issued += 1
        offset = int(progress.get("next_offset", 0))
        discovered = set(progress.get("qids", []))
        finished = False
        for _ in range(max_pages):
            try:
                envelope = await wikidata_mirror_qlever.cached_read(
                    db_factory, qlever_batches[query], refresh=refresh,
                ) if provider == "qlever" else await cached_public_read(
                    db_factory,
                    {
                        "action": "query",
                        "list": "search",
                        "srsearch": query,
                        "srnamespace": 0,
                        "srlimit": 50,
                        "sroffset": offset,
                    },
                    refresh=refresh,
                )
                response = envelope["response"]
                rows = response.get("query", {}).get("search")
                if not isinstance(rows, list):
                    raise MirrorReadUnavailableError("The search response is incomplete.")
                for row in rows:
                    qid = row.get("title") if isinstance(row, dict) else None
                    if not isinstance(qid, str) or not QID.fullmatch(qid):
                        raise MirrorReadUnavailableError("The search response has an invalid item.")
                    discovered.add(qid)
                continuation = response.get("continue", {}).get("sroffset")
                capped = provider == "qlever" and response.get("limit_reached") is True
                if continuation is None:
                    finished = not capped
                elif type(continuation) is not int or continuation <= offset:
                    raise MirrorReadUnavailableError("The search continuation did not advance.")
                else:
                    offset = continuation
                async with db_factory() as db:
                    await db.merge(
                        WikidataMirrorScope(
                            scope_key=key,
                            source="qlever_search" if provider == "qlever" else "broad_search",
                            status=("query_complete" if finished
                                    else "query_capped" if capped else "partial"),
                            requested_count=0,
                            stored_count=len(discovered),
                            progress={
                                "query": query,
                                "qids": sorted(discovered),
                                "next_offset": offset,
                                "negative_identity_proof": False,
                                "excluded_non_item_rows": response.get("excluded_non_item_rows", 0),
                            },
                            updated_at=datetime.fromisoformat(envelope["observed_at"]),
                        )
                    )
                    await db.commit()
                if finished or capped:
                    break
            except Exception as error:
                raise MirrorReadUnavailableError(
                    f"Candidate discovery stopped: {type(error).__name__}"
                ) from error
        qids.update(discovered)
        if not finished:
            partial += 1
        if on_progress:
            on_progress(
                {
                    "queries": issued,
                    "total_queries": len(queries),
                    "candidate_qids": len(qids),
                    "partial_queries": partial,
                    "coverage": "candidate_search_only",
                    "provider": provider,
                }
            )
    return {
        "qids": sorted(qids),
        "queries": issued,
        "partial_queries": partial,
        "coverage": "candidate_search_only",
        "provider": provider,
    }
