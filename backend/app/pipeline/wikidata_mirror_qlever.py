"""Fixed SELECT batches for an optional public candidate index."""

import asyncio
import json
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

import requests

from app.pipeline.inference_cache import cache_http_call
from app.pipeline.wikidata_duplicate_probe import identity_probes

API = "https://qlever.dev/api/wikidata"
DIRECT = "http://www.wikidata.org/prop/direct/"
NAME_PREDICATES = (
    "http://www.w3.org/2000/01/rdf-schema#label",
    "http://www.w3.org/2004/02/skos/core#altLabel",
    DIRECT + "P1476",
)
IDENTIFIER_PREDICATES = frozenset(
    DIRECT + pid for pid in ("P3959", "P214", "P8189", "P244", "P227")
)
_ITEM_URI = re.compile(r"https?://www\.wikidata\.org/entity/(Q[1-9][0-9]*)")
_lock = threading.Lock()
_last_request = 0.0


@dataclass(frozen=True)
class SeedBatch:
    predicate: str
    values: tuple[str, ...]

    def sparql(self) -> str:
        if self.predicate not in (*NAME_PREDICATES, *IDENTIFIER_PREDICATES):
            raise ValueError("The candidate predicate is unsupported.")
        if not 1 <= len(self.values) <= 50 or any(
            not isinstance(value, str) or not value.strip() for value in self.values
        ):
            raise ValueError("A candidate batch needs 1 to 50 nonempty string seeds.")
        languages = ("@he", "@en", "") if self.predicate in NAME_PREDICATES else ("",)
        terms = " ".join(json.dumps(value, ensure_ascii=False) + language
                         for value in self.values for language in languages)
        return ("SELECT ?item WHERE { VALUES ?value { " + terms + " } ?item <"
                + self.predicate + "> ?value } LIMIT 1001")


def discovery_batches(items: list[dict]) -> list[SeedBatch]:
    values_by_predicate = defaultdict(set)
    names = set()
    for item in items:
        for probe in identity_probes(item):
            predicate = DIRECT + probe["pid"]
            if probe["kind"] == "identifier" and predicate in IDENTIFIER_PREDICATES:
                values_by_predicate[predicate].add(probe["value"])
            elif probe["pid"].startswith("label:"):
                names.add(probe["value"])
            elif probe["pid"] == "title+P31":
                names.add(probe["value"].partition("␟")[0])
    for predicate in NAME_PREDICATES:
        values_by_predicate[predicate].update(name for name in names if name.strip())
    batches = []
    for predicate, seeds in sorted(values_by_predicate.items()):
        values = sorted(seeds)
        for start in range(0, len(values), 50):
            batches.append(SeedBatch(predicate, tuple(values[start:start + 50])))
    return batches


def decode_response(body: object) -> dict:
    if not isinstance(body, dict) or body.get("error") or body.get("head") != {"vars": ["item"]}:
        raise ValueError("The candidate index returned an incomplete result.")
    results = body.get("results")
    rows = results.get("bindings") if isinstance(results, dict) else None
    if not isinstance(rows, list):
        raise ValueError("The candidate index omitted its result rows.")
    qids = set()
    excluded = 0
    for row in rows:
        term = row.get("item") if isinstance(row, dict) else None
        if (not isinstance(term, dict) or term.get("type") != "uri"
                or not isinstance(term.get("value"), str)):
            raise ValueError("The candidate index returned an invalid item term.")
        match = _ITEM_URI.fullmatch(term["value"])
        if match:
            qids.add(match.group(1))
        else:
            excluded += 1
    return {"query": {"search": [{"title": qid} for qid in sorted(qids)]},
            "limit_reached": len(rows) >= 1001, "excluded_non_item_rows": excluded}


def request_batch(batch: SeedBatch) -> dict:
    global _last_request
    query = batch.sparql()
    with _lock:
        time.sleep(max(0.0, 2.0 - (time.monotonic() - _last_request)))
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.post(
                    API, data={"query": query},
                    headers={
                        "Accept": "application/sparql-results+json",
                        "User-Agent": "MHM-Pipeline/1.0 "
                        "(public candidate mirror; User:Alexander_Goldberg_IL)",
                    },
                    timeout=(10, 35), allow_redirects=False,
                )
            response.raise_for_status()
            decoded = decode_response(response.json())
        finally:
            _last_request = time.monotonic()
    return {"observed_at": datetime.now(UTC).isoformat(), "response": decoded}


async def cached_read(db_factory, batch: SeedBatch, *, refresh: bool = False) -> dict:
    query = batch.sparql()
    async with db_factory() as db:
        async def fetch():
            await db.rollback()
            return await asyncio.to_thread(request_batch, batch)

        return await cache_http_call(
            db, kind="wikidata.mirror_search", endpoint=API,
            payload={"schema": "mirror-qlever-v1", "query": query}, fetch=fetch, skip_cache=refresh,
        )
