"""Production network seam for API-backed rules.

One callable answers every API rule request shape so the engine never
opens a socket directly and tests inject a stub:

* ``("confirm_qids_alive", [QIDs])`` → ``{qid: True|False|None}``
  (Wikidata Action API, throttled — reuses ``wikidata_existence``).
* ``("inlabel_search", title)`` → candidate dicts (CirrusSearch via the
  duplicate-probe client, sharing its 1.1 s throttle and polite retries,
  Rule W-139).
* ``(url, params)`` → raw JSON GET (live Wikibase endpoints), same
  throttled client.
"""

from __future__ import annotations

import logging
from typing import Any

# Module-level so tests can patch it and so the per-request cost of the
# import is not paid inside the throttle loop.
from app.pipeline.inference_cache import cache_http_call

logger = logging.getLogger(__name__)


def production_fetcher(
    *,
    warm_wbget: dict[Any, Any] | None = None,
    warm_qid_alive: dict[str, bool | None] | None = None,
    warm_inlabel: dict[str, Any] | None = None,
) -> Any:
    """The real fetcher, memoized per run. Never raises on its own — rules
    catch and abstain.

    The pass-local caches collapse repeated probes *within* a run: the same
    label/QID recurs across items of a corpus, and every avoided request is
    throttle time (W-139). ``confirm_qids_alive`` dedupes per QID and batches
    only the missing ones, preserving the client's ``True/False/None`` abstain
    contract.

    ``warm_*`` seed those caches from the **global** inference cache (Rule
    W-280). ``prime_fetch_caches`` fills them before the rule loop starts, so
    a repeat run costs almost no requests at all. Pass ``None`` to fall back
    to the old pass-local-only behaviour.
    """

    inlabel_cache: dict[str, Any] = dict(warm_inlabel or {})
    qid_alive_cache: dict[str, bool | None] = dict(warm_qid_alive or {})
    wbget_cache: dict[tuple[str, tuple[tuple[str, str], ...]], Any] = dict(
        warm_wbget or {}
    )

    def fetch(call: Any, arg: Any = None) -> Any:
        if call == "confirm_qids_alive":
            qids = [str(q) for q in arg]
            missing = sorted({q for q in qids if q not in qid_alive_cache})
            if missing:
                from app.pipeline.wikidata_existence import confirm_qids_alive  # noqa: PLC0415

                results = confirm_qids_alive(missing) or {}
                for qid, alive in results.items():
                    qid_alive_cache[str(qid)] = alive
            return {q: qid_alive_cache.get(q) for q in qids}
        if call == "inlabel_search":
            title = str(arg)
            if title not in inlabel_cache:
                from app.pipeline.wikidata_duplicate_probe import (  # noqa: PLC0415
                    _fetch_json,
                    _search_url,
                )

                payload = _fetch_json(
                    _search_url(f'inlabel:"{title}"', limit=10), timeout=30.0,
                )
                rows = ((payload or {}).get("query") or {}).get("search") or []
                inlabel_cache[title] = [
                    {"qid": str(r.get("title") or ""), "label": title}
                    for r in rows
                    if isinstance(r, dict) and str(r.get("title") or "").startswith("Q")
                ]
            return list(inlabel_cache[title])
        if isinstance(call, str):
            # Tolerate the (url, params) two-argument call style.
            url, params = call, dict(arg or {})
        else:
            url, params = call
        key = (str(url), tuple(sorted((str(k), str(v)) for k, v in (params or {}).items())))
        if key not in wbget_cache:
            from urllib.parse import urlencode  # noqa: PLC0415

            from app.pipeline.wikidata_existence import _fetch_json_throttled  # noqa: PLC0415

            wbget_cache[key] = _fetch_json_throttled(
                f"{url}?{urlencode(params)}", timeout=30.0,
            )
        return wbget_cache[key]

    return fetch


# wbgetentities accepts many ids per request. Batching is the difference
# between 18,481 throttled round-trips and ~370 (Rule W-280).
WIKI_ENTITY_BATCH = 50


def _wbget_endpoint(endpoint: str) -> str:
    return f"{endpoint.rstrip('/')}/w/api.php"


