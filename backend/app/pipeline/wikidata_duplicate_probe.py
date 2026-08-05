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
STATUS_NOT_RUN = "not_run"

# The statuses that actually answer "does this already exist on Wikidata?".
# Everything else means UNKNOWN, and the judge must not read it as "new"
# (Rule W-144).
CONCLUSIVE_STATUSES = frozenset({STATUS_ABSENT, STATUS_CANDIDATES, STATUS_HAS_QID})

# The coarse class the read path CAN reproduce. The raw probe payload cannot key a
# verdict (Rule W-136) — this can, because it is derived from the persisted answer.
DUP_CLASS_CONCLUSIVE = "probed-conclusive"
DUP_CLASS_UNKNOWN = "unknown"


def duplicate_check_fallback() -> dict[str, Any]:
    """The answer for an item no probe has touched. Never ``absent``."""
    return {
        "status": STATUS_NOT_RUN,
        "candidates": [],
        "note": "duplicate probe did not run for this item",
    }


def duplicate_status_for_item(item: dict[str, Any]) -> str:
    """The duplicate status of *item*, from whichever surface carries it."""
    existence = item.get("_wikidata_existence")
    if isinstance(existence, dict) and existence.get("status"):
        return str(existence["status"])
    pack = item.get("verify_evidence")
    if isinstance(pack, dict):
        existing = pack.get("wikidata_existing")
        if isinstance(existing, dict):
            check = existing.get("duplicate_check")
            if isinstance(check, dict) and check.get("status"):
                return str(check["status"])
    return STATUS_NOT_RUN


def duplicate_class_for_item(item: dict[str, Any]) -> str:
    return (
        DUP_CLASS_CONCLUSIVE
        if duplicate_status_for_item(item) in CONCLUSIVE_STATUSES
        else DUP_CLASS_UNKNOWN
    )


def stamp_duplicate_check(item: dict[str, Any]) -> dict[str, Any]:
    """Publish the duplicate answer to every surface — the only writer (Rule W-159).

    Export (23) reported ``duplicate_check: not_run`` inside ``verify_evidence`` on
    all 343 items while 314 answers sat at the top level, because the export built
    the evidence pack *before* stamping the probe and never rebuilt it. Two
    surfaces written by two code paths in two orders will always drift eventually,
    so they are now literally the same object.
    """
    existence = item.get("_wikidata_existence")
    if not isinstance(existence, dict) or not existence:
        existence = duplicate_check_fallback()
        item["_wikidata_existence"] = existence
    pack = item.get("verify_evidence")
    if isinstance(pack, dict):
        existing = pack.get("wikidata_existing")
        if isinstance(existing, dict):
            existing["duplicate_check"] = existence
    return existence


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


# wbgetentities accepts 50 ids per request; asking for more is an API error.
_ENTITIES_PER_REQUEST = 50


def _entities_url(qids: list[str]) -> str:
    params = urllib.parse.urlencode({
        "action": "wbgetentities",
        "ids": "|".join(qids),
        "props": "claims|labels|aliases",
        "languages": "en|he",
        "format": "json",
        "formatversion": "2",
        "maxlag": "5",
    })
    return f"{_API}?{params}"


def _claim_values(claims: dict[str, Any], pid: str) -> set[str]:
    """Every value of *pid*, as a string. Item values come back as their QID."""
    out: set[str] = set()
    for claim in claims.get(pid) or []:
        snak = ((claim or {}).get("mainsnak") or {}).get("datavalue") or {}
        value = snak.get("value")
        if isinstance(value, dict):
            value = value.get("id") or value.get("text") or ""
        text = str(value or "").strip()
        if text:
            out.add(text)
    return out


