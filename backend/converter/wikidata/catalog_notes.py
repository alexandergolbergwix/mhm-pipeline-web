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
    # Scholarly catalog attribution / bibliography — not text written on the
    # object (export-33 P1684 false colophons / Rule W-172).
    if (
        "לפי דעת" in folded
        or "מיוסד על" in folded
        or "according to" in folded
        or re.search(r"(?:^|[\s|])(?:וראה|ראה)\s*:", text)
        or re.search(r"\b(?:see|cf\.)\s*:", folded)
    ):
        return True
    # Third-person catalog prose ABOUT a colophon — the scribe never names
    # his own colophon, so the word itself marks cataloger apparatus
    # (בסופו קולופון / קולופון המחבר בדף … / (קולופון 4).
    if "קולופון" in text:
        return True
    # Workflow / access prose: photocopy permissions and legal custody notes
    # (רשות לצלם ניתנה … / הוצא מהספרייה מתוקף צו בית משפט).
    if (
        "רשות לצלם" in text
        or "הוצא מהספרייה" in text
        or "צו בית משפט" in text
        or "מתוקף צו" in text
    ):
        return True
    # Catalog pointers (קיטלוג מפורט יותר, ראה באוסף הקיטלוגים).
    if "קיטלוג" in folded or "באוסף הקיטלוגים" in folded:
        return True
    # Third-person cross-reference prose (המחבר העתיק את כתב-יד …) —
    # a real inscription is first-person.
    if "המחבר העתיק" in folded or "המעתיק העתיק" in folded:
        return True
    # Scholarly-comparison bibliography prose (השווה מהדורת … / וראה שם
    # בעמ' … / על כתב-יד זה ראה …) — apparatus, not object text.
    if (
        "השווה מהדורת" in folded
        or "וראה שם" in folded
        or "על כתב-יד זה ראה" in folded
    ):
        return True
    return bool(
        re.match(r"^(?:נושא נוסף|additional subject|catalog(?:ue|ing)? note)\s*[:：]", folded)
        or "book suggested to google" in folded
        or ("catalog" in folded and "rejected" in folded)
        or re.match(r"^(?:הערה|note|internal note|catalog(?:ue)?)\s*[:：]", folded)
        or "רשומה נדחתה" in folded
        or "record rejected" in folded
        or folded.startswith("נושא נוסף")
    )


def is_incipit_text(value: object) -> bool:
    """True when *value* may become P1922 (first line), not a catalog note."""
    text = str(value or "").strip()
    if not text or text == "None":
        return False
    if is_catalog_note_placeholder(text):
        return False
    # Catalog chronology / folio apparatus is not an incipit (export-34 / W-173).
    # Example: ``בשנת תכ"א. בעמוד 542 דף השלמה…`` landed on P1922.
    folded = text.casefold()
    if re.match(r"^בשנת\b", text) or re.match(r"^in the year\b", folded):
        return False
    if re.search(r"(?:^|[\s.])בעמוד\s+\d+", text) and not re.search(
        r"^[\"'«]?[\u0590-\u05ff]{3,}", text,
    ):
        return False
    if re.match(r"^(?:f\.|fol\.|folio|p\.|page|עמ['׳]?)\s*\d+", folded):
        return False
    return True
