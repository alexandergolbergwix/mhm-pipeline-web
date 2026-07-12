"""MARC ingest — uses desktop's parsers when possible, falls back to a
straightforward JSON/CSV/TSV reader.

Supported uploads:

* ``.json`` / ``.jsonl`` — already-parsed MARC dicts (e.g. desktop's
  ``marc_extracted.json``).
* ``.mrc`` / ``.marc`` — binary MARC21, parsed via desktop's
  ``converter.parser.unified_reader.UnifiedReader`` so every MARC Parsing
  field handler runs (extract_all_data → identical to the desktop run).
* ``.tsv`` / ``.csv`` — tabular, one record per row, headers recognised
  case-insensitively (control_number, title, authors, contributors,
  subjects). Multi-value cells split on ``|`` / ``;``.
"""

from __future__ import annotations

import csv
import io
import json
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Tabular column header → internal slot.
_CN_HEADERS = (
    "_control_number", "control_number", "controlnumber", "001", "id", "nli_id",
)
_TITLE_HEADERS = ("title", "245a", "245", "main_title")
_AUTHOR_HEADERS = ("author", "authors", "100a", "100")
_CONTRIBUTOR_HEADERS = ("contributor", "contributors", "700a", "700")
_SUBJECT_HEADERS = ("subject", "subjects", "600", "650")
_MULTI_DELIMS = ("|", ";", "<--SEP-->")


def parse_marc_upload(raw: bytes, *, filename: str | None = None) -> list[dict[str, Any]]:
    if not raw:
        raise ValueError("Upload is empty")

    suffix = _suffix(filename)
    if suffix in ("json", "jsonl"):
        return _parse_json_like(raw, jsonl=(suffix == "jsonl"))
    if suffix in ("mrc", "marc"):
        return _parse_mrc_via_desktop(raw)
    if suffix == "tsv":
        return _parse_table(raw, delimiter="\t")
    if suffix == "csv":
        return _parse_table(raw, delimiter=",")

    head = raw.lstrip()[:200]
    if head.startswith(b"{") or head.startswith(b"["):
        return _parse_json_like(raw, jsonl=False)
    if len(head) >= 5 and head[:5].isdigit():
        try:
            return _parse_mrc_via_desktop(raw)
        except Exception:
            pass
    text = _decode(raw)
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    if "\t" in first:
        return _parse_table(raw, delimiter="\t")
    if "," in first:
        return _parse_table(raw, delimiter=",")
    return _parse_json_like(raw, jsonl=True)


# ── desktop-driven .mrc path ─────────────────────────────────────────────


def _parse_mrc_via_desktop(raw: bytes) -> list[dict[str, Any]]:
    """Use desktop's ``UnifiedReader`` + ``extract_all_data`` so every
    MARC Parsing field handler the desktop ships with runs here too."""
    from converter.parser.unified_reader import UnifiedReader  # noqa: PLC0415
    from converter.transformer.field_handlers import extract_all_data  # noqa: PLC0415

    with tempfile.NamedTemporaryFile(suffix=".mrc", delete=False) as f:
        f.write(raw)
        tmp = Path(f.name)
    try:
        reader = UnifiedReader(str(tmp))
        out: list[dict[str, Any]] = []
        for marc_record in reader:
            extracted = extract_all_data(marc_record)
            # ExtractedData is a dataclass with .to_dict() in desktop;
            # fall back to vars() if absent.
            if hasattr(extracted, "to_dict"):
                rec = extracted.to_dict()
            else:
                rec = dict(vars(extracted))
            rec.setdefault("_control_number", rec.get("control_number") or rec.get("001") or "")
            out.append(rec)
        return _normalise_records(out)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# ── JSON / JSONL ────────────────────────────────────────────────────────


def _parse_json_like(raw: bytes, *, jsonl: bool) -> list[dict[str, Any]]:
    text = _decode(raw).strip()
    if not text:
        raise ValueError("Upload is empty")
    if jsonl:
        return _normalise_records(_iter_jsonl(text))
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _normalise_records(_iter_jsonl(text))
    if isinstance(parsed, list):
        return _normalise_records(parsed)
    if isinstance(parsed, dict):
        return _normalise_records([parsed])
    raise ValueError("Top-level JSON must be an object or an array of objects.")


def _iter_jsonl(text: str) -> Iterable[dict[str, Any]]:
    for ln, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Line {ln}: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Line {ln}: each row must be a JSON object")
        yield row


# ── Tabular ──────────────────────────────────────────────────────────────


def _parse_table(raw: bytes, *, delimiter: str) -> list[dict[str, Any]]:
    text = _decode(raw)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if reader.fieldnames is None:
        raise ValueError("Table has no header row.")
    headers = {h: _header_norm(h) for h in reader.fieldnames if h is not None}
    out: list[dict[str, Any]] = []
    for row in reader:
        rec: dict[str, Any] = {}
        for raw_header, value in row.items():
            if raw_header is None or value is None:
                continue
            norm = headers.get(raw_header, _header_norm(raw_header))
            value = value.strip()
            if not value:
                continue
            if norm in _CN_HEADERS:
                rec["_control_number"] = value.strip('"')
            elif norm in _TITLE_HEADERS:
                rec["title"] = value
            elif norm in _AUTHOR_HEADERS:
                rec["authors"] = _split_multi(value)
            elif norm in _CONTRIBUTOR_HEADERS:
                rec["contributors"] = [
                    {"name": v, "role": "contributor", "field": "700"}
                    for v in _split_multi(value)
                ]
            elif norm in _SUBJECT_HEADERS:
                rec["subjects"] = [
                    {"name": v, "type": "topic"} for v in _split_multi(value)
                ]
            else:
                rec[norm] = value
        if rec:
            out.append(rec)
    return _normalise_records(out)


def _header_norm(s: str) -> str:
    return (
        s.strip().lower().replace(" ", "_").replace("-", "_").lstrip("﻿")
    )


