"""Shared Wikibase label/description language hygiene."""

from __future__ import annotations

from collections.abc import Mapping

_UNSUPPORTED_LANGS = frozenset({"und", ""})


def normalize_wikibase_language(lang: str | None) -> str:
    """Map unsupported Wikibase language codes to ``en``."""
    code = str(lang or "").strip().lower()
    if code in _UNSUPPORTED_LANGS:
        return "en"
    return code or "en"


def sanitize_monolingual_map(values: Mapping[str, str]) -> dict[str, str]:
    """Normalize language codes for Wikibase monolingual text fields.

    The text passes through verbatim (whitespace only): the exporter
    already ran its MARC hygiene at build time, and re-running
    ``clean_marc_label`` here silently rewrote what was written — it
    strips trailing ``'``/``.``, so a built label like ``…אלחגאג'``
    reached the wiki as ``…אלחגאג`` and the drift check failed on the
    difference forever. Build output is the source of truth; the write
    path publishes it as-is (11 items on run 47e03437).
    """
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
