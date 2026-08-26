"""Smart Wikidata existence + ownership checks (Action API).

Implements the access map in ``docs/wikidata-data-access.md``:

* Confirm a reconciled QID is still a live item via ``wbgetentities``
  (MediaWiki Action API — preferred for auth'd small batches).
* Classify ownership via first-revision author vs authenticated user
  (same channels as Rule-38 / ``WikidataUploader._is_our_item``).

Never treat a WDQS miss as CREATE permission when the Action API cannot
confirm absence of a ledger/reconcile candidate.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Literal

logger = logging.getLogger(__name__)

Ownership = Literal["own", "foreign", "unknown", "absent"]

_USER_AGENT = "MHM-Pipeline-Web/1.0 (Wikidata Studio; academic research)"
_PROD_API = "https://www.wikidata.org/w/api.php"
_TEST_API = "https://test.wikidata.org/w/api.php"
_WBGETENTITIES_BATCH = 50
_MIN_INTERVAL_SEC = 0.35
_last_api_call_at = 0.0


def _api_base(*, is_test: bool = False) -> str:
    return _TEST_API if is_test else _PROD_API


def _normalize_qid(qid: str) -> str | None:
    clean = str(qid or "").strip()
    if not clean.startswith("Q") or not clean[1:].isdigit():
        return None
    return clean


def _parse_entity_alive(ent: dict) -> bool:
    if ent.get("missing") is not None:
        return False
    return bool(ent.get("id") or ent.get("title"))


def _fetch_json_throttled(url: str, *, timeout: float = 45.0) -> dict:
    """One Action API GET with min interval and polite 429 / maxlag retry."""
    global _last_api_call_at
    delay = 2.0
    for attempt in range(4):
        wait = _MIN_INTERVAL_SEC - (time.monotonic() - _last_api_call_at)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
                "Accept-Encoding": "gzip,deflate",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                _last_api_call_at = time.monotonic()
                raw = resp.read()
                if raw[:2] == b"\x1f\x8b":
                    import gzip  # noqa: PLC0415

                    raw = gzip.decompress(raw)
                payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict) and payload.get("error"):
                code = str((payload.get("error") or {}).get("code") or "")
                if code == "maxlag" and attempt < 3:
                    time.sleep(delay)
                    delay *= 2
                    continue
            return payload
        except urllib.error.HTTPError as exc:
            _last_api_call_at = time.monotonic()
            if exc.code != 429 or attempt == 3:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                sleep_for = float(retry_after) if retry_after else delay
            except (TypeError, ValueError):
                sleep_for = delay
            time.sleep(min(max(sleep_for, 1.0), 30.0))
            delay *= 2
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            OSError,
        ) as exc:
            _last_api_call_at = time.monotonic()
            if attempt == 3:
                raise exc
            time.sleep(delay)
            delay *= 2
    raise urllib.error.URLError("wbgetentities exhausted retries")


def _get_json(url: str, *, timeout: float = 30.0) -> dict:
    return _fetch_json_throttled(url, timeout=timeout)


def confirm_qids_alive(
    qids: list[str],
    *,
    is_test: bool = False,
) -> dict[str, bool | None]:
    """Batch ``wbgetentities`` with throttling; True / False / None per QID."""
    out: dict[str, bool | None] = {}
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in qids:
        qid = _normalize_qid(raw)
        if not qid or qid in seen:
            continue
        seen.add(qid)
        normalized.append(qid)
    for raw in qids:
        qid = str(raw or "").strip()
        if qid and _normalize_qid(qid) is None:
            out[qid] = False

    base = _api_base(is_test=is_test)
    for start in range(0, len(normalized), _WBGETENTITIES_BATCH):
        chunk = normalized[start:start + _WBGETENTITIES_BATCH]
        params = urllib.parse.urlencode({
            "action": "wbgetentities",
            "ids": "|".join(chunk),
            "props": "info",
            "format": "json",
        })
        url = f"{base}?{params}"
        try:
            data = _fetch_json_throttled(url)
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            json.JSONDecodeError,
            OSError,
        ) as exc:
            logger.warning("wbgetentities batch failed (%d ids): %s", len(chunk), exc)
            for qid in chunk:
                out[qid] = None
            continue
        entities = data.get("entities") or {}
        for qid in chunk:
            ent = entities.get(qid) or {}
            if not ent:
                out[qid] = None
            else:
                out[qid] = _parse_entity_alive(ent)
    return out


def fetch_entity_labels(
    qids: list[str],
    *,
    is_test: bool = False,
) -> dict[str, dict[str, str]]:
    """Batch ``wbgetentities`` labels (en/he). Missing QIDs are omitted."""
    out: dict[str, dict[str, str]] = {}
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in qids:
        qid = _normalize_qid(raw)
        if not qid or qid in seen:
            continue
        seen.add(qid)
        normalized.append(qid)
    api = _api_base(is_test=is_test)
    for i in range(0, len(normalized), _WBGETENTITIES_BATCH):
        chunk = normalized[i:i + _WBGETENTITIES_BATCH]
        params = urllib.parse.urlencode({
            "action": "wbgetentities",
            "ids": "|".join(chunk),
            "props": "labels",
            "languages": "en|he",
            "format": "json",
        })
        try:
            payload = _fetch_json_throttled(f"{api}?{params}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("wbgetentities labels failed (%d ids): %s", len(chunk), exc)
            continue
        entities = payload.get("entities") or {}
        if not isinstance(entities, dict):
            continue
        for qid, ent in entities.items():
            if not isinstance(ent, dict) or ent.get("missing") is not None:
                continue
            labels = ent.get("labels") or {}
            row: dict[str, str] = {}
            if isinstance(labels, dict):
                for lang in ("en", "he"):
                    cell = labels.get(lang) or {}
                    val = cell.get("value") if isinstance(cell, dict) else cell
                    text = str(val or "").strip()
                    if text:
                        row[lang] = text
            if row:
                out[str(qid)] = row
    return out


def fetch_entity_p31(
    qids: list[str],
    *,
    is_test: bool = False,
) -> dict[str, list[str]]:
    """Batch ``wbgetentities`` P31 values. Missing QIDs are omitted."""
    out: dict[str, list[str]] = {}
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in qids:
        qid = _normalize_qid(raw)
        if not qid or qid in seen:
            continue
        seen.add(qid)
        normalized.append(qid)
    api = _api_base(is_test=is_test)
    for i in range(0, len(normalized), _WBGETENTITIES_BATCH):
        chunk = normalized[i:i + _WBGETENTITIES_BATCH]
        params = urllib.parse.urlencode({
            "action": "wbgetentities",
            "ids": "|".join(chunk),
            "props": "claims",
            "format": "json",
        })
        try:
            payload = _fetch_json_throttled(f"{api}?{params}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("wbgetentities P31 failed (%d ids): %s", len(chunk), exc)
            continue
        entities = payload.get("entities") or {}
        if not isinstance(entities, dict):
            continue
        from app.pipeline.wikidata_live_native_hygiene import (  # noqa: PLC0415
            live_p31_from_entity,
        )
        for qid, ent in entities.items():
            if not isinstance(ent, dict) or ent.get("missing") is not None:
                continue
            p31 = live_p31_from_entity(ent)
            if p31:
                out[str(qid)] = p31
    return out


def confirm_qid_alive(
    qid: str,
    *,
    is_test: bool = False,
    retries: int = 3,
) -> bool | None:
    """Return True if *qid* exists, False if missing, None if lookup failed."""
    clean = _normalize_qid(qid)
    if clean is None:
        return False
    delay = 1.0
    for attempt in range(max(1, retries)):
        result = confirm_qids_alive([clean], is_test=is_test).get(clean)
        if result is not None:
            return result
        if attempt < retries - 1:
            time.sleep(delay)
            delay = min(delay * 2, 8.0)
    return None


def classify_ownership_with_uploader(uploader: object, qid: str) -> Ownership:
    """Classify QID ownership using an authenticated ``WikidataUploader``.

    Prefer this path when a curator token is available — it reuses Rule-38's
    triple-verification channels.
    """
    clean = str(qid or "").strip()
    if not clean:
        return "absent"
    is_our = getattr(uploader, "_is_our_item", None)
    if not callable(is_our):
        return "unknown"
    try:
        return "own" if bool(is_our(clean)) else "foreign"
    except Exception as exc:  # noqa: BLE001
        logger.warning("ownership classify failed for %s: %s", clean, exc)
        return "unknown"


def accept_allows_foreign_modify(
    *,
    existing_qid: str,
    accept_foreign_modify: bool,
    accepted_foreign_qid: str | None,
) -> bool:
    """True only when the curator explicitly accepted *this* reconciled QID."""
    if not accept_foreign_modify:
        return False
    wanted = str(accepted_foreign_qid or "").strip()
    return bool(wanted) and wanted == str(existing_qid).strip()
