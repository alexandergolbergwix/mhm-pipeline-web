"""Distinguish an explicit catalogue code from bibliographic source text."""

import re
from collections.abc import Mapping
from urllib.parse import urlsplit


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


def structured_catalog_reference(reference: object) -> dict[str, object]:
    """Retain citation text and candidate details without asserting identity."""
    result = dict(reference) if isinstance(reference, Mapping) else {"text": str(reference or "")}
    text = catalog_reference_text(reference)
    result["text"] = text
    result["original_citation"] = str(result.get("original_citation") or text)
    result.setdefault("source_field", "581")
    years = sorted(set(re.findall(r"(?<!\d)(?:1[5-9]\d{2}|20\d{2})(?!\d)", text)))
    explicit = result.get("year")
    if explicit is not None:
        if type(explicit) is not int or not 1000 <= explicit <= 2099:
            raise ValueError("A bibliographic year must be an explicit supported integer.")
    elif len(years) == 1:
        result["candidate_year"] = int(years[0])
    pages = re.search(r"(?:\bpp?\.|\bpages?\b|עמ(?:וד(?:ים)?)?[׳']?)\s*([0-9]+(?:\s*[-–]\s*[0-9]+)?)", text, re.IGNORECASE)
    if pages and not result.get("pages"):
        result["candidate_pages"] = pages.group(1)
    for key in ("url", "evidence_uri"):
        value = result.get(key)
        if value:
            parsed = urlsplit(str(value))
            if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("A bibliography URL must be an HTTP URL without credentials.")
    result["review_status"] = "needs_identity_review"
    # An upload cannot declare its own identity proof. A validated choice sets
    # this value later, after the current public entity has been checked.
    result.pop("identity_verified", None)
    result.pop("identity_proof", None)
    return result
