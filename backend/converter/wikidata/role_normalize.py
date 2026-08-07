"""Normalize MARC/NER role strings for ROLE_TO_PID lookup."""

from __future__ import annotations

import re

_ROLE_QUOTE_CHARS = '"\'"\u201c\u201d\u2018\u2019\u00ab\u00bb\u2039\u203a\\'


def normalize_marc_role(role: str) -> str:
    """Strip MARC quote/paren wrappers so ``(מעתיק)`` maps to ROLE_TO_PID."""
    text = str(role or "").strip().replace('\\"', '"')
    text = text.strip(_ROLE_QUOTE_CHARS).strip()
    if text.startswith("(") and text.endswith(")") and text.count("(") == 1:
        text = text[1:-1].strip()
    text = text.strip(_ROLE_QUOTE_CHARS).strip()
    text = re.sub(r"\s+", " ", text)
    return text.casefold().replace("_", " ").strip()
