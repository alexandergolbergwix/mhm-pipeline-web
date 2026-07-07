"""Shared Wikibase label/description language hygiene."""

from __future__ import annotations

from collections.abc import Mapping

_UNSUPPORTED_LANGS = frozenset({"und", ""})


def sanitize_monolingual_map(values: Mapping[str, str]) -> dict[str, str]:
    """Normalize language codes for Wikibase monolingual text fields."""
    out: dict[str, str] = {}
    for lang, value in values.items():
        text = str(value or "").strip()
        if not text:
            continue
        code = str(lang or "").strip().lower()
        if code in _UNSUPPORTED_LANGS:
            code = "en"
        out.setdefault(code, text)
    return out
