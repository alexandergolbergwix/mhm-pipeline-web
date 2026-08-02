"""Ask Wikidata whether a CREATE candidate already exists, before we judge it.

The upload path is already fail-closed on duplicates (Rule W-30 / W-99), but the
curator and the AI judge saw no signal until then: an item could be reviewed,
approved and only *then* discovered to duplicate a live Wikidata item. This
module probes for existing items during verify-scope preparation and attaches
the result as an evidence channel, so a probable duplicate is visible in the
verdict and in the review table.

**Why the Action API and not WDQS.** Rules W-116 / W-119 forbid live SPARQL on
the verify and build paths: reconciling a 313-item corpus against
``query.wikidata.org`` produced 429s and read timeouts that hung whole jobs.
CirrusSearch (``action=query&list=search&srsearch=haswbstatement:P214=…``) answers
the same question — "does an item with this identifier exist?" — over the light,
rate-limit-friendly Action API, and every answer is cached content-addressed
(Rule W-25).

**Fail closed, never fail silent.** A probe that cannot complete reports
``status="unavailable"``. It must never be rendered as "no duplicate exists" —
that is the reading that lets a duplicate through (Rule W-30's reasoning).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

PROBE_SCHEMA = "dup_probe_v1"
_BATCH_SIZE = 20
_API = "https://www.wikidata.org/w/api.php"
_USER_AGENT = "MHM-Pipeline-Web/1.0 (Wikidata Studio duplicate check; academic research)"

# Identifier properties that answer "is this the same entity?" outright.
_IDENTIFIER_PIDS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "manuscript": ("P3959",),
    "person": ("P214", "P8189", "P244", "P227"),
    "work": (),
}

# Conjunctive keys: no single identifier answers "same entity?", but the
# combination does (Rule W-144). All 33 Samaritan manuscripts on Wikidata carry
# no `P3959` at all, yet each is identified by its holder plus shelfmark —
# `CAJS Rar Ms 75-117` at Penn, `MS. Bodley Or. 699` at the Bodleian. A `P3959`
# probe cannot see any of them.
#
# These cannot be batched: `haswbstatement` joins with `|` as OR, so an AND is
# one request per item. Bounded by the same probe budget.
_COMPOSITE_PIDS_BY_TYPE: dict[str, tuple[tuple[str, ...], ...]] = {
    "manuscript": (("P195", "P217"),),
}

# Separator inside a synthetic composite key. Chosen because neither a QID nor a
# shelfmark contains it, so the key round-trips through the cache unambiguously.
_COMPOSITE_SEP = "␟"

# Works carry no identifier at all — 0 of 105 in the reference corpus — so there
# was no duplicate check on them whatsoever (Rule W-145). A title is NOT an
# identifier, so this probe returns *candidates for the curator*, never a match.
# It exists because the collisions here are near-certain: the judge already caught
# us proposing a new item against Q623354, the Passover Haggadah.
_TITLE_PROBE_TYPES: dict[str, str] = {"work": "P1476"}
_TITLE_PREFIX = "title+"

STATUS_ABSENT = "absent"
STATUS_CANDIDATES = "candidates_found"
STATUS_UNAVAILABLE = "unavailable"
STATUS_SKIPPED = "skipped"
STATUS_HAS_QID = "already_linked"


def probe_enabled() -> bool:
    return os.getenv("WIKIDATA_DUPLICATE_PROBE", "1").strip().lower() not in {
        "0", "false", "no",
    }


def _probe_budget() -> int:
    try:
        return max(0, int(os.getenv("WIKIDATA_DUPLICATE_PROBE_MAX", "400")))
    except ValueError:
        return 400


def _timeout() -> float:
    try:
        return max(1.0, float(os.getenv("WIKIDATA_DUPLICATE_PROBE_TIMEOUT", "8")))
    except ValueError:
        return 8.0


def _statement_values(item: dict[str, Any], pid: str) -> list[str]:
    out: list[str] = []
    for statement in item.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        prop = str(statement.get("property_id") or statement.get("property") or "")
        if prop != pid:
            continue
        value = str(statement.get("value") or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def identity_probes(item: dict[str, Any]) -> list[dict[str, str]]:
    """Every lookup that would reveal an existing item for *item*.

    Identifier probes answer outright; composite probes (Rule W-144) answer by
    conjunction. Both count, so an item with only a composite probe available is
    still probed rather than skipped.
    """
    entity_type = str(item.get("entity_type") or "")
    probes: list[dict[str, str]] = []
    for pid in _IDENTIFIER_PIDS_BY_TYPE.get(entity_type, ()):
        for value in _statement_values(item, pid):
            probes.append({"kind": "identifier", "pid": pid, "value": value})
    probes.extend(composite_probes(item))
    probes.extend(title_probes(item))
    return probes


def title_probes(item: dict[str, Any]) -> list[dict[str, str]]:
    """A title-plus-class lookup for entity types that have no identifier."""
    entity_type = str(item.get("entity_type") or "")
    title_pid = _TITLE_PROBE_TYPES.get(entity_type)
    if not title_pid:
        return []
    titles = _statement_values(item, title_pid) or [
        str((item.get("labels") or {}).get("he") or (item.get("labels") or {}).get("en") or "")
    ]
    title = titles[0].strip()
    classes = _statement_values(item, "P31")
    if not title or len(classes) != 1:
        return []
    return [{
        "kind": "title",
        "pid": f"{_TITLE_PREFIX}P31",
        "value": _COMPOSITE_SEP.join((title, classes[0])),
    }]


def composite_probes(item: dict[str, Any]) -> list[dict[str, str]]:
    """Conjunctive lookups — every PID in the group must have exactly one value.

    Fails closed: a manuscript whose holder abstained (Rule W-143) has no `P195`,
    so no composite probe is produced and the item cannot report `absent` on this
    key. Two shelfmarks are ambiguous, so they abstain too.
    """
    entity_type = str(item.get("entity_type") or "")
    probes: list[dict[str, str]] = []
    for group in _COMPOSITE_PIDS_BY_TYPE.get(entity_type, ()):
        values: list[str] = []
        for pid in group:
            found = _statement_values(item, pid)
            if len(found) != 1:
                values = []
                break
            values.append(found[0])
        if not values:
            continue
        probes.append({
            "kind": "composite",
            "pid": "+".join(group),
            "value": _COMPOSITE_SEP.join(values),
        })
    return probes


def _composite_conjunction(pid: str, value: str) -> list[tuple[str, str]]:
    """Split a synthetic composite key back into its `(pid, value)` pairs."""
    return list(zip(pid.split("+"), value.split(_COMPOSITE_SEP), strict=True))


def _search_url(query: str, *, limit: int = 10) -> str:
    params = urllib.parse.urlencode({
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": str(limit),
        "format": "json",
        "formatversion": "2",
        # Back off automatically when the replicas lag (API etiquette).
        "maxlag": "5",
    })
    return f"{_API}?{params}"


def _claims_url(qid: str) -> str:
    params = urllib.parse.urlencode({
        "action": "wbgetentities",
        "ids": qid,
        "props": "claims|labels|descriptions",
        "languages": "en|he",
        "format": "json",
        "formatversion": "2",
        "maxlag": "5",
    })
    return f"{_API}?{params}"


def _min_interval() -> float:
    try:
        return max(0.0, float(os.getenv("WIKIDATA_DUPLICATE_PROBE_INTERVAL", "1.1")))
    except ValueError:
        return 1.1


_last_call_at = 0.0


def _fetch_json(url: str, *, timeout: float) -> dict[str, Any]:
    """One throttled Action API GET, retrying politely on 429 / maxlag.

    Unthrottled probing earns `429 Too Many Requests` after about ten calls —
    measured, not assumed: 50 of 60 probes failed that way. A shared minimum
    interval plus Retry-After keeps a whole corpus inside the API's etiquette
    (Rule W-139).
    """
    import time  # noqa: PLC0415

    global _last_call_at
    interval = _min_interval()
    delay = 2.0
    for attempt in range(4):
        wait = interval - (time.monotonic() - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                _last_call_at = time.monotonic()
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict) and payload.get("error"):
                code = str((payload.get("error") or {}).get("code") or "")
                if code == "maxlag" and attempt < 3:
                    time.sleep(delay)
                    delay *= 2
                    continue
            return payload
        except urllib.error.HTTPError as exc:
            _last_call_at = time.monotonic()
            if exc.code != 429 or attempt == 3:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                sleep_for = float(retry_after) if retry_after else delay
            except (TypeError, ValueError):
                sleep_for = delay
            time.sleep(min(max(sleep_for, 1.0), 30.0))
            delay *= 2
    raise urllib.error.URLError("duplicate probe exhausted its retries")


def search_by_statement(
    pid: str,
    value: str,
    *,
    timeout: float | None = None,
    fetch: Any = None,
) -> dict[str, Any]:
    """CirrusSearch for items carrying ``pid=value``.

    Returns ``{"status": …, "candidates": [...]}``. A network or parse failure is
    reported as ``unavailable`` — never as ``absent``.
    """
    caller = fetch or _fetch_json
    query = f"haswbstatement:{pid}={value}"
    try:
        payload = caller(_search_url(query), timeout=timeout or _timeout())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.warning("wikidata duplicate probe failed for %s=%s: %s", pid, value, exc)
        return {"status": STATUS_UNAVAILABLE, "candidates": [], "error": str(exc)}

    results = ((payload or {}).get("query") or {}).get("search") or []
    candidates = [
        {
            "qid": str(row.get("title") or ""),
            "matched_on": f"{pid}={value}",
            "snippet": str(row.get("snippet") or "")[:200],
        }
        for row in results
        if isinstance(row, dict) and str(row.get("title") or "").startswith("Q")
    ]
    return {
        "status": STATUS_CANDIDATES if candidates else STATUS_ABSENT,
        "candidates": candidates,
    }


def _merge(results: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    unavailable = False
    for result in results:
        if result.get("status") == STATUS_UNAVAILABLE:
            unavailable = True
        for candidate in result.get("candidates") or []:
            qid = str(candidate.get("qid") or "")
            if qid and qid not in seen:
                seen.add(qid)
                candidates.append(candidate)
    if candidates:
        status = STATUS_CANDIDATES
    elif unavailable:
        status = STATUS_UNAVAILABLE
    else:
        status = STATUS_ABSENT
    return {"status": status, "candidates": candidates}


def decide_without_network(item: dict[str, Any]) -> dict[str, Any] | None:
    """The two verdicts that need no lookup, or ``None`` when a probe is needed."""
    existing = str(item.get("existing_qid") or "").strip()
    if existing:
        return {
            "status": STATUS_HAS_QID,
            "existing_qid": existing,
            "candidates": [],
            "note": "item already targets a live Wikidata QID; no CREATE risk",
        }
    if not identity_probes(item):
        return {
            "status": STATUS_SKIPPED,
            "candidates": [],
            "note": (
                "no identifier, holder+shelfmark or title+class key to probe — "
                "absence of a duplicate is NOT established for this item"
            ),
        }
    return None


async def probe_item(
    db: AsyncSession | None,
    item: dict[str, Any],
    *,
    fetch: Any = None,
    skip_cache: bool = False,
) -> dict[str, Any]:
    """Existence evidence for one Studio item (cached; never raises)."""
    decided = decide_without_network(item)
    if decided is not None:
        return decided
    probes = identity_probes(item)

    async def run() -> dict[str, Any]:
        from fastapi.concurrency import run_in_threadpool  # noqa: PLC0415

        results = [
            await run_in_threadpool(
                search_by_statement, probe["pid"], probe["value"], fetch=fetch,
            )
            for probe in probes
        ]
        return {**_merge(results), "probed": probes}

    if db is None:
        return await run()

    from app.pipeline.inference_cache import cache_lookup_or_call  # noqa: PLC0415

    try:
        return await cache_lookup_or_call(
            db,
            kind="wikidata.duplicate_probe",
            query_summary={
                "schema": PROBE_SCHEMA,
                "entity_type": item.get("entity_type"),
                "probes": probes,
            },
            fetch=run,
            skip_cache=skip_cache,
        )
    except Exception as exc:  # noqa: BLE001 — a probe must never break verify
        logger.warning("duplicate probe cache path failed: %s", exc)
        return {"status": STATUS_UNAVAILABLE, "candidates": [], "error": str(exc)}


def _batch_query(pairs: list[tuple[str, str]]) -> str:
    return "haswbstatement:" + "|".join(f"{pid}={value}" for pid, value in pairs)


def _conjunction_query(conjunction: list[tuple[str, str]]) -> str:
    """AND of several statement filters — space-separated, never `|` (that is OR)."""
    return " ".join(
        f'haswbstatement:{pid}="{value}"' if " " in value else f"haswbstatement:{pid}={value}"
        for pid, value in conjunction
    )


def probe_title(
    pid: str,
    value: str,
    *,
    fetch: Any = None,
    timeout: float | None = None,
) -> list[dict[str, str]]:
    """Candidates whose label or alias is this title and whose class matches.

    `matched_on` deliberately says `title~` — a tilde, because this is a *likeness*
    and the curator must confirm it. Nothing downstream may treat it as identity.
    """
    caller = fetch or _fetch_json
    title, _sep, class_qid = value.partition(_COMPOSITE_SEP)
    query = f'inlabel:"{title}" haswbstatement:P31={class_qid}'
    payload = caller(_search_url(query, limit=10), timeout=timeout or _timeout())
    results = ((payload or {}).get("query") or {}).get("search") or []
    out: list[dict[str, str]] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        qid = str(row.get("title") or "")
        if not qid.startswith("Q"):
            continue
        out.append({
            "qid": qid,
            "matched_on": f"title~{title} AND P31={class_qid}",
            "label": title,
            "requires_curator_confirmation": "true",
        })
    return out


def probe_composite(
    pid: str,
    value: str,
    *,
    fetch: Any = None,
    timeout: float | None = None,
) -> list[dict[str, str]]:
    """Resolve one conjunctive key. Every hit is a candidate for *this* item.

    Unlike an identifier probe there is no per-claim attribution to do: matching
    the whole conjunction *is* the evidence, so the search result stands alone and
    costs one request instead of one-plus-one-per-hit.
    """
    caller = fetch or _fetch_json
    conjunction = _composite_conjunction(pid, value)
    payload = caller(
        _search_url(_conjunction_query(conjunction), limit=10),
        timeout=timeout or _timeout(),
    )
    results = ((payload or {}).get("query") or {}).get("search") or []
    matched_on = " AND ".join(f"{p}={v}" for p, v in conjunction)
    out: list[dict[str, str]] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        qid = str(row.get("title") or "")
        if not qid.startswith("Q"):
            continue
        out.append({"qid": qid, "matched_on": matched_on, "label": ""})
    return out


def probe_batch(
    pairs: list[tuple[str, str]],
    *,
    fetch: Any = None,
    timeout: float | None = None,
) -> dict[tuple[str, str], list[dict[str, str]]]:
    """Resolve many ``(pid, value)`` identifiers in one CirrusSearch request.

    212 single probes on the reference corpus become ~15 batched requests, which
    is what keeps the check inside the API's rate limits. Each hit is then
    attributed to the identifier that matched by reading that item's claims —
    one extra request per hit, and hits are rare.
    """
    caller = fetch or _fetch_json
    out: dict[tuple[str, str], list[dict[str, str]]] = {}
    if not pairs:
        return out
    payload = caller(_search_url(_batch_query(pairs), limit=50), timeout=timeout or _timeout())
    results = ((payload or {}).get("query") or {}).get("search") or []
    qids = [
        str(row.get("title") or "")
        for row in results
        if isinstance(row, dict) and str(row.get("title") or "").startswith("Q")
    ]
    wanted = {pid for pid, _ in pairs}
    for qid in qids:
        entity = caller(_claims_url(qid), timeout=timeout or _timeout())
        claims = (((entity or {}).get("entities") or {}).get(qid) or {}).get("claims") or {}
        labels = (((entity or {}).get("entities") or {}).get(qid) or {}).get("labels") or {}
        label = ""
        for lang in ("en", "he"):
            value = labels.get(lang)
            if isinstance(value, dict) and value.get("value"):
                label = str(value["value"])
                break
            if isinstance(value, str) and value:
                label = value
                break
        for pid in wanted:
            for claim in claims.get(pid) or []:
                snak = ((claim or {}).get("mainsnak") or {}).get("datavalue") or {}
                value = str(snak.get("value") or "").strip()
                key = (pid, value)
                if key in out or key not in {(p, v) for p, v in pairs}:
                    continue
                out.setdefault(key, []).append({
                    "qid": qid,
                    "matched_on": f"{pid}={value}",
                    "label": label,
                })
    return out


CACHE_KIND = "wikidata.duplicate_probe"

STATUS_NOT_PROBED = "not_probed"


async def attach_cached_duplicate_evidence(
    db_factory: Any,
    items: list[dict[str, Any]],
) -> dict[str, int]:
    """Stamp the *cached* duplicate answer with no network call (Rule W-144).

    Export (19) reported `duplicate_check: not_run` on all 313 items while 207
    probe answers sat in the cache: `_wikidata_existence` lives only in the verify
    process's memory and is deliberately outside the verdict fingerprint
    (Rule W-136), so no read path ever showed it. A check the curator cannot see
    is not a check.

    Read-only by construction — it never probes, so it cannot turn opening an
    export into external I/O.
    """
    stats = {"answered": 0, "candidates": 0, "not_probed": 0}
    pending: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        if item.get("_wikidata_existence"):
            continue
        decided = decide_without_network(item)
        if decided is not None:
            item["_wikidata_existence"] = decided
            continue
        probes = identity_probes(item)
        item["_wikidata_existence"] = {
            "status": STATUS_NOT_PROBED,
            "candidates": [],
            "probed": probes,
            "note": "no probe has run for this item yet",
        }
        stats["not_probed"] += 1
        for probe in probes:
            pending.setdefault((probe["pid"], probe["value"]), []).append(item)

    if not pending:
        return stats

    cached = await _read_cached_pairs(db_factory, sorted(pending))
    for key, candidates in cached.items():
        for item in pending.get(key, []):
            existence = item["_wikidata_existence"]
            if existence["status"] == STATUS_NOT_PROBED:
                existence["status"] = STATUS_ABSENT
                existence["note"] = (
                    "answered from the cached probe; re-run verify to refresh"
                )
                stats["not_probed"] -= 1
                stats["answered"] += 1
            if candidates:
                existence["status"] = STATUS_CANDIDATES
                for candidate in candidates:
                    if candidate not in existence["candidates"]:
                        existence["candidates"].append(candidate)

    # A partially cached item must not read as `absent` either (Rule W-144).
    for item in items:
        existence = item.get("_wikidata_existence") or {}
        if existence.get("status") != STATUS_ABSENT:
            continue
        probed = existence.get("probed") or []
        if any((p["pid"], p["value"]) not in cached for p in probed):
            existence["status"] = STATUS_SKIPPED
            existence["note"] = (
                "only some keys have a cached answer — absence of a duplicate is "
                "NOT established for this item"
            )
            stats["answered"] = max(0, stats["answered"] - 1)

    stats["candidates"] = sum(
        1 for item in items
        if (item.get("_wikidata_existence") or {}).get("status") == STATUS_CANDIDATES
    )
    return stats


def _pair_summary(pid: str, value: str) -> dict[str, Any]:
    """Cache key for one identifier lookup, shared across every item using it."""
    return {"schema": PROBE_SCHEMA, "pid": pid, "value": value}


async def _read_cached_pairs(
    db_factory: Any,
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], list[dict[str, str]]]:
    """Cached candidates per identifier. One short transaction, index lookups."""
    if db_factory is None or not pairs:
        return {}
    from app.pipeline.inference_cache import (  # noqa: PLC0415
        canonical_hash,
        read_many_from_inference_cache,
    )

    found: dict[tuple[str, str], list[dict[str, str]]] = {}
    summaries = {key: _pair_summary(*key) for key in pairs}
    try:
        async with db_factory() as db:
            # ONE round trip for the whole corpus: the per-key helper costs a
            # SELECT plus an UPDATE+COMMIT each, which is ~900 round trips for
            # 313 items and does not scale.
            hits = await read_many_from_inference_cache(
                db, kind=CACHE_KIND, query_summaries=list(summaries.values()),
            )
    except Exception as exc:  # noqa: BLE001 — a cache miss must never break verify
        logger.warning("duplicate probe cache read failed: %s", exc)
        return {}
    for key, summary in summaries.items():
        hit = hits.get(canonical_hash(summary))
        # An explicit empty list is a real cached "absent" — keep it.
        if isinstance(hit, dict) and isinstance(hit.get("candidates"), list):
            found[key] = hit["candidates"]
    return found


async def _write_cached_pairs(
    db_factory: Any,
    results: dict[tuple[str, str], list[dict[str, str]]],
) -> None:
    if db_factory is None or not results:
        return
    from app.pipeline.inference_cache import write_many_to_inference_cache  # noqa: PLC0415

    try:
        async with db_factory() as db:
            # One upsert statement, one commit — not one per identifier.
            await write_many_to_inference_cache(
                db,
                kind=CACHE_KIND,
                entries=[
                    (_pair_summary(pid, value), {"candidates": candidates})
                    for (pid, value), candidates in results.items()
                ],
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("duplicate probe cache write failed: %s", exc)


async def attach_duplicate_evidence(
    db_factory: Any,
    items: list[dict[str, Any]],
    *,
    fetch: Any = None,
    budget: int | None = None,
) -> dict[str, int]:
    """Stamp ``_wikidata_existence`` on every CREATE candidate in *items*.

    Bounded by ``WIKIDATA_DUPLICATE_PROBE_MAX`` so a large corpus cannot turn
    verify preparation into a long external I/O loop (Rule W-116's lesson). Items
    past the budget are marked ``skipped`` with the reason, never ``absent``
    (Rule W-110's "no silent caps").
    """
    stats = {
        "probed": 0, "duplicates": 0, "unavailable": 0, "skipped": 0, "cached": 0,
    }
    if not probe_enabled():
        for item in items:
            item["_wikidata_existence"] = {
                "status": STATUS_SKIPPED,
                "candidates": [],
                "note": "duplicate probe disabled (WIKIDATA_DUPLICATE_PROBE=0)",
            }
        stats["skipped"] = len(items)
        return stats

    remaining = _probe_budget() if budget is None else budget
    pending: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for item in items:
        decided = decide_without_network(item)
        if decided is not None:
            item["_wikidata_existence"] = decided
            if decided["status"] == STATUS_SKIPPED:
                stats["skipped"] += 1
            continue
        probes = identity_probes(item)
        if remaining <= 0:
            item["_wikidata_existence"] = {
                "status": STATUS_SKIPPED,
                "candidates": [],
                "note": "probe budget exhausted for this job; duplication not checked",
            }
            stats["skipped"] += 1
            continue
        remaining -= 1
        item["_wikidata_existence"] = {
            "status": STATUS_ABSENT,
            "candidates": [],
            "probed": probes,
            # `absent` is only honest once every key has answered (Rule W-144).
            # Kept as strings, not tuples: this rides on an item that may be
            # serialised to JSON, and a leaked set would raise there.
            "_unanswered": [
                _COMPOSITE_SEP.join((p["pid"], p["value"])) for p in probes
            ],
        }
        stats["probed"] += 1
        for probe in probes:
            pending.setdefault((probe["pid"], probe["value"]), []).append(item)

    from fastapi.concurrency import run_in_threadpool  # noqa: PLC0415

    pairs = sorted(pending)

    def apply(key: tuple[str, str], candidates: list[dict[str, str]]) -> None:
        for item in pending.get(key, []):
            existence = item["_wikidata_existence"]
            answered = _COMPOSITE_SEP.join(key)
            unanswered = existence.get("_unanswered")
            if isinstance(unanswered, list) and answered in unanswered:
                unanswered.remove(answered)
            if not candidates:
                continue
            existence["status"] = STATUS_CANDIDATES
            for candidate in candidates:
                if candidate not in existence["candidates"]:
                    existence["candidates"].append(candidate)

    # Identifier lookups are cached per (pid, value) so they are shared across
    # items and free on a re-run. Read in one short transaction, then release it:
    # the HTTP below must never run inside an open transaction (Rule W-40).
    cached = await _read_cached_pairs(db_factory, pairs)
    for key, candidates in cached.items():
        apply(key, candidates)
    stats["cached"] = len(cached)

    misses = [key for key in pairs if key not in cached]
    # Only single-identifier keys can share a request. Composite and title keys
    # each need their own, so they are dispatched by key shape, not by guesswork.
    single_misses = [key for key in misses if "+" not in key[0]]
    unbatchable = [key for key in misses if "+" in key[0]]
    misses = single_misses
    fresh: dict[tuple[str, str], list[dict[str, str]]] = {}
    for start in range(0, len(misses), _BATCH_SIZE):
        chunk = misses[start : start + _BATCH_SIZE]
        try:
            hits = await run_in_threadpool(probe_batch, chunk, fetch=fetch)
        except Exception as exc:  # noqa: BLE001 — a probe must never break verify
            logger.warning("duplicate probe batch failed: %s", exc)
            for key in chunk:
                for item in pending[key]:
                    item["_wikidata_existence"] = {
                        "status": STATUS_UNAVAILABLE,
                        "candidates": [],
                        "error": str(exc),
                        "note": "lookup failed — duplication is UNKNOWN, not ruled out",
                    }
                    stats["unavailable"] += 1
            continue
        # Every probed identifier in this chunk got an answer, including the
        # absences — caching those is what makes a re-run free.
        for key in chunk:
            fresh[key] = hits.get(key, [])
            apply(key, fresh[key])

    # Conjunctive and title keys cannot share a request (`|` is OR), so one each.
    for key in unbatchable:
        resolver = probe_title if key[0].startswith(_TITLE_PREFIX) else probe_composite
        try:
            hits = await run_in_threadpool(resolver, key[0], key[1], fetch=fetch)
        except Exception as exc:  # noqa: BLE001 — a probe must never break verify
            logger.warning("duplicate probe %s failed: %s", key[0], exc)
            for item in pending[key]:
                existence = item["_wikidata_existence"]
                existence.setdefault("errors", []).append(f"{key[0]}: {exc}")
            continue
        fresh[key] = hits
        apply(key, hits)

    await _write_cached_pairs(db_factory, fresh)

    # A key that never answered means duplication was not ruled out. Reporting
    # `absent` off a partial probe is the exact false negative Rule W-144 forbids.
    for item in items:
        existence = item.get("_wikidata_existence") or {}
        unanswered = existence.pop("_unanswered", None)
        if not unanswered or existence.get("status") != STATUS_ABSENT:
            continue
        existence["status"] = STATUS_SKIPPED
        existence["note"] = (
            f"{len(unanswered)} of {len(existence.get('probed') or [])} keys did not "
            "answer — absence of a duplicate is NOT established for this item"
        )
        stats["skipped"] += 1
        stats["probed"] = max(0, stats["probed"] - 1)

    stats["duplicates"] = sum(
        1 for item in items
        if (item.get("_wikidata_existence") or {}).get("status") == STATUS_CANDIDATES
    )
    logger.info("wikidata duplicate probe: %s", stats)
    return stats
