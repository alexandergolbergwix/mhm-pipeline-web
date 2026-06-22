"""Resolve MARC 650/655 labels to Wikidata QIDs for LOD projection.

Resolution order (fail-closed — no QID emitted without a hit):
  1. Static crosswalk (``SUBJECT_TO_QID`` / ``GENRE_TO_QID`` / ``KNOWN_WORK_QIDS``)
  2. Pre-stamped ``wikidata_id`` on the subject/genre row (authority enrichment)
  3. Cached WDQS label lookup (Hebrew @he / English @en, highest sitelinks)
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
from typing import Any

import requests

from converter.rdf.rdf_helpers import clean_marc_label
from converter.transformer.subject_records import subject_term
from converter.wikidata.property_mapping import (
    GENRE_TO_QID,
    KNOWN_WORK_QIDS,
    Q_ILLUMINATED_MANUSCRIPT,
    Q_MANUSCRIPT_FRAGMENT,
    Q_PALIMPSEST,
    SUBJECT_TO_QID,
)

logger = logging.getLogger(__name__)

_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
_USER_AGENT = (
    "MHMPipeline/1.0 (https://github.com/alexandergolbergwix/pipeline; alexandergo@wix.com)"
)
_POSITIVE_TTL_SECONDS = 30 * 24 * 3600
_NEGATIVE_TTL_SECONDS = 24 * 3600
_MAX_LABEL_LEN = 200
_CACHE_VERSION = 1
_CACHE_FILENAME = "wikidata_label_qid_cache.json"

# 655 labels that imply additional P31 classes (WikiProject Manuscripts).
GENRE_LABEL_TO_INSTANCE_QID: dict[str, str] = {
    "Illustrated works (Manuscript)": Q_ILLUMINATED_MANUSCRIPT,
    "illuminated manuscript": Q_ILLUMINATED_MANUSCRIPT,
    "Palimpsests": Q_PALIMPSEST,
    "palimpsest": Q_PALIMPSEST,
    "Manuscript fragments": Q_MANUSCRIPT_FRAGMENT,
    "manuscript fragment": Q_MANUSCRIPT_FRAGMENT,
}

_QID_RE = re.compile(r"^Q\d+$")


def _normalize_qid(raw: str | None) -> str | None:
    if not raw:
        return None
    text = str(raw).strip()
    if text.startswith("http"):
        text = text.rstrip("/").rsplit("/", 1)[-1]
    if _QID_RE.match(text):
        return text
    return None


def _static_subject_qid(term: str) -> str | None:
    cleaned = clean_marc_label(term)
    if not cleaned:
        return None
    for table in (SUBJECT_TO_QID, KNOWN_WORK_QIDS):
        qid = table.get(cleaned)
        if qid:
            return qid
    return None


def _static_genre_qid(term: str) -> str | None:
    cleaned = clean_marc_label(term)
    if not cleaned:
        return None
    return GENRE_TO_QID.get(cleaned)


def instance_qids_from_genre_labels(labels: list[str]) -> list[str]:
    """Extra P31 QIDs implied by MARC 655 genre/form headings."""
    out: list[str] = []
    seen: set[str] = set()
    for label in labels:
        qid = GENRE_LABEL_TO_INSTANCE_QID.get(clean_marc_label(label))
        if qid and qid not in seen:
            seen.add(qid)
            out.append(qid)
    return out


def resolve_subject_qid(
    subj: dict[str, Any],
    *,
    allow_network: bool | None = None,
    cache_path: pathlib.Path | None = None,
) -> str | None:
    """Resolve a topical/person subject row to a Wikidata QID."""
    term = subject_term(subj)
    if not term:
        return None
    stamped = _normalize_qid(subj.get("wikidata_id"))
    if stamped:
        return stamped
    static = _static_subject_qid(term)
    if static:
        return static
    return lookup_qid_by_label(term, allow_network=allow_network, cache_path=cache_path)


def resolve_genre_qid(
    label: str,
    *,
    genre_entry: dict[str, Any] | None = None,
    allow_network: bool | None = None,
    cache_path: pathlib.Path | None = None,
) -> str | None:
    """Resolve a MARC 655 label to a Wikidata QID for P136."""
    term = clean_marc_label(label)
    if not term:
        return None
    if genre_entry:
        stamped = _normalize_qid(genre_entry.get("wikidata_id"))
        if stamped:
            return stamped
    static = _static_genre_qid(term)
    if static:
        return static
    return lookup_qid_by_label(term, allow_network=allow_network, cache_path=cache_path)


def lookup_qid_by_label(
    label: str,
    *,
    timeout_seconds: float = 2.0,
    allow_network: bool | None = None,
    cache_path: pathlib.Path | None = None,
) -> str | None:
    """WDQS lookup: exact label match → QID with highest sitelinks."""
    if not isinstance(label, str):
        return None
    cleaned = clean_marc_label(label.strip())
    if not cleaned or len(cleaned) > _MAX_LABEL_LEN:
        return None

    resolved_cache = _resolve_cache_path(cache_path)
    cache = _load_cache(resolved_cache)
    entry = cache.get("entries", {}).get(cleaned)
    if entry and _entry_is_fresh(entry):
        qid = entry.get("qid")
        return qid if isinstance(qid, str) and _QID_RE.match(qid) else None

    if not _network_allowed(allow_network):
        return None

    qid = _query_qid(cleaned, timeout_seconds=timeout_seconds)
    ttl = _POSITIVE_TTL_SECONDS if qid else _NEGATIVE_TTL_SECONDS
    _store_entry(resolved_cache, cache, cleaned, qid, ttl)
    return qid


def _has_hebrew(text: str) -> bool:
    return any("֐" <= c <= "׿" for c in text)


def _escape_sparql_literal(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
    )


def _build_qid_sparql(label: str) -> str:
    safe = _escape_sparql_literal(label)
    if _has_hebrew(label):
        label_clause = (
            f'{{ ?item rdfs:label "{safe}"@he . }}\n'
            f'UNION {{ ?item skos:altLabel "{safe}"@he . }}'
        )
    else:
        label_clause = (
            f'{{ ?item rdfs:label "{safe}"@en . }}\n'
            f'UNION {{ ?item skos:altLabel "{safe}"@en . }}'
        )
    return (
        "SELECT ?item (COUNT(?sitelink) AS ?count) WHERE {\n"
        f"  {label_clause}\n"
        "  OPTIONAL { ?item ^schema:about ?sitelink . }\n"
        "  FILTER(!ISBLANK(?item))\n"
        "}\n"
        "GROUP BY ?item\n"
        "ORDER BY DESC(?count)\n"
        "LIMIT 1\n"
    )


def _query_qid(label: str, *, timeout_seconds: float) -> str | None:
    sparql = _build_qid_sparql(label)
    try:
        resp = requests.get(
            _SPARQL_ENDPOINT,
            params={"query": sparql, "format": "json"},
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/sparql-results+json",
            },
            timeout=timeout_seconds,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("WDQS label→QID lookup failed: %s", exc)
        return None
    bindings = data.get("results", {}).get("bindings", [])
    if not bindings:
        return None
    item_uri = bindings[0].get("item", {}).get("value", "")
    if not item_uri:
        return None
    qid = item_uri.rstrip("/").rsplit("/", 1)[-1]
    return qid if _QID_RE.match(qid) else None


def _network_allowed(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    no_net = os.environ.get("MHM_NO_NETWORK", "").strip().lower()
    return no_net not in ("1", "true", "yes", "on")


def _resolve_cache_path(explicit: pathlib.Path | None) -> pathlib.Path:
    if explicit is not None:
        return explicit
    try:
        import platformdirs  # noqa: PLC0415

        base = pathlib.Path(platformdirs.user_cache_dir("MHMPipeline"))
    except Exception:  # noqa: BLE001
        base = pathlib.Path.home() / ".cache" / "MHMPipeline"
    return base / _CACHE_FILENAME


def _load_cache(path: pathlib.Path) -> dict[str, Any]:
    empty: dict[str, Any] = {"version": _CACHE_VERSION, "entries": {}}
    try:
        if not path.exists():
            return empty
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except Exception:  # noqa: BLE001
        return empty
    if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION:
        return empty
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    return data


def _entry_is_fresh(entry: dict[str, Any]) -> bool:
    import datetime as _dt

    fetched_at_raw = entry.get("fetched_at")
    ttl_raw = entry.get("ttl_seconds")
    if not isinstance(fetched_at_raw, str) or not isinstance(ttl_raw, int):
        return False
    try:
        if fetched_at_raw.endswith("Z"):
            fetched_at = _dt.datetime.fromisoformat(fetched_at_raw[:-1]).replace(tzinfo=_dt.UTC)
        else:
            fetched_at = _dt.datetime.fromisoformat(fetched_at_raw)
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=_dt.UTC)
    except Exception:  # noqa: BLE001
        return False
    age = (_dt.datetime.now(tz=_dt.UTC) - fetched_at).total_seconds()
    return age < ttl_raw


def _store_entry(
    path: pathlib.Path,
    cache: dict[str, Any],
    label: str,
    qid: str | None,
    ttl_seconds: int,
) -> None:
    import datetime as _dt

    entries = cache.setdefault("entries", {})
    entries[label] = {
        "qid": qid,
        "fetched_at": _dt.datetime.now(tz=_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ttl_seconds": ttl_seconds,
    }
    cache.setdefault("version", _CACHE_VERSION)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            json.dump(cache, fp, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        logger.debug("WDQS QID cache write failed: %s", exc)


def unresolved_topic_labels(subjects: list[dict[str, Any]]) -> list[str]:
    """650 topic terms with no resolvable QID (for description enrichment)."""
    out: list[str] = []
    for subj in subjects:
        if str(subj.get("type") or "") != "topic":
            continue
        if str(subj.get("field") or "") not in ("650", ""):
            continue
        if resolve_subject_qid(subj, allow_network=False):
            continue
        term = subject_term(subj)
        if term:
            out.append(term)
    return out
