"""PDF rendering for the research-agent export_pdf skill.

fpdf2 is pure Python. DejaVu Sans (present on the Heroku stack and most
Linux images) covers Hebrew; when no Unicode font is found the PDF falls
back to the core helvetica font and non-Latin-1 characters are replaced —
an export must never fail because of typography.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansBook.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)

_MAX_CHARS = 60000


def _find_unicode_font() -> str | None:
    for path in _FONT_CANDIDATES:
        try:
            if os.path.exists(path):
                return path
        except OSError:
            continue
    return None


def _strip_markdown(text: str) -> str:
    text = re.sub(r"```[a-z]*\n?", "", text)
    text = re.sub(r"\*\*?([^*\n]+)\*\*?", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return text


def _latin1_safe(text: str) -> str:
    return text.encode("latin-1", "replace").decode("latin-1")


def render_pdf(title: str, body: str) -> bytes:
    from fpdf import FPDF

    body = _strip_markdown(body or "")[:_MAX_CHARS]
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    font_path = _find_unicode_font()
    unicode_font = False
    if font_path:
        try:
            pdf.add_font("uni", "", font_path)
            pdf.add_font("uni", "B", font_path)
            unicode_font = True
        except Exception as exc:  # pragma: no cover - corrupt font file
            logger.warning("PDF font load failed (%s): %s", font_path, exc)

    if unicode_font:
        pdf.set_font("uni", "B", 15)
        pdf.multi_cell(0, 9, title)
        pdf.ln(2)
        pdf.set_font("uni", "", 10.5)
        paragraphs = body.splitlines()
    else:
        pdf.set_font("helvetica", "B", 15)
        pdf.multi_cell(0, 9, _latin1_safe(title))
        pdf.ln(2)
        pdf.set_font("helvetica", "", 10.5)
        paragraphs = _latin1_safe(body).splitlines()

    for paragraph in paragraphs:
        if not paragraph.strip():
            pdf.ln(2.5)
            continue
        pdf.multi_cell(0, 5.5, paragraph)
    return bytes(pdf.output())