def _entity_names(entity: dict[str, Any]) -> set[str]:
    """Labels and aliases of *entity*, normalised for title attribution."""
    names: set[str] = set()
    for value in (entity.get("labels") or {}).values():
        text = value.get("value") if isinstance(value, dict) else value
        if text:
            names.add(_normalise_title(str(text)))
    aliases = entity.get("aliases") or {}
    for rows in aliases.values():
        for row in rows if isinstance(rows, list) else []:
            text = row.get("value") if isinstance(row, dict) else row
            if text:
                names.add(_normalise_title(str(text)))
    return names


def _normalise_title(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def _fetch_entities(
    qids: list[str],
    *,
    fetch: Any = None,
    timeout: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Labels, aliases and claims for many QIDs — batched, never one per QID."""
    caller = fetch or _fetch_json
    out: dict[str, dict[str, Any]] = {}
    for start in range(0, len(qids), _ENTITIES_PER_REQUEST):
        chunk = qids[start : start + _ENTITIES_PER_REQUEST]
        payload = caller(_entities_url(chunk), timeout=timeout or _timeout())
        for qid, entity in ((payload or {}).get("entities") or {}).items():
            if isinstance(entity, dict):
                out[str(qid)] = entity
    return out


def _search_qids(payload: dict[str, Any] | None) -> list[str]:
    results = ((payload or {}).get("query") or {}).get("search") or []
    return [
        str(row.get("title") or "")
        for row in results
        if isinstance(row, dict) and str(row.get("title") or "").startswith("Q")
    ]


def _report(on_progress: Any, done: int, total: int) -> None:
    """Publish probe progress, never letting a reporting error break the probe."""
    if on_progress is None:
        return
    try:
        on_progress(done, total)
    except Exception as exc:  # noqa: BLE001
        logger.warning("duplicate probe progress callback failed: %s", exc)


def _unbatchable_budget() -> int:
    """How many one-request-each probes a single job may issue.

    Identifier probes pack 50 to a request; conjunctive and title keys are now
    grouped too (``probe_titles_batch`` / ``probe_composites_batch``), so this
    bounds only the residue — the groups that errored and fell back to one request
    each. Search is the expensive endpoint: the run on 2026-08-02 earned
    `429 Too Many Requests` and, with four polite retries each, left verify sitting
    on "Loading Studio scope…" for tens of minutes. Whatever is dropped is
    reported as `skipped`, cached as `deferred` and logged — never a silent cap.
    """
    try:
        return max(0, int(os.getenv("WIKIDATA_DUPLICATE_PROBE_UNBATCHED_MAX", "40")))
    except ValueError:
        return 40


def _probe_class(key: tuple[str, str]) -> str:
    """The probe class of a key — its PID group, e.g. `title+P31` or `P195+P217`."""
    return key[0]


def order_unbatchable_fairly(keys: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Round-robin across probe classes so no class is systematically last.

    The residue used to be issued in ``sorted()`` order, and `"P195+P217"` sorts
    before `"title+P31"`, so every manuscript composite key was attempted before
    any work title key. Works were not unlucky — they were structurally last, and
    the budget always ran out on them.
    """
    by_class: dict[str, list[tuple[str, str]]] = {}
    for key in keys:
        by_class.setdefault(_probe_class(key), []).append(key)
    out: list[tuple[str, str]] = []
    queues = [iter(sorted(group)) for _, group in sorted(by_class.items())]
    while queues:
        remaining = []
        for queue in queues:
            key = next(queue, None)
            if key is not None:
                out.append(key)
                remaining.append(queue)
        queues = remaining
    return out


def _unbatchable_class_budget(allowance: int, classes_present: int) -> int:
    """Per-class share of the residue budget, so one class cannot consume it all."""
    if classes_present <= 1:
        return allowance
    return max(1, -(-allowance // classes_present))


def _rate_limit_trip() -> int:
    """Consecutive failures after which this job stops probing entirely."""
    try:
        return max(1, int(os.getenv("WIKIDATA_DUPLICATE_PROBE_TRIP", "3")))
    except ValueError:
        return 3


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


def _title_group_size() -> int:
    try:
        return max(1, int(os.getenv("WIKIDATA_DUPLICATE_PROBE_TITLE_GROUP", "10")))
    except ValueError:
        return 10


def probe_titles_batch(
    keys: list[tuple[str, str]],
    *,
    fetch: Any = None,
    timeout: float | None = None,
    group_size: int | None = None,
) -> dict[tuple[str, str], list[dict[str, str]]]:
    """Resolve many title+class keys, grouped by class (Rule W-145).

    One request per group of titles that share a class, then one batched
    ``wbgetentities`` to attribute each hit to the title it actually carries.
    105 works went from 105 sequential searches — more than the whole unbatchable
    budget — to roughly ten. That starvation is why 29 works reported
    ``not_probed`` with their keys already computed.

    Attribution is by normalised label/alias equality, so a hit that merely ranked
    for the group is not credited to a title it does not carry. `matched_on` keeps
    the tilde: this is a likeness the curator must confirm, never an identity.
    """
    caller = fetch or _fetch_json
    limit = group_size or _title_group_size()
    out: dict[tuple[str, str], list[dict[str, str]]] = {}
    if not keys:
        return out

    by_class: dict[str, list[tuple[str, str]]] = {}
    for key in keys:
        title, _sep, class_qid = key[1].partition(_COMPOSITE_SEP)
        if title.strip() and class_qid.strip():
            by_class.setdefault(class_qid, []).append(key)

    for class_qid, class_keys in by_class.items():
        for start in range(0, len(class_keys), limit):
            group = class_keys[start : start + limit]
            titles = {key: key[1].partition(_COMPOSITE_SEP)[0] for key in group}
            clause = " OR ".join(f'inlabel:"{title}"' for title in titles.values())
            query = f"haswbstatement:P31={class_qid} ({clause})"
            qids = _search_qids(
                caller(_search_url(query, limit=50), timeout=timeout or _timeout()),
            )
            for key in group:
                out.setdefault(key, [])
            if not qids:
                continue
            entities = _fetch_entities(qids, fetch=fetch, timeout=timeout)
            for qid, entity in entities.items():
                names = _entity_names(entity)
                for key, title in titles.items():
                    if _normalise_title(title) not in names:
                        continue
                    out[key].append({
                        "qid": qid,
                        "matched_on": f"title~{title} AND P31={class_qid}",
                        "label": title,
                        "requires_curator_confirmation": "true",
                    })
    return out


def _composite_group_size() -> int:
    try:
        return max(1, int(os.getenv("WIKIDATA_DUPLICATE_PROBE_COMPOSITE_GROUP", "15")))
    except ValueError:
        return 15


def composite_batching_enabled() -> bool:
    return os.getenv(
        "WIKIDATA_DUPLICATE_PROBE_BATCH_COMPOSITE", "1",
    ).strip().lower() not in {"0", "false", "no"}


def probe_composites_batch(
    keys: list[tuple[str, str]],
    *,
    fetch: Any = None,
    timeout: float | None = None,
    group_size: int | None = None,
) -> dict[tuple[str, str], list[dict[str, str]]]:
    """Resolve many conjunctive keys by batching the *selective* PID (Rule W-144).

    `haswbstatement` joins with `|` as OR, so the batched query is a deliberate
    superset: it ORs the shelfmarks (the selective half of holder+shelfmark) and
    then enforces the **full conjunction client-side** against each hit's claims.
    A hit that matches only the shelfmark is not a candidate — substituting a
    one-sided lookup for the AND is exactly the false positive W-144 forbids.

    Only groups whose keys share the same PID list can batch together.
    """
    caller = fetch or _fetch_json
    limit = group_size or _composite_group_size()
    out: dict[tuple[str, str], list[dict[str, str]]] = {}
    if not keys:
        return out

    by_shape: dict[str, list[tuple[str, str]]] = {}
    for key in keys:
        by_shape.setdefault(key[0], []).append(key)

    for pid_group, shape_keys in by_shape.items():
        pids = pid_group.split("+")
        selective_pid = pids[-1]
        for start in range(0, len(shape_keys), limit):
            group = shape_keys[start : start + limit]
            conjunctions = {key: _composite_conjunction(*key) for key in group}
            selective = {
                key: next(v for p, v in pairs if p == selective_pid)
                for key, pairs in conjunctions.items()
            }
            clause = "|".join(
                f'{selective_pid}="{value}"' if " " in value else f"{selective_pid}={value}"
                for value in dict.fromkeys(selective.values())
            )
            qids = _search_qids(
                caller(
                    _search_url(f"haswbstatement:{clause}", limit=50),
                    timeout=timeout or _timeout(),
                ),
            )
            for key in group:
                out.setdefault(key, [])
            if not qids:
                continue
            entities = _fetch_entities(qids, fetch=fetch, timeout=timeout)
            for qid, entity in entities.items():
                claims = entity.get("claims") or {}
                for key, pairs in conjunctions.items():
                    if not all(value in _claim_values(claims, pid) for pid, value in pairs):
                        continue
                    out[key].append({
                        "qid": qid,
                        "matched_on": " AND ".join(f"{p}={v}" for p, v in pairs),
                        "label": "",
                    })
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
    answered = {
        key: candidates
        for key, row in cached.items()
        if (candidates := cached_pair_candidates(row)) is not None
    }
    deferred_reasons = {
        key: reason
        for key, row in cached.items()
        if (reason := cached_pair_deferred_reason(row))
    }
    for key, candidates in answered.items():
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

    # A partially cached item must not read as `absent` either (Rule W-144). A key
    # the budget deferred says so, which is a different fact from "never attempted"
    # and the curator is told which (Rule W-160).
    for item in items:
        existence = item.get("_wikidata_existence") or {}
        probed = existence.get("probed") or []
        keys = [(p["pid"], p["value"]) for p in probed]
        capped = [key for key in keys if key in deferred_reasons]
        if capped and existence.get("status") in {STATUS_NOT_PROBED, STATUS_ABSENT}:
            was_answered = existence.get("status") == STATUS_ABSENT
            existence["status"] = STATUS_SKIPPED
            existence["reason"] = "capped"
            existence["note"] = (
                f"{len(capped)} of {len(keys)} keys were deferred by the probe budget "
                f"({deferred_reasons[capped[0]]}) — the key was computed but not "
                "probed; absence of a duplicate is NOT established"
            )
            if was_answered:
                stats["answered"] = max(0, stats["answered"] - 1)
            else:
                stats["not_probed"] = max(0, stats["not_probed"] - 1)
            stats["capped"] = stats.get("capped", 0) + 1
            continue
        if existence.get("status") != STATUS_ABSENT:
            continue
        if any(key not in answered for key in keys):
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
    for item in items:
        stamp_duplicate_check(item)
    return stats


def _pair_summary(pid: str, value: str) -> dict[str, Any]:
    """Cache key for one identifier lookup, shared across every item using it."""
    return {"schema": PROBE_SCHEMA, "pid": pid, "value": value}


ANSWER_DEFERRED = "deferred"


def _pair_result(
    candidates: list[dict[str, str]] | None,
    *,
    deferred_reason: str | None = None,
) -> dict[str, Any]:
    """The cached payload for one key — an answer, or a stated non-answer.

    A key the budget dropped used to leave no row at all, indistinguishable on the
    read path from a key nothing had ever looked at: 29 works reported "no probe
    has run for this item yet" when the truth was "we computed the key and then
    ran out of budget" (Rule W-160).
    """
    if deferred_reason:
        return {"answer": ANSWER_DEFERRED, "candidates": [], "reason": deferred_reason}
    return {"answer": "candidates" if candidates else "absent", "candidates": candidates or []}


def cached_pair_candidates(answer: Any) -> list[dict[str, str]] | None:
    """Candidates from a cached row, or ``None`` when it did not answer.

    Tolerates the pre-`answer` row shape so a warm 7-day cache is not thrown away.
    """
    if not isinstance(answer, dict) or not isinstance(answer.get("candidates"), list):
        return None
    if answer.get("answer") == ANSWER_DEFERRED:
        return None
    return answer["candidates"]


def cached_pair_deferred_reason(answer: Any) -> str | None:
    if isinstance(answer, dict) and answer.get("answer") == ANSWER_DEFERRED:
        return str(answer.get("reason") or "capped")
    return None


async def _read_cached_pairs(
    db_factory: Any,
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Cached rows per identifier. One short transaction, index lookups."""
    if db_factory is None or not pairs:
        return {}
    from app.pipeline.inference_cache import (  # noqa: PLC0415
        canonical_hash,
        read_many_from_inference_cache,
    )

    found: dict[tuple[str, str], dict[str, Any]] = {}
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
        # An explicit empty candidate list is a real cached "absent" — keep it.
        if isinstance(hit, dict) and isinstance(hit.get("candidates"), list):
            found[key] = hit
    return found


async def _write_cached_pairs(
    db_factory: Any,
    results: dict[tuple[str, str], list[dict[str, str]]],
    *,
    deferred: dict[tuple[str, str], str] | None = None,
) -> None:
    if db_factory is None or not (results or deferred):
        return
    from app.pipeline.inference_cache import write_many_to_inference_cache  # noqa: PLC0415

    entries = [
        (_pair_summary(pid, value), _pair_result(candidates))
        for (pid, value), candidates in results.items()
    ]
    entries.extend(
        (_pair_summary(pid, value), _pair_result(None, deferred_reason=reason))
        for (pid, value), reason in (deferred or {}).items()
    )
    try:
        async with db_factory() as db:
            # One upsert statement, one commit — not one per identifier.
            await write_many_to_inference_cache(db, kind=CACHE_KIND, entries=entries)
    except Exception as exc:  # noqa: BLE001
        logger.warning("duplicate probe cache write failed: %s", exc)


async def attach_duplicate_evidence(
    db_factory: Any,
    items: list[dict[str, Any]],
    *,
    fetch: Any = None,
    budget: int | None = None,
    on_progress: Any = None,
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
    answered_keys: set[tuple[str, str]] = set()
    for key, row in cached.items():
        candidates = cached_pair_candidates(row)
        if candidates is None:
            # A deferred row is a MISS, so the next job retries it. Treating it as
            # an answer would let the 7-day cache TTL freeze the cap in place.
            continue
        answered_keys.add(key)
        apply(key, candidates)
    stats["cached"] = len(answered_keys)

    misses = [key for key in pairs if key not in answered_keys]
    # Identifier keys pack many to a request; composite and title keys batch too,
    # but by group (see probe_composites_batch / probe_titles_batch), so they are
    # dispatched by key shape rather than one at a time.
    single_misses = [key for key in misses if "+" not in key[0]]
    title_misses = [key for key in misses if key[0].startswith(_TITLE_PREFIX)]
    composite_misses = [
        key for key in misses
        if "+" in key[0] and not key[0].startswith(_TITLE_PREFIX)
    ]
    total_keys = len(misses)
    fresh: dict[tuple[str, str], list[dict[str, str]]] = {}

    def mark_unavailable(keys: list[tuple[str, str]], exc: Exception) -> None:
        for key in keys:
            for item in pending[key]:
                item["_wikidata_existence"] = {
                    "status": STATUS_UNAVAILABLE,
                    "candidates": [],
                    "error": str(exc),
                    "note": "lookup failed — duplication is UNKNOWN, not ruled out",
                }
                stats["unavailable"] += 1

    for start in range(0, len(single_misses), _BATCH_SIZE):
        chunk = single_misses[start : start + _BATCH_SIZE]
        try:
            hits = await run_in_threadpool(probe_batch, chunk, fetch=fetch)
        except Exception as exc:  # noqa: BLE001 — a probe must never break verify
            logger.warning("duplicate probe batch failed: %s", exc)
            mark_unavailable(chunk, exc)
            continue
        # Every probed identifier in this chunk got an answer, including the
        # absences — caching those is what makes a re-run free.
        for key in chunk:
            fresh[key] = hits.get(key, [])
            apply(key, fresh[key])
        _report(on_progress, len(fresh), total_keys)

    # Title and composite keys, grouped. A group that errors falls back to the
    # per-key residue below rather than losing every key in it.
    unbatchable: list[tuple[str, str]] = []
    for group_keys, resolver, label in (
        (title_misses, probe_titles_batch, "title"),
        (composite_misses, probe_composites_batch, "composite"),
    ):
        if not group_keys:
            continue
        if label == "composite" and not composite_batching_enabled():
            unbatchable.extend(group_keys)
            continue
        try:
            hits = await run_in_threadpool(resolver, group_keys, fetch=fetch)
        except Exception as exc:  # noqa: BLE001 — a probe must never break verify
            logger.warning("duplicate probe %s batch failed: %s", label, exc)
            unbatchable.extend(group_keys)
            continue
        for key in group_keys:
            if key not in hits:
                # The group answered, but not for this key — fall back rather than
                # cache an absence nobody established (Rule W-144).
                unbatchable.append(key)
                continue
            fresh[key] = hits[key]
            apply(key, fresh[key])
        _report(on_progress, len(fresh), total_keys)

    # The residue: groups that errored, plus keys a group did not answer. One
    # request each, bounded, with a circuit breaker so a rate-limited API cannot
    # hold the whole verify job hostage — and interleaved across probe classes so
    # the budget does not always run out on the same class (Rule W-145).
    allowance = _unbatchable_budget()
    ordered = order_unbatchable_fairly(unbatchable)
    classes_present = len({_probe_class(key) for key in ordered})
    per_class = _unbatchable_class_budget(allowance, classes_present)
    trip, consecutive_failures = _rate_limit_trip(), 0
    issued_by_class: dict[str, int] = {}
    deferred: dict[tuple[str, str], str] = {}
    for key in ordered:
        probe_class = _probe_class(key)
        issued = issued_by_class.get(probe_class, 0)
        if consecutive_failures >= trip:
            deferred[key] = "circuit breaker tripped after repeated lookup failures"
            continue
        if issued >= per_class or sum(issued_by_class.values()) >= allowance:
            deferred[key] = (
                f"probe budget: {probe_class} reached its share "
                f"({per_class} of {allowance})"
            )
            continue
        issued_by_class[probe_class] = issued + 1
        resolver = probe_title if key[0].startswith(_TITLE_PREFIX) else probe_composite
        try:
            hits = await run_in_threadpool(resolver, key[0], key[1], fetch=fetch)
        except Exception as exc:  # noqa: BLE001 — a probe must never break verify
            logger.warning("duplicate probe %s failed: %s", key[0], exc)
            consecutive_failures += 1
            for item in pending[key]:
                existence = item["_wikidata_existence"]
                existence.setdefault("errors", []).append(f"{key[0]}: {exc}")
            continue
        consecutive_failures = 0
        fresh[key] = hits
        apply(key, hits)
        _report(on_progress, len(fresh), total_keys)
    if deferred:
        # Rule W-110: a cap the curator cannot see reads as "we checked". The
        # deferred rows make it visible on the read path too (Rule W-160).
        logger.warning(
            "duplicate probe: %s of %s residue keys deferred "
            "(budget %s, per class %s, consecutive failures %s) — those items "
            "report skipped with reason=capped",
            len(deferred), len(ordered), allowance, per_class, consecutive_failures,
        )
        stats["dropped_unbatched"] = len(deferred)
        for key in deferred:
            for item in pending[key]:
                existence = item["_wikidata_existence"]
                existence["reason"] = "capped"

    await _write_cached_pairs(db_factory, fresh, deferred=deferred)

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
    for item in items:
        stamp_duplicate_check(item)
    logger.info("wikidata duplicate probe: %s", stats)
    return stats