def _wbget_params(ids: list[str]) -> dict[str, str]:
    return {
        "action": "wbgetentities",
        "ids": "|".join(ids),
        "props": "info|labels",
        "languages": "en|he",
        "format": "json",
        "formatversion": "2",
    }


async def prime_fetch_caches(
    db: Any,
    *,
    endpoint: str,
    qids: list[str],
    labels: list[str],
) -> tuple[dict[Any, Any], dict[str, bool | None], dict[str, Any]]:
    """Seed the fetcher caches from the **global** inference cache (W-280).

    Two wins at once:

    * **Batching** — ``wbgetentities`` is asked for up to
      ``WIKI_ENTITY_BATCH`` ids at a time instead of one per item, so a first
      run costs a few hundred throttled requests rather than one per entity.
    * **Cross-run reuse** — every request goes through
      ``cache_http_call(user_id=None)``, which is Redis L1 → Postgres L2 and
      shared across users and projects. A repeat run over the same corpus
      costs almost no network at all.

    The cache key is ``endpoint`` + payload with volatile keys stripped
    (``timestamp``, ``date``, ``fetched_at``, ``now``, …), so two identical
    requests hash the same no matter when they are made. Date and time never
    enter the key.

    Returns the three warm maps ``production_fetcher`` expects. Values are
    split back per id so the per-item rule loop finds a hit under its own
    single-id request shape.
    """
    if not endpoint:
        return {}, {}, {}

    async def _get(url: str, payload: dict[str, str], kind: str) -> Any:
        async def fetch() -> Any:
            from urllib.parse import urlencode  # noqa: PLC0415

            from app.pipeline.wikidata_existence import (  # noqa: PLC0415
                _fetch_json_throttled,
            )

            return await _to_thread_sync(
                _fetch_json_throttled, f"{url}?{urlencode(payload)}", timeout=45.0,
            )

        return await cache_http_call(
            db, kind=kind, endpoint=url, payload=payload,
            fetch=fetch, user_id=None,
        )

    wbget_warm: dict[Any, Any] = {}
    qid_alive_warm: dict[str, bool | None] = {}
    api = _wbget_endpoint(endpoint)

    unique_qids = [q for q in dict.fromkeys(qids) if q]
    for start in range(0, len(unique_qids), WIKI_ENTITY_BATCH):
        batch = unique_qids[start : start + WIKI_ENTITY_BATCH]
        payload = _wbget_params(batch)
        try:
            body = await _get(api, payload, "wikidata.wiki_entity")
        except Exception as exc:  # noqa: BLE001 — priming is best-effort
            logger.debug("prime_fetch_caches: entity batch skipped: %s", exc)
            continue
        entities = (body or {}).get("entities") or {}
        for qid in batch:
            one = {"entities": {qid: entities.get(qid) or {}}}
            single = _wbget_params([qid])
            key = (api, tuple(sorted((str(k), str(v)) for k, v in single.items())))
            wbget_warm[key] = one
            ent = entities.get(qid) or {}
            if ent.get("missing") is not None or not ent:
                qid_alive_warm[qid] = False
            elif ent.get("id") or ent.get("title"):
                qid_alive_warm[qid] = True

    inlabel_warm: dict[str, Any] = {}
    for title in dict.fromkeys(labels):
        if not title:
            continue
        try:
            from app.pipeline.wikidata_duplicate_probe import _search_url  # noqa: PLC0415

            url = _search_url(f'inlabel:"{title}"', limit=10)
            body = await _get(url, {}, "wikidata.duplicate_probe")
        except Exception as exc:  # noqa: BLE001 — priming is best-effort
            logger.debug("prime_fetch_caches: label %r skipped: %s", title, exc)
            continue
        rows = ((body or {}).get("query") or {}).get("search") or []
        inlabel_warm[title] = [
            {"qid": str(r.get("title") or ""), "label": title}
            for r in rows
            if isinstance(r, dict) and str(r.get("title") or "").startswith("Q")
        ]

    return wbget_warm, qid_alive_warm, inlabel_warm


async def _to_thread_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
    import asyncio  # noqa: PLC0415

    return await asyncio.to_thread(lambda: fn(*args, **kwargs))
