"""MARC ingest — uses desktop's parsers when possible, falls back to a
straightforward JSON/CSV/TSV reader.

Supported uploads:

* ``.json`` / ``.jsonl`` — already-parsed MARC dicts (e.g. desktop's
  ``marc_extracted.json``).
* ``.mrc`` / ``.marc`` — binary MARC21, parsed via desktop's
  ``converter.parser.unified_reader.UnifiedReader`` so every Stage-1
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
    Stage-1 field handler the desktop ships with runs here too."""
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
                rec["_control_number"] = value
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
        record["_control_number"] = str(cn)
        # If the record carries raw MARC subfield keys (``100$a``,
        # ``700$a``, ``600$a``, …) — typical of NLI-style TSV exports —
        # collapse them into the normalised authors/contributors/subjects
        # shape the entity extractor + the desktop item builder expect.
        if any("$" in k for k in row):
            _collapse_marc_subfields(record)
        out.append(record)
    return out


def _collapse_marc_subfields(record: dict[str, Any]) -> None:
    """In-place normalisation: ``100$a`` → ``authors[]``, ``700$a`` →
    ``contributors[]``, ``600$a`` → ``subjects[]`` (type=person),
    ``650$a`` → ``subjects[]`` (type=topic), ``651$a`` → place subjects,
    ``245$a/$b`` → ``title``, ``008`` → ``dates.year``.

    Multi-value subfields are pipe-separated. Roles travel through
    ``$e`` parallel arrays where present.
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
        for i, name in enumerate(a):
            role = e[i] if i < len(e) else "author"
            authors.append({"name": name, "role": role, "field": tag})
    if authors:
        record["authors"] = authors

    # ── Contributors (700, 710, 711, 800, 810, 811) ─────────────────
    contributors = list(record.get("contributors") or [])
    for tag in ("700", "710", "711", "800", "810", "811"):
        a = _split_multi(_str(record.get(f"{tag}$a")))
        e = _split_multi(_str(record.get(f"{tag}$e")))
        for i, name in enumerate(a):
            role = e[i] if i < len(e) else "contributor"
            contributors.append({"name": name, "role": role, "field": tag})
    if contributors:
        record["contributors"] = contributors

    # ── Subjects ─────────────────────────────────────────────────────
    subjects = list(record.get("subjects") or [])
    # 600 = personal-name subject; 610 = corporate; 611 = meeting
    for tag, kind in (("600", "person"), ("610", "organization"), ("611", "meeting")):
        for name in _split_multi(_str(record.get(f"{tag}$a"))):
            subjects.append({"name": name, "type": kind, "field": tag})
    # 650 topical, 651 geographic
    for name in _split_multi(_str(record.get("650$a"))):
        subjects.append({"name": name, "type": "topic", "field": "650"})
    for name in _split_multi(_str(record.get("651$a"))):
        subjects.append({"name": name, "type": "place", "field": "651"})
    if subjects:
        record["subjects"] = subjects

    # ── Dates (008 positions 7-10 are the production year) ──────────
    f008 = _str(record.get("008"))
    if f008 and len(f008) >= 11 and not record.get("dates"):
        # Be lenient: accept any 4-digit run starting at byte 7.
        candidate = "".join(c for c in f008[7:11] if c.isdigit())
        if candidate and len(candidate) == 4:
            record["dates"] = {"year": int(candidate)}

    # ── Genre/form ──────────────────────────────────────────────────
    genres = list(record.get("genres") or [])
    for name in _split_multi(_str(record.get("655$a"))):
        genres.append({"name": name, "field": "655"})
    if genres:
        record["genres"] = genres


def _str(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


# ── Entity extraction ───────────────────────────────────────────────────


def extract_named_entities(record: dict[str, Any]) -> list[dict[str, str]]:
    """Pull person-name candidates out of a parsed MARC record."""
    out: list[dict[str, str]] = []

    for c in record.get("contributors", []):
        name = (c or {}).get("name") if isinstance(c, dict) else None
        if name:
            out.append({
                "text": str(name).strip(),
                "kind": "person",
                "role": str((c or {}).get("role") or ""),
                "field": str((c or {}).get("field") or ""),
            })

    for a in record.get("authors", []):
        if isinstance(a, str) and a.strip():
            out.append({"text": a.strip(), "kind": "person", "role": "author", "field": "100"})
        elif isinstance(a, dict) and (a.get("name") or "").strip():
            out.append({
                "text": str(a["name"]).strip(),
                "kind": "person",
                "role": str(a.get("role") or "author"),
                "field": str(a.get("field") or "100"),
            })

    for sub in record.get("subjects", []):
        if isinstance(sub, dict) and sub.get("type") == "person" and sub.get("name"):
            out.append({
                "text": str(sub["name"]).strip(),
                "kind": "person",
                "role": "subject",
                "field": "600",
            })

    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for ent in out:
        key = (ent["text"], ent["role"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ent)
    return deduped
