"""Distinguish an explicit catalogue code from bibliographic source text."""

import re
from collections.abc import Mapping


def catalog_reference_code(reference: object) -> str:
    if not isinstance(reference, Mapping):
        return ""
    return str(reference.get("number") or reference.get("catalog_code") or "").strip()


def catalog_reference_text(reference: object) -> str:
    if not isinstance(reference, Mapping):
        return str(reference or "").strip()
    text = str(reference.get("text") or "").strip()
    if text:
        return text
    name = str(reference.get("catalog") or reference.get("name") or "").strip()
    code = catalog_reference_code(reference)
    return f"{name}: {code}" if name and code else name or code


def catalog_reference_qid(reference: object) -> str:
    if not isinstance(reference, Mapping):
        return ""
    qid = str(reference.get("catalog_qid") or "").strip()
    return qid if re.fullmatch(r"Q[1-9][0-9]*", qid) else ""
