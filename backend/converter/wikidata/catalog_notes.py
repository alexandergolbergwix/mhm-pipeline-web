"""Catalog / workflow note markers that must never become public Wikidata claims."""

from __future__ import annotations

import re


def is_catalog_note_placeholder(value: object) -> bool:
    """True for NLI catalog workflow text, not manuscript inscription text.

    Values such as ``רשומה זמנית`` ("temporary record") arrive in
    ``colophon_text`` / note fields after MARC merge. Keeping them in
    source evidence is fine; projecting them as P1684 inscription would
    be a false scholarly claim (Rule W-72).
    """
    text = str(value or "").strip().strip("\"'”“„׳״")
    if not text:
        return False
    folded = text.casefold()
    if folded in {
        "רשומה זמנית",
        "temporary record",
        "temporary entry",
        "תאור זמני",
    }:
        return True
    # Compound catalog rows: ``רשומה זמנית | נושא נוסף: …`` (Rule W-170).
    if "רשומה זמנית" in folded or "temporary record" in folded:
        return True
    return bool(
        re.match(r"^(?:נושא נוסף|additional subject|catalog(?:ue|ing)? note)\s*[:：]", folded)
        or "book suggested to google" in folded
        or ("catalog" in folded and "rejected" in folded)
    )
