"""Shared Wikibase label/description language hygiene."""

from __future__ import annotations

from collections.abc import Mapping

from converter.rdf.rdf_helpers import clean_marc_label

_UNSUPPORTED_LANGS = frozenset({"und", ""})


def normalize_wikibase_language(lang: str | None) -> str:
    """Map unsupported Wikibase language codes to ``en``."""
    code = str(lang or "").strip().lower()
    if code in _UNSUPPORTED_LANGS:
        return "en"
    return code or "en"


def sanitize_monolingual_map(values: Mapping[str, str]) -> dict[str, str]:
    """Normalize language codes for Wikibase monolingual text fields."""
    out: dict[str, str] = {}
    for lang, value in values.items():
        text = clean_marc_label(str(value or ""))
        if not text:
            continue
        code = str(lang or "").strip().lower()
        if code in _UNSUPPORTED_LANGS:
            code = "en"
        out.setdefault(code, text)
    return out
