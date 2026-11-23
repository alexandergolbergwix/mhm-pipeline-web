"""Read exact VIAF authority cross-references for MAZAL personal identities."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from collections.abc import Mapping

import httpx

from app.pipeline.wikidata_gila_facts import _snak_value
from app.pipeline.wikidata_source_choices import _claim_values
from converter.wikidata.property_mapping import normalize_bnf, normalize_isni, normalize_lccn
from converter.wikidata.reconciler import WikidataReconciler
from converter.wikidata.retry_policy import retry_after_seconds

logger = logging.getLogger(__name__)
_MAZAL = re.compile(r"987[0-9]{13,21}")
_VIAF = re.compile(r"[1-9][0-9]{0,21}")
_GND = re.compile(r"[0-9][0-9-]{4,16}[0-9X]")
_BASE = "https://viaf.org/viaf/sourceID/J9U%7C"
_URL = re.compile(rf"{re.escape(_BASE)}({_MAZAL.pattern})\?view=json")
_PROPERTIES = {"P8189", "P214", "P227", "P244", "P213", "P268"}
_NORMALIZERS = {"P244": normalize_lccn, "P213": normalize_isni, "P268": normalize_bnf}


def viaf_mazal_url(aid: str) -> str:
    return f"{_BASE}{aid}?view=json"


def cited_mazal_controls(claim: Mapping) -> set[str]:
    return {
        match[1] for ref in claim["references"] for snak in ref.get("snaks", {}).get("P854", [])
        if (match := _URL.fullmatch(_snak_value(snak)))
    }


def cluster_keys(aid: str, payload: Mapping) -> frozenset[tuple[str, str]]:
    """Check the named source ID before accepting other IDs in the cluster."""
    cluster = payload.get("ns1:VIAFCluster", payload)
    if not isinstance(cluster, Mapping) or cluster.get("ns1:nameType") != "Personal":
        return frozenset()
    viaf_id = str(cluster.get("ns1:viafID", ""))
    links = cluster.get("ns1:sources", {})
    raw = links.get("ns1:source", []) if isinstance(links, Mapping) else []
    sources = defaultdict(set)
    for row in raw if isinstance(raw, list) else [raw]:
        if isinstance(row, Mapping) and isinstance(text := row.get("content"), str) and "|" in text:
            prefix, value = text.split("|", 1)
            sources[prefix].add(value.strip())
    if sources["J9U"] != {aid} or not _VIAF.fullmatch(viaf_id):
        return frozenset()
    keys = {("P214", viaf_id)}
    if len(sources["DNB"]) == 1:
        value = next(iter(sources["DNB"]))
        if _GND.fullmatch(value):
            keys.add(("P227", value))
    for prefix, prop in (("LC", "P244"), ("ISNI", "P213"), ("BNF", "P268")):
        if len(sources[prefix]) == 1 and (value := _NORMALIZERS[prop](next(iter(sources[prefix])))):
            keys.add((prop, value))
    return frozenset(keys)


async def read_viaf_mazal(ids, progress=None) -> dict[str, frozenset[tuple[str, str]]]:
    ids = sorted({aid for aid in ids if _MAZAL.fullmatch(aid)})
    if not ids:
        return {}
    headers = {
        "User-Agent": "MHMPipeline/1.0 (https://mhm-pipeline.org; verify VIAF cross-references)",
        "Accept": "application/json",
    }
    results = {}
    async with httpx.AsyncClient(headers=headers, follow_redirects=False, timeout=12) as client:
        for i, aid in enumerate(ids):
            if progress and i % 10 == 0:
                await progress(i, len(ids))
            try:
                response = await client.get(viaf_mazal_url(aid))
                if response.status_code == 429:
                    wait = max(1, retry_after_seconds(response.headers.get("Retry-After")))
                    if wait > 8:
                        break
                    await asyncio.sleep(wait)
                    response = await client.get(viaf_mazal_url(aid))
                    if response.status_code == 429:
                        break
                response.raise_for_status()
                data = response.json()
                if isinstance(data, Mapping) and (keys := cluster_keys(aid, data)):
                    results[aid] = keys
            except (httpx.HTTPError, ValueError) as exc:
                logger.info("VIAF cross-reference unavailable for %s (%s).", aid, type(exc).__name__)
            await asyncio.sleep(0.5)
        if progress:
            await progress(i + 1, len(ids))
    return results


def authority_items(keys) -> dict[tuple[str, str], set[str]]:
    """Use exact identifier statements and retain all candidates for ambiguity checks."""
    groups = defaultdict(set)
    for prop, value in keys:
        if prop in _PROPERTIES and re.fullmatch(r"[A-Za-z0-9 -]{1,24}", value):
            groups[prop].update({value, value.replace(" ", "")})
    lookup = defaultdict(set)
    reconciler = WikidataReconciler()
    for prop, forms in sorted(groups.items()):
        rows = sorted(forms)
        for start in range(0, len(rows), 35):
            requested = set(rows[start:start + 35])
            values = " ".join(json.dumps(value) for value in sorted(requested))
            query = (
                "SELECT DISTINCT ?value ?item WHERE { "
                f"VALUES ?value {{ {values} }} ?item wdt:{prop} ?value . }}"
            )
            for hit in reconciler._query(query):
                value = hit["value"]["value"]
                qid = hit["item"]["value"].rsplit("/", 1)[-1]
                if value in requested and re.fullmatch(r"Q[1-9][0-9]*", qid):
                    normalized = _NORMALIZERS[prop](value) if prop in _NORMALIZERS else value
                    lookup[(prop, normalized)].add(qid)
    return lookup


def viaf_matches(candidate, aid, keys):
    from app.pipeline.wikidata_gila_people import _links

    if not keys or "Q5" not in _links(candidate, "P31"):
        return False
    values = _claim_values(candidate, "P8189")
    if values and set(values) != {aid}:
        return False
    matched = False
    for prop, expected in keys:
        values = _claim_values(candidate, prop)
        if values:
            normalized = {_NORMALIZERS[prop](str(value)) if prop in _NORMALIZERS else value for value in values}
            if normalized != {expected}:
                return False
            matched = True
    return matched
