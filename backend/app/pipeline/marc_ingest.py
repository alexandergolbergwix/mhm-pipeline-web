"""Accept a MARC upload and normalise it to a list of records.

Two input formats are supported for now:

* ``marc_extracted.json`` — the JSON list the desktop pipeline produces
  after Stage 1. Each record carries ``_control_number`` plus a
  free-form set of fields.
* JSON-Lines (.jsonl) — one record per line, same shape.

A future revision will accept raw MARC21 binary via ``pymarc``; until
then the upload mirrors what the curator already has in front of them.
"""

from __future__ import annotations

import json
from typing import Any, Iterable


def parse_marc_upload(raw: bytes) -> list[dict[str, Any]]:
    """Return the list of records contained in *raw*.

    Accepts a JSON array, a JSON-Lines stream, or a single JSON object
    (one record). Tolerates UTF-8 BOM. Raises ``ValueError`` with a
    human-readable message on bad input.
    """
    text = raw.decode("utf-8-sig", errors="strict").strip()
    if not text:
        raise ValueError("Upload is empty")

    # Try JSON array / object first.
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _parse_jsonl(text)

    if isinstance(parsed, list):
        return _normalise_records(parsed)
    if isinstance(parsed, dict):
        return _normalise_records([parsed])
    raise ValueError("Top-level JSON must be an object or an array of objects.")


def _parse_jsonl(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
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
        out.append(row)
    return _normalise_records(out)


def _normalise_records(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Make sure every record exposes a non-empty ``_control_number``.

    The desktop pipeline already stamps one but external MARC dumps may
    use ``001``, ``id``, or omit it entirely (in which case we synthesise
    a positional id so the run is still navigable).
    """
    out = []
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


def extract_named_entities(record: dict[str, Any]) -> list[dict[str, str]]:
    """Pull person-name candidates out of a parsed MARC record.

    Mirrors the desktop pipeline's authority feed: MARC 100 + 700 names
    + the ``contributors`` array some Stage-1 outputs include. Returns a
    list of ``{"text": <name>, "kind": "person", "role": <role>, "field": <marc-tag>}``.
    The set is deliberately small for the MVP — VIAF/Wikidata/KIMA
    enrichment lands when the corresponding adapter does.
    """
    out: list[dict[str, str]] = []

    # 100 + 700 in raw MARC dump shape: ``record["100"]["a"]`` or
    # ``record["contributors"] = [{name, role, field}]`` in the
    # already-normalised shape the desktop pipeline emits.
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

    # Subject persons (MARC 600).
    for sub in record.get("subjects", []):
        if isinstance(sub, dict) and sub.get("type") == "person" and sub.get("name"):
            out.append({
                "text": str(sub["name"]).strip(),
                "kind": "person",
                "role": "subject",
                "field": "600",
            })

    # Deduplicate by (text, role) to avoid repeated noise.
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for ent in out:
        key = (ent["text"], ent["role"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ent)
    return deduped
