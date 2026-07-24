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
import urllib.error
import urllib.parse
import urllib.request
from typing import Literal

logger = logging.getLogger(__name__)

Ownership = Literal["own", "foreign", "unknown", "absent"]

_USER_AGENT = "MHM-Pipeline-Web/1.0 (Wikidata Studio; academic research)"
_PROD_API = "https://www.wikidata.org/w/api.php"
_TEST_API = "https://test.wikidata.org/w/api.php"


def _api_base(*, is_test: bool = False) -> str:
    return _TEST_API if is_test else _PROD_API


def _get_json(url: str, *, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip,deflate",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        # urllib may already decompress gzip depending on build; tolerate both.
        if raw[:2] == b"\x1f\x8b":
            import gzip  # noqa: PLC0415

            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))


def confirm_qid_alive(qid: str, *, is_test: bool = False) -> bool | None:
    """Return True if *qid* exists, False if missing, None if lookup failed.

    Uses Action API ``wbgetentities`` (Wikidata:Data_access — entity JSON for
    known QIDs in small batches).
    """
    clean = str(qid or "").strip()
    if not clean.startswith("Q") or not clean[1:].isdigit():
        return False
    params = urllib.parse.urlencode({
        "action": "wbgetentities",
        "ids": clean,
        "props": "info",
        "format": "json",
    })
    url = f"{_api_base(is_test=is_test)}?{params}"
    try:
        data = _get_json(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.warning("wbgetentities failed for %s: %s", clean, exc)
        return None
    entities = data.get("entities") or {}
    ent = entities.get(clean) or {}
    if ent.get("missing") is not None:
        return False
    # Redirects still have an id; treat as alive (reconcile target is valid).
    return bool(ent.get("id") or ent.get("title"))


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
