"""Work-title normalization and variant generation for Mazal work matching."""
from __future__ import annotations

import re
from typing import Any

_PREFIX_RE = re.compile(
    r"^(?:תפסיל|חלק|פרק|מאת|על|ב|ל|מ|ה|ו|כולל)\s+",
    re.IGNORECASE,
)
_ISBD_TRAIL_RE = re.compile(r"[.,;:\s]+$")


def normalize_work_title_for_match(title: str) -> str:
    """Strip catalog wrappers and ISBD punctuation for authority lookup."""
    t = (title or "").strip()
    if not t:
        return ""
    prev = None
    while prev != t:
        prev = t
        t = _PREFIX_RE.sub("", t).strip()
    t = _ISBD_TRAIL_RE.sub("", t).strip()
    return " ".join(t.split())


def work_title_variants(title: str, marc_record: dict[str, Any] | None = None) -> list[str]:
    """Ordered unique variants to try against Mazal (raw → normalized → note titles)."""
    seen: set[str] = set()
    out: list[str] = []

    def _add(v: str) -> None:
        v = v.strip()
        if len(v) < 3:
            return
        key = v.casefold()
        if key in seen:
            return
        seen.add(key)
        out.append(v)

    _add(title)
    norm = normalize_work_title_for_match(title)
    if norm:
        _add(norm)
    if marc_record:
        for wm in marc_record.get("work_mentions") or []:
            if isinstance(wm, dict):
                wt = str(wm.get("title") or "").strip()
                if wt:
                    _add(wt)
                    n = normalize_work_title_for_match(wt)
                    if n:
                        _add(n)
    return out
