"""Accept a MARC upload and normalise it to a list of records.

Supported input formats (dispatched by filename suffix; falls back to
content sniffing when the suffix is missing):

* ``.json``   — JSON list, JSON object, or single record.
* ``.jsonl``  — JSON-Lines, one record per line.
* ``.mrc`` / ``.marc`` — binary MARC21 (ISO 2709). Parsed via ``pymarc``.
* ``.tsv``    — tab-separated table. First row = column headers.
* ``.csv``    — comma-separated table. First row = column headers.

All paths normalise to a list of dicts with at least ``_control_number``,
matching the shape :mod:`app.pipeline.run` expects.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Iterable


# Tabular column headers we recognise — mapped to the internal record
# shape that the authority matcher's entity extractor consumes.
_CN_HEADERS = (
    "_control_number", "control_number", "controlnumber", "001", "id", "nli_id",
)
_TITLE_HEADERS = ("title", "245a", "245", "main_title")
_AUTHOR_HEADERS = ("author", "authors", "100a", "100")
_CONTRIBUTOR_HEADERS = ("contributor", "contributors", "700a", "700")
_SUBJECT_HEADERS = ("subject", "subjects", "600", "650")

# Headers in tabular uploads that hold MULTI-VALUE fields. Values are
# split on these delimiters in order.
_MULTI_DELIMS = ("|", ";", "<--SEP-->")


def parse_marc_upload(raw: bytes, *, filename: str | None = None) -> list[dict[str, Any]]:
    """Return the list of records contained in *raw*.

    ``filename`` is optional but lets us dispatch reliably; without it we
    sniff (JSON-looking → JSON, leading 5-digit length-of-record → MARC21,
    tabs in the first line → TSV, commas → CSV).
    """
    if not raw:
        raise ValueError("Upload is empty")

    suffix = _suffix(filename)
    if suffix in ("json", "jsonl"):
        return _parse_json_like(raw, jsonl=(suffix == "jsonl"))
    if suffix in ("mrc", "marc"):
        return _parse_mrc(raw)
    if suffix == "tsv":
        return _parse_table(raw, delimiter="\t")
    if suffix == "csv":
        return _parse_table(raw, delimiter=",")

    # No / unknown suffix — sniff.
    head = raw.lstrip()[:200]
    if head.startswith(b"{") or head.startswith(b"["):
        return _parse_json_like(raw, jsonl=False)
    # MARC21 records start with a 5-digit length-of-record + leader.
    if len(head) >= 5 and head[:5].isdigit():
        try:
            return _parse_mrc(raw)
        except Exception:
            pass
    # Tabular fallback: look at the first non-empty line.
    text = _decode(raw)
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    if "\t" in first:
        return _parse_table(raw, delimiter="\t")
    if "," in first:
        return _parse_table(raw, delimiter=",")
    # Last-ditch attempt: JSONL.
    return _parse_json_like(raw, jsonl=True)


# ── JSON / JSON-Lines ────────────────────────────────────────────────────


def _parse_json_like(raw: bytes, *, jsonl: bool) -> list[dict[str, Any]]:
    text = _decode(raw).strip()
    if not text:
        raise ValueError("Upload is empty")

    if jsonl:
        return _normalise_records(_iter_jsonl(text))

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Some "JSON" exports are actually JSONL — fall back gracefully.
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


# ── Tabular (TSV / CSV) ──────────────────────────────────────────────────


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
                # Keep unknown columns under their normalised header so
                # the MARC popup still surfaces them.
                rec[norm] = value
        if rec:
            out.append(rec)
    return _normalise_records(out)


def _header_norm(s: str) -> str:
    return (
        s.strip().lower()
         .replace(" ", "_")
         .replace("-", "_")
         .lstrip("﻿")
    )


def _split_multi(value: str) -> list[str]:
    for delim in _MULTI_DELIMS:
        if delim in value:
            return [v.strip() for v in value.split(delim) if v.strip()]
    return [value]


# ── MARC21 binary (.mrc) ────────────────────────────────────────────────


def _parse_mrc(raw: bytes) -> list[dict[str, Any]]:
    try:
        from pymarc import MARCReader  # noqa: PLC0415 — heavy import deferred
    except ImportError as exc:
        raise ValueError(
            "pymarc is required to parse binary MARC21 (.mrc) — pip install pymarc"
        ) from exc

    out: list[dict[str, Any]] = []
    reader = MARCReader(raw, to_unicode=True, force_utf8=True, utf8_handling="replace")
    for record in reader:
        if record is None:
            continue
        rec = _marc_record_to_dict(record)
        if rec:
            out.append(rec)
    return _normalise_records(out)


def _marc_record_to_dict(record: Any) -> dict[str, Any]:
    cn = ""
    f001 = record["001"]
    if f001 is not None:
        cn = (f001.data or "").strip()

    title = ""
    f245 = record["245"]
    if f245 is not None:
        title = (f245.value() or "").strip()

    authors: list[str] = []
    for f100 in record.get_fields("100"):
        a = (f100.value() or "").strip()
        if a:
            authors.append(a)

    contributors: list[dict[str, str]] = []
    for tag in ("700", "710", "711"):
        for field in record.get_fields(tag):
            name = (field["a"] or field.value() or "").strip()
            if not name:
                continue
            relator = (field["e"] or "").strip() if "e" in field else ""
            contributors.append(
                {"name": name, "role": relator or "contributor", "field": tag},
            )

    subjects: list[dict[str, str]] = []
    for tag in ("600", "610", "611", "650", "651"):
        for field in record.get_fields(tag):
            name = (field["a"] or field.value() or "").strip()
            if not name:
                continue
            subjects.append(
                {
                    "name": name,
                    "type": "person" if tag.startswith("60") else "topic",
                }
            )

    notes: list[str] = []
    for tag in ("500", "505", "561"):
        for field in record.get_fields(tag):
            v = (field.value() or "").strip()
            if v:
                notes.append(v)

    return {
        "_control_number": cn,
        "title": title,
        "authors": authors,
        "contributors": contributors,
        "subjects": subjects,
        "notes": notes,
    }


# ── helpers ──────────────────────────────────────────────────────────────


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
        out.append(record)
    return out


# ── Entity extraction ────────────────────────────────────────────────────


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
