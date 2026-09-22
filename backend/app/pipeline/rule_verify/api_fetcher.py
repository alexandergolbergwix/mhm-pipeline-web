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

logger = logging.getLogger(__name__)


def production_fetcher() -> Any:
    """The real fetcher, memoized per run. Never raises on its own — rules
    catch and abstain.

    The caches die with the pass instance, so nothing crosses runs. They
    collapse repeated probes: the same label/QID recurs across items of a
    corpus, and every avoided request is ~1.1 s of throttle time (W-139).
    ``confirm_qids_alive`` dedupes per QID and batches only the missing
    ones, preserving the client's ``True/False/None`` abstain contract.
    """

    inlabel_cache: dict[str, Any] = {}
    qid_alive_cache: dict[str, bool | None] = {}
    wbget_cache: dict[tuple[str, tuple[tuple[str, str], ...]], Any] = {}

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
