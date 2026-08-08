"""ISBD title / subtitle split for MARC 245 (any language).

When ``245$b`` is absent but ``245$a`` carries ``main : remainder``, emit
P1476=main and P1680=remainder. Language-agnostic; never substitutes shelfmark.
"""

from __future__ import annotations

import re

_QUOTE_CHARS = "\"'”“„׳״"


def split_isbd_title_subtitle(
    title_a: str | None,
    title_b: str | None = None,
) -> tuple[str, str]:
    """Return ``(title, subtitle)`` from MARC 245 ``$a`` / ``$b``.

    Rules:
    1. Non-empty ``$b`` that is not a duplicate of ``$a`` → title=``$a``,
       subtitle=``$b``.
    2. Else split ``$a`` (or the duplicated full string) once on an ISBD colon
       break outside quotes.
    3. Else subtitle is empty.
    """
    raw_a = str(title_a or "").strip().strip(_QUOTE_CHARS)
    raw_b = str(title_b or "").strip().strip(_QUOTE_CHARS + " /:")

    if raw_b and raw_a and raw_a.casefold() != raw_b.casefold():
        return raw_a.rstrip(" :./,"), raw_b

    source = raw_a or raw_b
    if not source:
        return "", ""
    return _split_colon_title(source)


def _split_colon_title(text: str) -> tuple[str, str]:
    """Split on the first ISBD colon not inside quotation marks."""
    cleaned = re.sub(r"\s+", " ", text.strip())
    if ":" not in cleaned:
        return cleaned.rstrip(" .,;:/-"), ""

    in_quotes = False
    quote_chars = set(_QUOTE_CHARS)
    for idx, ch in enumerate(cleaned):
        if ch in quote_chars:
            in_quotes = not in_quotes
            continue
        if in_quotes or ch != ":":
            continue
        left = cleaned[:idx].rstrip()
        right = cleaned[idx + 1 :].lstrip()
        if left and right:
            return left.rstrip(" .,;:/-"), right.rstrip(" .,;:/-")
        break
    return cleaned.rstrip(" .,;:/-"), ""
