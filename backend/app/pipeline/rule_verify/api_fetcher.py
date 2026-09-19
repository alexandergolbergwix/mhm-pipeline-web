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
    """The real fetcher. Never raises on its own — rules catch and abstain."""

    def fetch(call: Any, arg: Any = None) -> Any:
        if call == "confirm_qids_alive":
            from app.pipeline.wikidata_existence import confirm_qids_alive

            return confirm_qids_alive([str(q) for q in arg])
        if call == "inlabel_search":
            from app.pipeline.wikidata_duplicate_probe import _fetch_json, _search_url

            payload = _fetch_json(_search_url(f'inlabel:"{arg}"', limit=10))
            rows = ((payload or {}).get("query") or {}).get("search") or []
            return [
                {"qid": str(r.get("title") or ""), "label": str(arg)}
                for r in rows
                if isinstance(r, dict) and str(r.get("title") or "").startswith("Q")
            ]
        url, params = call
        from urllib.parse import urlencode

        from app.pipeline.wikidata_existence import _fetch_json_throttled

        return _fetch_json_throttled(f"{url}?{urlencode(params)}", timeout=30.0)

    return fetch