def _split_multi(value: str) -> list[str]:
    for delim in _MULTI_DELIMS:
        if delim in value:
            return [v.strip() for v in value.split(delim) if v.strip()]
    return [value]


# ── shared helpers ──────────────────────────────────────────────────────


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8-sig", errors="replace")


def _suffix(filename: str | None) -> str:
    if not filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _normalise_records(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        cn = (
            row.get("_control_number")
            or row.get("control_number")
            or row.get("001")
            or row.get("id")
        )
        if not cn:
            cn = f"row{i:04d}"
        record = dict(row)
        record["_control_number"] = str(cn).strip('"')
        # If the record carries raw MARC subfield keys (``100$a``,
        # ``700$a``, ``600$a``, …) — typical of NLI-style TSV exports —
        # collapse them into the normalised authors/contributors/subjects
        # shape the entity extractor + the desktop item builder expect.
        if any("$" in k for k in row):
            _collapse_marc_subfields(record)
        out.append(record)
    return out


def _dates_from_260_264(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return parsed production dates from MARC 260/264 $c, or ``None``."""
    from converter.transformer.field_handlers import FieldHandlers  # noqa: PLC0415
    from converter.transformer.hebrew_gregorian_calendar import (
        enrich_dates_with_calendar_fields,  # noqa: PLC0415
    )

    for tag in ("260", "264"):
        for piece in _split_multi(_str(record.get(f"{tag}$c"))):
            piece = piece.strip().strip("[].")
            if piece:
                parsed = enrich_dates_with_calendar_fields(
                    FieldHandlers._parse_date_string(piece)
                )
                if parsed:
                    return parsed
    return None


def _collapse_marc_subfields(record: dict[str, Any]) -> None:
    """In-place normalisation: collapses raw ``<tag>$<sub>`` subfield
    columns (typical of NLI-style TSV / JSON exports) into the flat
    keys the rest of the web pipeline expects.

    Derived keys:

    * ``title``       — ``245$a`` (+ optional ``$b``)
    * ``authors``     — ``100/110/111$a`` with ``$e`` roles and ``$d`` dates
    * ``contributors``— ``700/710/711/800/810/811$a`` with ``$e`` / ``$d``
    * ``subjects``    — ``600/610/611/650/651$a`` (``600$d`` biographical dates)
    * ``dates``         — ``260/264$c`` production date (never 008)
    * ``colophon_year`` — year parsed from ``590/500$a`` colophon text
    * ``provenance_events`` — custody dates from ``541$d``, ``583$c``
    * ``genres``      — ``655$a``
    * ``notes``       — ``500/590/541$a`` (general/local/source notes —
                        the AI-Extraction Person-NER input)
    * ``provenance``  — ``561$a`` (the Provenance-NER input)
    * ``contents``    — ``505$a`` split on ``--`` into title chunks
                        (the Contents-NER input + work-item driver)
    * ``colophon_text``— ``590$a`` (local notes — desktop convention)

    Date-source contract: ``marc_date_sources.py``.

    Multi-value subfields are pipe-separated. Roles travel through
    ``$e`` parallel arrays where present.

    **Why every NER-input key matters:** the Stage-2 extractor reads
    these flat keys (``extraction.py:_extract_person_texts`` /
    ``provenance`` branch / ``contents`` branch). When they're empty,
    Modal is called with empty strings → 0 entities → 0 work items
    in the Studio. (Smoking gun, 2026-06-02.)
    """
    # ── Title ────────────────────────────────────────────────────────
    title_a = _str(record.get("245$a"))
    title_b = _str(record.get("245$b"))
    if title_a and not record.get("title"):
        record["title"] = (title_a + (f" {title_b}" if title_b else "")).strip(" :./,")

    # ── Authors (MARC 100, 110, 111) ────────────────────────────────
    authors = list(record.get("authors") or [])
    for tag in ("100", "110", "111"):
        a = _split_multi(_str(record.get(f"{tag}$a")))
        e = _split_multi(_str(record.get(f"{tag}$e")))
        # MARC $d carries birth/death dates — used by Mazal homonym resolution.
        d = _split_multi(_str(record.get(f"{tag}$d")))
        for i, name in enumerate(a):
            name = name.strip()
            if not name:
                continue
            role = e[i] if i < len(e) else "author"
            entry: dict[str, Any] = {
                "name": normalize_entity_text(name),
                "role": normalize_role(role),
                "field": tag,
            }
            if i < len(d) and d[i].strip():
                entry["dates"] = d[i].strip()
            authors.append(entry)
    if authors:
        record["authors"] = authors

    # ── Contributors (700, 710, 711, 800, 810, 811) ─────────────────
    contributors = list(record.get("contributors") or [])
    for tag in ("700", "710", "711", "800", "810", "811"):
        a = _split_multi(_str(record.get(f"{tag}$a")))
        e = _split_multi(_str(record.get(f"{tag}$e")))
        d = _split_multi(_str(record.get(f"{tag}$d")))
        for i, name in enumerate(a):
            name = name.strip()
            if not name:
                continue
            role = e[i] if i < len(e) else "contributor"
            entry = {
                "name": normalize_entity_text(name),
                "role": normalize_role(role),
                "field": tag,
            }
            if i < len(d) and d[i].strip():
                entry["dates"] = d[i].strip()
            contributors.append(entry)
    if contributors:
        record["contributors"] = contributors

    # ── Subjects ─────────────────────────────────────────────────────
    subjects = list(record.get("subjects") or [])
    # 600 = personal-name subject; 610 = corporate; 611 = meeting
    for tag, kind in (("600", "person"), ("610", "organization"), ("611", "meeting")):
        a_vals = _split_multi(_str(record.get(f"{tag}$a")))
        if not a_vals:
            continue
        d_vals = _split_multi(_str(record.get(f"{tag}$d")))
        auth_vals = _split_multi(_str(record.get(f"{tag}$0")))
        src_vals = _split_multi(_str(record.get(f"{tag}$2")))
        for i, name in enumerate(a_vals):
            name = name.strip()
            if not name:
                continue
            entry: dict[str, Any] = {"name": name, "type": kind, "field": tag}
            if i < len(d_vals) and d_vals[i].strip():
                entry["dates"] = d_vals[i].strip()
            if i < len(auth_vals) and auth_vals[i].strip():
                entry["authority_id"] = auth_vals[i].strip()
            if i < len(src_vals) and src_vals[i].strip():
                entry["source"] = src_vals[i].strip()
            subjects.append(entry)
    # 650 topical, 651 geographic
    auth_650 = _split_multi(_str(record.get("650$0")))
    src_650 = _split_multi(_str(record.get("650$2")))
    for i, name in enumerate(_split_multi(_str(record.get("650$a")))):
        name = name.strip()
        if not name:
            continue
        entry = {"name": name, "type": "topic", "field": "650"}
        if i < len(auth_650) and auth_650[i].strip():
            entry["authority_id"] = auth_650[i].strip()
        if i < len(src_650) and src_650[i].strip():
            entry["source"] = src_650[i].strip()
        subjects.append(entry)
    auth_651 = _split_multi(_str(record.get("651$0")))
    src_651 = _split_multi(_str(record.get("651$2")))
    for i, name in enumerate(_split_multi(_str(record.get("651$a")))):
        name = name.strip()
        if not name:
            continue
        entry = {"name": name, "type": "place", "field": "651"}
        if i < len(auth_651) and auth_651[i].strip():
            entry["authority_id"] = auth_651[i].strip()
        if i < len(src_651) and src_651[i].strip():
            entry["source"] = src_651[i].strip()
        subjects.append(entry)
    if subjects:
        from converter.transformer.subject_records import normalize_subjects_list  # noqa: PLC0415

        record["subjects"] = normalize_subjects_list(subjects)

    # ── Production place from 260/264 (desktop handle_260_264 parity) ───
    if not record.get("place"):
        for tag in ("260", "264"):
            place_a = _str(record.get(f"{tag}$a")).strip().strip('"')
            if place_a:
                record["place"] = place_a.rstrip(" :;,")
                break

    # ── Production / related places (MARC 751 Added Entry—Geographic Name) ─
    # NLI uses 751 $a (place name) + $e (relationship, e.g. "place of writing"
    # or "related place") for manuscript geography.  Pipe-separated when repeated.
    _PRODUCTION_ROLES = frozenset(
        (
            "place of writing",
            "place of origin",
            "production place",
            "place of creation",
            "related place",
            "origin",
            "written",
        )
    )
    related_places: list[str] = list(record.get("related_places") or [])
    for place_name, role_text in zip(
        _split_multi(_str(record.get("751$a"))),
        _split_multi(_str(record.get("751$e"))) + [""] * 999,
    ):
        place_name = place_name.strip().strip('"')
        if not place_name:
            continue
        if place_name not in related_places:
            related_places.append(place_name)
        if role_text.strip().strip('"').lower() in _PRODUCTION_ROLES:
            if not record.get("place"):
                record["place"] = place_name
    if related_places:
        record["related_places"] = related_places
    # When NLI omits $e or uses an unlisted role, still promote the first 751$a
    # to production place if nothing else filled the slot.
    if not record.get("place") and related_places:
        record["place"] = related_places[0]

    # ── Dates (260/264 $c — never 008, which is catalog-entry metadata) ─
    if not record.get("dates"):
        parsed = _dates_from_260_264(record)
        if parsed:
            record["dates"] = parsed

    # ── Genre/form ──────────────────────────────────────────────────
    genre_entries: list[dict[str, Any]] = []
    for existing in record.get("genre_entries") or []:
        if isinstance(existing, dict):
            genre_entries.append(dict(existing))
    for existing in record.get("genres") or []:
        if isinstance(existing, dict):
            genre_entries.append(dict(existing))
    auth_655 = _split_multi(_str(record.get("655$0")))
    src_655 = _split_multi(_str(record.get("655$2")))
    for i, name in enumerate(_split_multi(_str(record.get("655$a")))):
        name = name.strip()
        if not name:
            continue
        entry: dict[str, Any] = {"name": name, "type": "genre", "field": "655"}
        if i < len(auth_655) and auth_655[i].strip():
            entry["authority_id"] = auth_655[i].strip()
        if i < len(src_655) and src_655[i].strip():
            entry["source"] = src_655[i].strip()
        genre_entries.append(entry)
    if genre_entries:
        from converter.transformer.subject_records import normalize_genre_entries  # noqa: PLC0415

        flat, normalized = normalize_genre_entries([], genre_entries=genre_entries)
        record["genres"] = flat
        if normalized:
            record["genre_entries"] = normalized

    # ── Notes (500$a general note, 590$a local note, 541$a source) ─
    notes: list[str] = list(record.get("notes") or [])
    for tag in ("500", "590", "541"):
        for piece in _split_multi(_str(record.get(f"{tag}$a"))):
            if piece and piece not in notes:
                notes.append(piece)
    if notes:
        record["notes"] = notes

    # ── Colophon text (590$a + keyword-detected 500$a) ──────────────
    # Some catalogues file the colophon as a 590$a local note. Others
    # use a plain 500$a with a keyword marker (קולופון / colophon / כתב
    # יד סופר). Detect both so the Hebrew-Person-NER sees the scribe's
    # name and the structured extractor below can pull year + scribe.
    _COLOPHON_KEYWORDS = ("קולופון", "colophon", "כתב יד סופר", "כתב-יד")
    colophon_pieces: list[str] = []
    for piece in _split_multi(_str(record.get("590$a"))):
        if piece:
            colophon_pieces.append(piece)
    # 500$a keyword detection
    for piece in _split_multi(_str(record.get("500$a"))):
        if not piece:
            continue
        lower = piece.lower()
        if any(kw in lower for kw in _COLOPHON_KEYWORDS):
            if piece not in colophon_pieces:
                colophon_pieces.append(piece)
    if colophon_pieces and not record.get("colophon_text"):
        record["colophon_text"] = " | ".join(colophon_pieces)

    # ── Structured colophon extraction ───────────────────────────────
    # Best-effort: extract a year and a scribe name from colophon text.
    # These land on optional keys (colophon_year, colophon_scribe) in the
    # record's MARC JSONB. No migration needed — they're stored alongside
    # existing free-form MARC fields.
    if record.get("colophon_text") and not record.get("colophon_year"):
        _extract_colophon_fields(record)

    # ── Work mentions from 500$a notes (כולל: pattern) ──────────────
    # NLI cataloguers often list contained works after the keyword כולל
    # (e.g. "כולל: עת שערי רצון; שיר השירים"). Extract these as work
    # entity candidates so they can flow to match_work.
    # Derived work mentions may have been persisted by an older parser.
    # Recompute them from the raw 500 field on every preparation so parser
    # fixes invalidate stale catalogue fragments without a data migration.
    _extract_work_mentions(record)

    if not record.get("editorial_metadata"):
        _extract_editorial_metadata(record)

    # ── Provenance (561$a — Provenance-NER input) ──────────────────
    provenance_pieces = _split_multi(_str(record.get("561$a")))
    if provenance_pieces and not record.get("provenance"):
        record["provenance"] = " | ".join(provenance_pieces)

    # ── Contents (505$a — Contents-NER input + work driver) ────────
    # Desktop's 505 handler splits on ``--`` to recover one row per
    # contained work. We mirror that here so the contents_ner pipeline
    # + the desktop WikidataItemBuilder's `_add_works_and_authorities`
    # see a populated `contents` list.
    from converter.rdf.rdf_helpers import (  # noqa: PLC0415
        is_descriptive_content_title,
        parse_contents_entry,
    )

    contents: list[dict[str, Any]] = list(record.get("contents") or [])
    for chunk in _split_multi(_str(record.get("505$a"))):
        for raw_title in chunk.split("--"):
            parsed = parse_contents_entry(raw_title.strip().strip(".,;:"))
            title = parsed["title"]
            if not title or is_descriptive_content_title(title):
                continue
            entry: dict[str, Any] = {
                "title": title,
                "source_field": "505",
                "candidate_kind": "named_work",
                "source_text": parsed.get("raw") or raw_title,
            }
            if parsed.get("folio_range"):
                entry["folio_range"] = parsed["folio_range"]
            if parsed.get("sequence") is not None:
                entry["sequence"] = parsed["sequence"]
            contents.append(entry)
    if contents:
        record["contents"] = contents

    # ── Provenance events (movement map) — 541 $b acquisition, 583 $j action ─
    _extract_provenance_events(record)


def _first(v: Any) -> str | None:
    """First non-empty value from a (possibly pipe-separated) subfield."""
    parts = _split_multi(_str(v))
    return parts[0] if parts and parts[0] else None


import re as _re

# ── Structured colophon extraction ─────────────────────────────────────────

# Hebrew year in square brackets: [ה'תר"ל] or [תרל"ה] or [ה'תרל"ה]
_HEBREW_YEAR_RE = _re.compile(
    r"\[(?:ה['\u05F3])?(?P<year>[א-ת]{2,6}[\"'״\u05F4][א-ת])\]"
)
# Gregorian year: 4 digits in the range 1000–2100
_GREGORIAN_YEAR_RE = _re.compile(r"\b(?P<year>1[0-9]{3}|20[0-9]{2})\b")
# Patronymic scribe pattern: "בן" or "ב"ר" / "ב\"ר" followed by a name
_SCRIBE_BEN_RE = _re.compile(r"(?:ב\"ר|ב'ר|בן)\s+(?P<name>[\u05D0-\u05EA]+(?:\s+[\u05D0-\u05EA]+)?)")

# Hebrew letter→decimal for simple gematria (used for year conversion)
_HEB_VAL: dict[str, int] = {
    "א": 1, "ב": 2, "ג": 3, "ד": 4, "ה": 5, "ו": 6, "ז": 7, "ח": 8, "ט": 9,
    "י": 10, "כ": 20, "ל": 30, "מ": 40, "נ": 50, "ס": 60, "ע": 70, "פ": 80,
    "צ": 90, "ק": 100, "ר": 200, "ש": 300, "ת": 400,
}


def _gematria_to_gregorian(heb: str) -> int | None:
    """Convert a Hebrew year gematria string (without thousands prefix) to CE year.

    Adds 1240 for the 5th millennium (5001–5999 → 1240–2239 CE).
    Returns None on failure.
    """
    total = 0
    for ch in heb:
        if ch in ('"', "'", "\u05F4", "\u05F3"):
            continue
        val = _HEB_VAL.get(ch)
        if val is None:
            return None
        total += val
    if 1 <= total <= 999:
        return total + 1240
    return None


def _extract_colophon_fields(record: dict[str, Any]) -> None:
    """Best-effort extract colophon_year and colophon_scribe from colophon_text."""
    from converter.transformer.gematria import (  # noqa: PLC0415
        letters_to_gregorian_year,
        letters_to_hebrew_year,
    )

    text = str(record.get("colophon_text") or "")
    if not text:
        return

    # Try Hebrew bracket year first.
    m = _HEBREW_YEAR_RE.search(text)
    if m:
        token = m.group("year")
        hy = letters_to_hebrew_year(token)
        greg = letters_to_gregorian_year(token)
        if hy is not None:
            record["colophon_hebrew_year"] = hy
        if greg is not None:
            record["colophon_year"] = greg

    # Fallback: Gregorian year.
    if not record.get("colophon_year"):
        m2 = _GREGORIAN_YEAR_RE.search(text)
        if m2:
            ce = int(m2.group("year"))
            record["colophon_year"] = ce
            from converter.transformer.gematria import gregorian_to_hebrew_year  # noqa: PLC0415

            hy = gregorian_to_hebrew_year(ce)
            if hy is not None:
                record["colophon_hebrew_year"] = hy

    # Scribe: patronymic "בן / ב"ר" pattern
    ms = _SCRIBE_BEN_RE.search(text)
    if ms:
        record["colophon_scribe"] = ms.group("name").strip()


# ── Work mentions extraction ────────────────────────────────────────────────

_WORK_MENTION_TRIGGERS = _re.compile(
    r"(?:^|(?:(?:כה[\"״]?י|בכה[\"״]?י|החיבור|החבור|כתב[ -]היד)\s+))"
    r"(?:כולל|ובו|מכיל|תפסיל|ובתוכו)\s*:?\s*(?P<titles>[^.|]+)",
    _re.UNICODE,
)
_WORK_TITLE_HEADS = (
    "ספר", "מסכת", "פירוש", "פרוש", "תשובות", "סידור", "סדר", "תפילה",
    "תפלת", "קונטרס", "חיבור", "דרוש", "פיוט", "מגילה", "הלכות", "ליקוט",
    "שער", "שיר", "זוהר", "מדרש", "מחזור", "פסקי", "אגרת", "כתבי", "תיקון",
    "קבלה", "כוונות", "תרגום", "מחברת", "פרי",
)
_WORK_HEAD_PATTERN = "|".join(_re.escape(head) for head in _WORK_TITLE_HEADS)
_WORK_NAMED_SEP_RE = _re.compile(
    rf"\s*;\s*|\s*,\s*(?=(?:{_WORK_HEAD_PATTERN})\b)|"
    rf"\s+ו(?=(?:{_WORK_HEAD_PATTERN})\b)",
    _re.UNICODE,
)
_GERSHAYIM_IN_WORD_RE = _re.compile(r'(?<!\sו)(?<=[֐-ת])"(?=[֐-ת])')
_GERSHAYIM_SENTINEL = "\ue001"


def _quoted_work_titles(text: str) -> list[str]:
    protected = _GERSHAYIM_IN_WORD_RE.sub(_GERSHAYIM_SENTINEL, text.replace('""', '"'))
    return [
        match.replace(_GERSHAYIM_SENTINEL, '"').strip()
        for match in _re.findall(r'"([^"|]{3,}?)"', protected)
    ]


def _unquoted_work_titles(text: str) -> list[str]:
    candidate = text.strip()
    if ":" in candidate:
        _, suffix = candidate.split(":", 1)
        if _re.match(rf"\s*(?:{_WORK_HEAD_PATTERN})\b", suffix):
            candidate = suffix.strip()
    return [part.strip() for part in _WORK_NAMED_SEP_RE.split(candidate) if part.strip()]


def _extract_work_mentions(record: dict[str, Any]) -> None:
    """Extract named works from semantically anchored MARC 500 notes."""
    record.pop("work_mentions", None)
    raw_500a = _str(record.get("500$a"))
    candidates = [part.strip() for part in raw_500a.split("|") if part.strip()]

    from converter.rdf.rdf_helpers import (  # noqa: PLC0415
        clean_marc_label,
        is_descriptive_content_title,
    )

    work_mentions: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    for note in candidates:
        for match in _WORK_MENTION_TRIGGERS.finditer(note):
            raw_titles = match.group("titles").strip()
            quoted = _quoted_work_titles(raw_titles)
            raw_candidates = list(quoted)
            if quoted:
                prefix = raw_titles.split('"', 1)[0].strip(" ,;:")
                if _re.match(rf"(?:{_WORK_HEAD_PATTERN})\b", prefix):
                    raw_candidates.insert(0, prefix)
            else:
                raw_candidates = _unquoted_work_titles(raw_titles)
            for raw in raw_candidates:
                title = clean_marc_label(raw.strip().strip(".,;:"))
                if len(title) < 3 or is_descriptive_content_title(title):
                    continue
                key = title.casefold()
                if key in seen_titles:
                    continue
                seen_titles.add(key)
                work_mentions.append({
                    "title": title,
                    "source_field": "500",
                    "candidate_kind": "named_work",
                    "source_text": match.group(0).strip(),
                })
    if work_mentions:
        record["work_mentions"] = work_mentions


_EDITOR_IN_RE = _re.compile(
    r"בעריכת\s+([^.;|]+)",
    _re.UNICODE,
)
_EDITOR_LABEL_RE = _re.compile(
    r"עורך[:\s]+([^.;|]+)",
    _re.UNICODE,
)
_EDITION_STMT_RE = _re.compile(
    r"(?:מהדורת|במהדורה)\s+([^.;]+)",
    _re.UNICODE,
)


def _extract_editorial_metadata(record: dict[str, Any]) -> None:
    """Extract editor / edition signals from 500$a notes (Gila audit MS)."""
    candidates: list[str] = list(record.get("notes") or [])
    raw_500a = _str(record.get("500$a"))
    if raw_500a and raw_500a not in candidates:
        candidates.append(raw_500a)

    editor_names: list[str] = []
    edition_features: list[str] = []
    edition_statement = ""
    has_imprint = False

    for note in candidates:
        for match in _EDITOR_IN_RE.finditer(note):
            name = match.group(1).strip().strip("., ")
            if name and name not in editor_names:
                editor_names.append(name)
        for match in _EDITOR_LABEL_RE.finditer(note):
            name = match.group(1).strip().strip("., ")
            if name and name not in editor_names:
                editor_names.append(name)
        if "הערות והוספות בשוליים" in note and "marginal_notes" not in edition_features:
            edition_features.append("marginal_notes")
        if "בסופו השערים" in note:
            has_imprint = True
        ed_match = _EDITION_STMT_RE.search(note)
        if ed_match and not edition_statement:
            edition_statement = ed_match.group(1).strip()
        if "הגהות" in note and "emendations" not in edition_features:
            edition_features.append("emendations")

    if not any([editor_names, edition_statement, edition_features, has_imprint]):
        return

    record["editorial_metadata"] = {
        "editor_names": editor_names,
        "edition_statement": edition_statement,
        "edition_features": edition_features,
        "has_imprint": has_imprint,
    }


def _extract_provenance_events(record: dict[str, Any]) -> None:
    """Build ``record["provenance_events"]`` from collapsed 541/583 keys.

    Reuses the desktop ``FieldHandlers`` helpers so the TSV/JSON
    collapsed-key path and the ``.mrc`` path (which runs desktop
    ``extract_all_data``) emit byte-identical event dicts. Idempotent:
    skips when ``provenance_events`` is already populated (the ``.mrc``
    path fills it upstream).
    """
    if record.get("provenance_events"):
        return
    from converter.transformer.field_handlers import FieldHandlers  # noqa: PLC0415

    events: list[dict[str, Any]] = []

    # MARC 541 — acquisition (place from $b address, date from $d, agent $a).
    place_text = FieldHandlers._city_from_address(_first(record.get("541$b")))
    ev = FieldHandlers.build_provenance_event(
        event_type="acquisition",
        place_text=place_text,
        agent_name=_first(record.get("541$a")),
        date_str=_first(record.get("541$d")),
        source_field="541",
    )
    if ev:
        events.append(ev)

    # MARC 583 — conservation / exhibition (site $j; may repeat, pipe-joined).
    sites = _split_multi(_str(record.get("583$j")))
    actions = _split_multi(_str(record.get("583$a"))) + [""] * 999
    juris = _split_multi(_str(record.get("583$h"))) + [""] * 999
    dates = _split_multi(_str(record.get("583$c"))) + [""] * 999
    for site, action, jur, dt in zip(sites, actions, juris, dates):
        if not site.strip():
            continue
        event_type = "exhibition" if "exhib" in action.lower() else "conservation"
        ev = FieldHandlers.build_provenance_event(
            event_type=event_type,
            place_text=site,
            agent_name=jur or None,
            date_str=dt or None,
            source_field="583",
        )
        if ev:
            events.append(ev)

    if events:
        record["provenance_events"] = events


def _str(v: Any) -> str:
    if not isinstance(v, str):
        return ""
    from app.pipeline.entity_normalize import normalize_entity_text  # noqa: PLC0415

    return normalize_entity_text(v)


def _label_from_marc_entry(
    entry: Any,
    *,
    keys: tuple[str, ...] = ("place", "name", "term", "title", "text"),
) -> str:
    """Coerce a MARC list entry (str or dict) to a plain label string.

    Older ingest rows and desktop ``752`` hierarchies sometimes store
    ``related_places`` as ``{"place": "…", "hierarchy": […]}`` dicts.
    WikidataItemBuilder calls ``.strip()`` on these — normalise here.
    """
    if entry is None:
        return ""
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        for key in keys:
            raw = entry.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return ""
    return str(entry).strip()


def _normalize_related_places(places: list[Any] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for entry in places or []:
        label = _label_from_marc_entry(entry, keys=("place", "name", "term"))
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(label)
    return out


def _normalize_related_works(entries: list[Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in entries or []:
        if isinstance(entry, str):
            title = entry.strip()
            if title:
                out.append({"title": title})
            continue
        if not isinstance(entry, dict):
            continue
        title = _label_from_marc_entry(entry, keys=("title", "name", "term"))
        if not title:
            continue
        norm = dict(entry)
        norm["title"] = title
        out.append(norm)
    return out


def prepare_record_for_pipeline(rec: dict[str, Any]) -> dict[str, Any]:
    """Normalise a ``run_records.marc`` JSONB row before any pipeline stage.

    Must be called by every pipeline consumer that reads records straight
    from the DB (Wikidata Studio, RDF build, etc.) so they all see the
    same flat-key shape as ingest-time records.

    Specifically:
    - Collapses raw ``<tag>$<sub>`` subfield keys (``505$a`` → ``contents``,
      ``561$a`` → ``provenance``, ``500$a`` → ``notes``, etc.) for records
      uploaded before the 2026-06-02 ingest normalisation was deployed.
    - Coerces ``genres`` to ``list[str]`` (the item builder and graph builder
      both expect plain strings; old rows stored dicts).

    Safe to call on already-normalised records — ``_collapse_marc_subfields``
    is idempotent for all non-subfield-key paths.
    """
    row = dict(rec)
    control_number = (
        row.get("_control_number")
        or row.get("control_number")
        or row.get("controlNumber")
        or row.get("001")
        or ""
    )
    row["_control_number"] = str(control_number).strip().strip("\"'")
    if any("$" in k for k in row):
        _collapse_marc_subfields(row)
    if row.get("contributors"):
        row["contributors"] = _expand_pipe_delimited_entries(list(row["contributors"]))
    if row.get("authors"):
        row["authors"] = _expand_pipe_delimited_entries(list(row["authors"]))
    from converter.transformer.subject_records import (  # noqa: PLC0415
        normalize_genre_entries,
        normalize_subjects_list,
    )

    if row.get("subjects"):
        row["subjects"] = normalize_subjects_list(list(row["subjects"]))
    if row.get("genres") or row.get("genre_entries"):
        flat_genres, genre_entries = normalize_genre_entries(
            list(row.get("genres") or []),
            genre_entries=list(row.get("genre_entries") or []),
        )
        # Always replace — legacy rows store empty 655 dict shells
        # ``[{"name": "", "field": "655"}]`` that must not reach item_builder.
        row["genres"] = flat_genres
        row["genre_entries"] = genre_entries
    if row.get("related_places"):
        row["related_places"] = _normalize_related_places(list(row["related_places"]))
    if row.get("related_works"):
        row["related_works"] = _normalize_related_works(list(row["related_works"]))
    _merge_work_mentions_into_contents(row)
    return row


def _merge_work_mentions_into_contents(record: dict[str, Any]) -> None:
    """Promote ``work_mentions`` (500 כולל: parsing) into ``contents`` for RDF."""
    from converter.rdf.rdf_helpers import (  # noqa: PLC0415
        clean_marc_label,
        is_descriptive_content_title,
    )

    mentions = record.get("work_mentions") or []
    # Remove MARC 500-derived rows produced by older parser versions before
    # merging the freshly parsed work_mentions. MARC 505 rows are preserved.
    contents: list[dict[str, Any]] = [
        content
        for content in (record.get("contents") or [])
        if not (
            isinstance(content, dict)
            and str(content.get("source_field") or "").strip() == "500"
        )
    ]
    if not mentions:
        if contents:
            record["contents"] = contents
        else:
            record.pop("contents", None)
        return
    existing = {
        clean_marc_label(str(c.get("title") or "")).casefold()
        for c in contents
        if isinstance(c, dict) and c.get("title")
    }
    for wm in mentions:
        if not isinstance(wm, dict):
            continue
        title = clean_marc_label(str(wm.get("title") or ""))
        if not title or is_descriptive_content_title(title):
            continue
        key = title.casefold()
        if key in existing:
            continue
        contents.append({
            "title": title,
            "source_field": str(wm.get("source_field") or "500"),
            "candidate_kind": str(wm.get("candidate_kind") or "named_work"),
            "source_text": str(wm.get("source_text") or ""),
        })
        existing.add(key)
    if contents:
        record["contents"] = contents


from app.pipeline.entity_normalize import (
    normalize_entity_key,
    normalize_entity_text,
    normalize_role,
)


def _provenance_institution_candidates(record: dict[str, Any]) -> list[dict[str, str]]:
    """Emit corporate entities from provenance text when institutional."""
    from app.pipeline.entity_kind_infer import infer_entity_kind  # noqa: PLC0415

    candidates: list[tuple[str, str, str]] = []
    prov = str(record.get("provenance") or "").strip()
    if prov:
        for piece in prov.split("|"):
            piece = piece.strip()
            if piece:
                candidates.append((piece, "561", "former_owner"))
    for piece in _split_multi(_str(record.get("541$a"))):
        if piece:
            candidates.append((piece, "541", "collection"))
    for owner in record.get("former_owners") or []:
        if isinstance(owner, str) and owner.strip():
            candidates.append((owner.strip(), "561", "former_owner"))

    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name, field, role in candidates:
        clean = normalize_entity_text(name)
        if not clean:
            continue
        kind = infer_entity_kind(clean, field)
        if kind not in ("corporate", "organization", "meeting"):
            continue
        key = (normalize_entity_key(clean), kind)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "text": clean,
            "kind": "corporate",
            "role": normalize_role(role),
            "field": field,
        })
    return out


def _expand_pipe_delimited_entries(entries: list[Any]) -> list[dict[str, Any]]:
    """Split ``name|name`` contributor/author dicts (desktop .mrc path)."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_name = str(entry.get("name") or "")
        if not any(d in raw_name for d in _MULTI_DELIMS):
            out.append(dict(entry))
            continue
        names = _split_multi(raw_name)
        roles = _split_multi(str(entry.get("role") or ""))
        dates = _split_multi(str(entry.get("dates") or ""))
        field = str(entry.get("field") or "")
        fallback_role = str(entry.get("role") or "contributor")
        for i, seg in enumerate(names):
            seg = normalize_entity_text(seg)
            if not seg:
                continue
            role_raw = roles[i] if i < len(roles) else fallback_role
            new_entry: dict[str, Any] = {
                "name": seg,
                "role": normalize_role(role_raw),
                "field": field,
            }
            if i < len(dates) and dates[i].strip():
                new_entry["dates"] = normalize_entity_text(dates[i])
            out.append(new_entry)
    return out


from app.pipeline.entity_kind_infer import infer_entity_kind


def build_record_note_blob(record: dict[str, Any]) -> str:
    """Concatenate searchable note/colophon/work text for a MARC record."""
    parts: list[str] = []
    for note in record.get("notes") or []:
        if isinstance(note, str) and note.strip():
            parts.append(note.strip())
    colophon = record.get("colophon_text")
    if isinstance(colophon, str) and colophon.strip():
        parts.append(colophon.strip())
    scribe = record.get("colophon_scribe")
    if isinstance(scribe, str) and scribe.strip():
        parts.append(scribe.strip())
    for wm in record.get("work_mentions") or []:
        if isinstance(wm, dict):
            title = wm.get("title")
            if isinstance(title, str) and title.strip():
                parts.append(title.strip())
    meta = record.get("editorial_metadata")
    if isinstance(meta, dict):
        for name in meta.get("editor_names") or []:
            if str(name).strip():
                parts.append(str(name).strip())
        stmt = meta.get("edition_statement")
        if isinstance(stmt, str) and stmt.strip():
            parts.append(stmt.strip())
    prov = record.get("provenance")
    if isinstance(prov, str) and prov.strip():
        parts.append(prov.strip())
    return " ".join(parts).lower()


def extract_named_entities(record: dict[str, Any]) -> list[dict[str, str]]:
    """Pull person-name candidates out of a parsed MARC record."""
    out: list[dict[str, str]] = []

    for c in record.get("contributors", []):
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        if not name:
            continue
        field_tag = str(c.get("field") or "")
        ent: dict[str, str] = {
            "text": normalize_entity_text(str(name)),
            "kind": infer_entity_kind(str(name), field_tag),
            "role": normalize_role(str(c.get("role") or "")),
            "field": field_tag,
        }
        if c.get("dates"):
            ent["dates"] = str(c["dates"]).strip()
        out.append(ent)

    for a in record.get("authors", []):
        if isinstance(a, str) and a.strip():
            out.append({
                "text": normalize_entity_text(a),
                "kind": "person",
                "role": "author",
                "field": "100",
            })
        elif isinstance(a, dict) and (a.get("name") or "").strip():
            field_tag = str(a.get("field") or "100")
            kind = infer_entity_kind(str(a["name"]), field_tag)
            default_role = "institution" if kind in ("corporate", "meeting") else "author"
            ent = {
                "text": normalize_entity_text(str(a["name"])),
                "kind": kind,
                "role": normalize_role(str(a.get("role") or default_role)),
                "field": field_tag,
            }
            if a.get("dates"):
                ent["dates"] = str(a["dates"]).strip()
            out.append(ent)

    for sub in record.get("subjects", []):
        if not isinstance(sub, dict):
            continue
        kind = sub.get("type") or sub.get("kind") or ""
        name = sub.get("name") or sub.get("term") or ""
        if not name:
            continue
        name = normalize_entity_text(str(name))
        if kind == "person":
            ent = {"text": name, "kind": "person", "role": "subject", "field": "600"}
            if sub.get("dates"):
                ent["dates"] = str(sub["dates"]).strip()
            out.append(ent)
        elif kind in ("organization", "corporate"):
            out.append({
                "text": name,
                "kind": "corporate",
                "role": "institution",
                "field": str(sub.get("field") or "610"),
            })
        elif kind == "topic":
            out.append({
                "text": name,
                "kind": "topic",
                "role": "subject",
                "field": str(sub.get("field") or "650"),
            })
        elif kind in ("place", "geographic"):
            out.append({"text": name, "kind": "place", "role": "place", "field": "651"})

    # MARC 260/264 production place — the primary geographic field on most
    # manuscript records (e.g. "ירושלים", "Italy, northern").
    production_place = normalize_entity_text(str(record.get("place") or ""))
    if production_place:
        out.append({
            "text": production_place,
            "kind": "place",
            "role": "production place",
            "field": "260",
        })

    # MARC 651 / 752 / related_places — yield place entities so KIMA fires.
    for slot in ("related_places", "places"):
        for entry in record.get(slot) or []:
            if isinstance(entry, str):
                text = entry.strip()
            elif isinstance(entry, dict):
                text = str(entry.get("name") or entry.get("term") or "").strip()
            else:
                text = ""
            if text:
                out.append({
                    "text": normalize_entity_text(text),
                    "kind": "place",
                    "role": "place",
                    "field": "752",
                })

    # Provenance-event places (acquisition 541 $b, conservation/exhibition
    # 583 $j) — one place entity per event so KIMA resolves coords. The
    # ``<type>_place`` role lets the authority matcher fire KIMA and lets the
    # movement map type the stop. Coords flow back onto the event later.
    for ev in record.get("provenance_events") or []:
        if not isinstance(ev, dict):
            continue
        text = str(ev.get("place_text") or "").strip()
        if not text:
            continue
        out.append({
            "text": normalize_entity_text(text),
            "kind": "place",
            "role": f"{ev.get('type') or 'provenance'}_place",
            "field": str(ev.get("source_field") or ""),
        })

    # Institutions named in provenance / acquisition (561, 541, former owners)
    for inst in _provenance_institution_candidates(record):
        out.append(inst)

    # Work mentions extracted from notes (כולל: / כולל …)
    for wm in record.get("work_mentions") or []:
        if not isinstance(wm, dict):
            continue
        title = str(wm.get("title") or "").strip()
        if title:
            out.append({
                "text": title,
                "kind": "work",
                "role": "contained_work",
                "field": str(wm.get("source_field") or "500"),
            })

    editorial = record.get("editorial_metadata") or {}
    if isinstance(editorial, dict):
        for editor_name in editorial.get("editor_names") or []:
            name = str(editor_name).strip()
            if name:
                out.append({
                    "text": normalize_entity_text(name),
                    "kind": "person",
                    "role": "editor",
                    "field": "500",
                })

    # Dedup pass: key = (normalize(text), kind).
    # When the same entity appears multiple times with different roles (e.g.
    # a person named both as author in 100 and subject in 600), keep the
    # highest-priority role and record alt_roles for audit. When the same
    # entity appears twice with the SAME role (duplicate MARC tags), keep
    # the first occurrence (preserves dates if present on the 1st entry).
    #
    # Role priority (higher index = higher priority):
    _ROLE_PRIORITY = {
        "place": 0,
        "production_place": 1,
        "former_owner": 2,
        "subject": 2,
        "contributor": 3,
        "institution": 3,
        "author": 4,
        "scribe": 4,
        "translator": 4,
        "editor": 4,
        "contained_work": 1,
    }

    def _role_rank(r: str) -> int:
        from app.pipeline.entity_normalize import normalize_role_key
        return _ROLE_PRIORITY.get(normalize_role_key(r), 2)

    # (normalized_text, kind) → index in deduped list
    canon: dict[tuple[str, str], int] = {}
    deduped: list[dict[str, str]] = []

    for ent in out:
        nk = (normalize_entity_key(ent["text"]), ent.get("kind", ""))
        if nk not in canon:
            canon[nk] = len(deduped)
            entry = dict(ent)
            deduped.append(entry)
        else:
            existing = deduped[canon[nk]]
            existing_rank = _role_rank(existing.get("role", ""))
            new_rank = _role_rank(ent.get("role", ""))
            alt_roles: list[str] = list(existing.get("alt_roles") or [])  # type: ignore[arg-type]
            incoming_role = ent.get("role", "")
            # Promote to higher-priority role.
            if new_rank > existing_rank:
                # The old role becomes an alt_role before we overwrite it.
                old_role = existing.get("role", "")
                if old_role and old_role not in alt_roles:
                    alt_roles.append(old_role)
                existing["role"] = incoming_role
                existing["field"] = ent.get("field", "")
                # Carry dates from the promoted entry if not already set.
                if ent.get("dates") and not existing.get("dates"):
                    existing["dates"] = ent["dates"]
            else:
                # Lower/equal priority — record the incoming role as an alt_role.
                if incoming_role and incoming_role not in ([existing.get("role")] + alt_roles):
                    alt_roles.append(incoming_role)
            if alt_roles:
                existing["alt_roles"] = alt_roles

    return deduped
