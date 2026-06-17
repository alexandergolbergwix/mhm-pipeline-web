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
from pathlib import Path
from typing import Any, Iterable

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


def _collapse_marc_subfields(record: dict[str, Any]) -> None:
    """In-place normalisation: collapses raw ``<tag>$<sub>`` subfield
    columns (typical of NLI-style TSV / JSON exports) into the flat
    keys the rest of the web pipeline expects.

    Derived keys:

    * ``title``       — ``245$a`` (+ optional ``$b``)
    * ``authors``     — ``100/110/111$a`` with ``$e`` roles
    * ``contributors``— ``700/710/711/800/810/811$a`` with ``$e`` roles
    * ``subjects``    — ``600/610/611/650/651$a`` with type labels
    * ``dates.year``  — ``008`` positions 7-10
    * ``genres``      — ``655$a``
    * ``notes``       — ``500/590/541$a`` (general/local/source notes —
                        the AI-Extraction Person-NER input)
    * ``provenance``  — ``561$a`` (the Provenance-NER input)
    * ``contents``    — ``505$a`` split on ``--`` into title chunks
                        (the Contents-NER input + work-item driver)
    * ``colophon_text``— ``590$a`` (local notes — desktop convention)

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
        d_vals = _split_multi(_str(record.get(f"{tag}$d")))
        for i, name in enumerate(a_vals):
            entry = {"name": name, "type": kind, "field": tag}
            if i < len(d_vals) and d_vals[i].strip():
                entry["dates"] = d_vals[i].strip()
            subjects.append(entry)
    # 650 topical, 651 geographic
    for name in _split_multi(_str(record.get("650$a"))):
        subjects.append({"name": name, "type": "topic", "field": "650"})
    for name in _split_multi(_str(record.get("651$a"))):
        subjects.append({"name": name, "type": "place", "field": "651"})
    if subjects:
        record["subjects"] = subjects

    # ── Production / related places (MARC 751 Added Entry—Geographic Name) ─
    # NLI uses 751 $a (place name) + $e (relationship, e.g. "place of writing")
    # for manuscript production places.  Pipe-separated when repeated.
    _PRODUCTION_ROLES = frozenset(
        ("place of writing", "place of origin", "production place",
         "place of creation", "origin", "written")
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

    # ── Dates (008 positions 7-10 are the production year) ──────────
    f008 = _str(record.get("008"))
    if f008 and len(f008) >= 11 and not record.get("dates"):
        # Be lenient: accept any 4-digit run starting at byte 7.
        candidate = "".join(c for c in f008[7:11] if c.isdigit())
        if candidate and len(candidate) == 4:
            record["dates"] = {"year": int(candidate)}

    # ── Genre/form ──────────────────────────────────────────────────
    # Flat list[str] — mirrors extract_all_data() / GraphBuilder expectations.
    genres: list[str] = []
    for existing in record.get("genres") or []:
        if isinstance(existing, str) and existing.strip():
            genres.append(existing.strip())
        elif isinstance(existing, dict):
            term = _str(existing.get("name") or existing.get("term"))
            if term:
                genres.append(term)
    for name in _split_multi(_str(record.get("655$a"))):
        name = name.strip()
        if name and name not in genres:
            genres.append(name)
    if genres:
        record["genres"] = genres

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
    if not record.get("work_mentions"):
        _extract_work_mentions(record)

    # ── Provenance (561$a — Provenance-NER input) ──────────────────
    provenance_pieces = _split_multi(_str(record.get("561$a")))
    if provenance_pieces and not record.get("provenance"):
        record["provenance"] = " | ".join(provenance_pieces)

    # ── Contents (505$a — Contents-NER input + work driver) ────────
    # Desktop's 505 handler splits on ``--`` to recover one row per
    # contained work. We mirror that here so the contents_ner pipeline
    # + the desktop WikidataItemBuilder's `_add_works_and_authorities`
    # see a populated `contents` list.
    contents: list[dict[str, Any]] = list(record.get("contents") or [])
    for chunk in _split_multi(_str(record.get("505$a"))):
        for title in chunk.split("--"):
            title = title.strip().strip(".,;:")
            if title:
                contents.append({"title": title})
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
    text = str(record.get("colophon_text") or "")
    if not text:
        return

    # Try Hebrew bracket year first.
    m = _HEBREW_YEAR_RE.search(text)
    if m:
        year = _gematria_to_gregorian(m.group("year"))
        if year:
            record["colophon_year"] = year

    # Fallback: Gregorian year.
    if not record.get("colophon_year"):
        m2 = _GREGORIAN_YEAR_RE.search(text)
        if m2:
            record["colophon_year"] = int(m2.group("year"))

    # Scribe: patronymic "בן / ב"ר" pattern
    ms = _SCRIBE_BEN_RE.search(text)
    if ms:
        record["colophon_scribe"] = ms.group("name").strip()


# ── Work mentions extraction ────────────────────────────────────────────────

_WORK_MENTION_TRIGGERS = _re.compile(
    r"(?:כולל|ובו|מכיל|תפסיל|ובתוכו)[:\s]+(?P<titles>[^.]+?)(?=\.|$)",
    _re.UNICODE,
)
# Works separated by ; or , or "ו" conjunction
_WORK_SEP_RE = _re.compile(r"[;,]\s*|(?:^|\s+)ו(?=[א-ת])")


def _extract_work_mentions(record: dict[str, Any]) -> None:
    """Scan 500$a notes for work-listing keywords and emit work_mentions list.

    We scan both the already-split ``record["notes"]`` AND the raw ``500$a``
    subfield value (before ``_split_multi`` breaks it on semicolons) so that
    a note like "כולל: עת שערי רצון; שיר השירים" produces both titles even
    though the notes list has already been fragmented on the semicolon.
    """
    # Collect candidates: processed notes list + raw 500$a before split.
    candidates: list[str] = list(record.get("notes") or [])
    raw_500a = _str(record.get("500$a"))
    if raw_500a and raw_500a not in candidates:
        candidates.append(raw_500a)

    work_mentions: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    for note in candidates:
        for match in _WORK_MENTION_TRIGGERS.finditer(note):
            raw_titles = match.group("titles").strip()
            for raw in _WORK_SEP_RE.split(raw_titles):
                title = raw.strip().strip(".,;:")
                if len(title) < 3:
                    continue
                key = title.lower()
                if key in seen_titles:
                    continue
                seen_titles.add(key)
                work_mentions.append({"title": title, "source_field": "500"})
    if work_mentions:
        record["work_mentions"] = work_mentions


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
    v = v.strip()
    # Strip CSV-style double-quote wrapping that NLI export files add around
    # subfield values (e.g. '"ʻAmrān (Yemen)"' → 'ʻAmrān (Yemen)').
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    return v


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
    if any("$" in k for k in row):
        _collapse_marc_subfields(row)
    raw_genres = row.get("genres")
    if raw_genres:
        flat: list[str] = []
        for g in raw_genres:
            if isinstance(g, str) and g.strip():
                flat.append(g.strip())
            elif isinstance(g, dict):
                term = _str(g.get("name") or g.get("term"))
                if term:
                    flat.append(term)
        if flat:
            row["genres"] = flat
        elif isinstance(raw_genres, list) and raw_genres and isinstance(raw_genres[0], dict):
            row["genres"] = []
    return row


from app.pipeline.entity_normalize import (
    normalize_entity_key,
    normalize_entity_text,
    normalize_role,
)


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
        ent: dict[str, str] = {
            "text": normalize_entity_text(str(name)),
            "kind": "corporate" if str(c.get("field") or "") in ("710", "711") else "person",
            "role": normalize_role(str(c.get("role") or "")),
            "field": str(c.get("field") or ""),
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
            kind = "corporate" if field_tag in ("110", "710") else "person"
            default_role = "institution" if kind == "corporate" else "author"
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
            "role": f"{ev.get('type') or 'provenance'} place",
            "field": str(ev.get("source_field") or ""),
        })

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
