"""Preserve original MARC occurrences beside the flattened compatibility view."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from converter.parser.marc_reader import MarcField, MarcRecord


def _slot_text(value: object) -> str:
    text = str(value or "").strip()
    for quote in ('"', "'"):
        while len(text) >= 2 and text.startswith(quote) and text.endswith(quote):
            text = text[1:-1].strip()
    return text


def split_slots(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [_slot_text(part) for part in value]
    text = _slot_text(value)
    for delimiter in ("<--SEP-->", "|", ";"):
        if delimiter in text:
            return [_slot_text(part) for part in text.split(delimiter)]
    return [text] if text else []


def occurrences(record: Mapping[str, object], tag: str | None = None) -> list[dict[str, object]]:
    raw = record.get("marc_occurrences", [])
    if not isinstance(raw, list):
        raise ValueError("MARC occurrences must be an array.")
    result = []
    seen: set[tuple[str, int]] = set()
    counts: dict[str, int] = defaultdict(int)
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ValueError("A MARC occurrence must be an object.")
        field_tag = str(entry.get("tag", ""))
        if not re.fullmatch(r"\d{3}", field_tag):
            raise ValueError("A MARC occurrence needs a three-digit tag.")
        counts[field_tag] += 1
        index = entry.get("occurrence", counts[field_tag])
        if type(index) is not int or index < 1 or (field_tag, index) in seen:
            raise ValueError("MARC occurrence identifiers must be unique positive integers.")
        seen.add((field_tag, index))
        fields = entry.get("subfields", [])
        if not isinstance(fields, list):
            raise ValueError("MARC subfields must retain their original order.")
        normalized = []
        for subfield in fields:
            if not isinstance(subfield, Mapping):
                raise ValueError("A MARC subfield must be an object.")
            code, value = subfield.get("code"), subfield.get("value")
            if not isinstance(code, str) or len(code) != 1 or not isinstance(value, str):
                raise ValueError("A MARC subfield needs a code and text value.")
            normalized.append({"code": code, "value": value})
        indicators = entry.get("indicators", [" ", " "])
        if not isinstance(indicators, (list, tuple)) or len(indicators) != 2:
            raise ValueError("A MARC data field needs two indicators.")
        if any(not isinstance(v, str) or len(v) != 1 for v in indicators):
            raise ValueError("Each MARC indicator must contain one character.")
        row = {
            "tag": field_tag,
            "occurrence": index,
            "indicators": list(indicators),
            "subfields": normalized,
        }
        if "data" in entry:
            if not isinstance(entry["data"], str):
                raise ValueError("A MARC control field needs a text value.")
            row["data"] = entry["data"]
        if tag is None or field_tag == tag:
            result.append(row)
    return result


def values(row: Mapping[str, object], code: str) -> list[str]:
    fields = row.get("subfields", [])
    return (
        [
            str(field["value"])
            for field in fields
            if isinstance(field, Mapping) and field.get("code") == code
        ]
        if isinstance(fields, list)
        else []
    )


def source_key(row: Mapping[str, object]) -> str:
    return f"{row['tag']}:{row['occurrence']}"


def attach(record: dict[str, object]) -> None:
    """Normalize MARC JSON and regenerate only its derived flat columns."""
    if "marc_occurrences" not in record and isinstance(record.get("fields"), list):
        converted = []
        for field in record["fields"]:
            if not isinstance(field, Mapping) or len(field) != 1:
                raise ValueError("A MARC JSON field needs one tag.")
            tag, data = next(iter(field.items()))
            if isinstance(data, str):
                converted.append({"tag": tag, "data": data})
            elif isinstance(data, Mapping):
                subfields = []
                for sf in data.get("subfields", []):
                    if not isinstance(sf, Mapping) or len(sf) != 1:
                        raise ValueError("A MARC JSON subfield needs one code.")
                    code, value = next(iter(sf.items()))
                    subfields.append({"code": code, "value": value})
                converted.append(
                    {
                        "tag": tag,
                        "indicators": [data.get("ind1", " "), data.get("ind2", " ")],
                        "subfields": subfields,
                    }
                )
            else:
                raise ValueError("Invalid MARC JSON field.")
        record["marc_occurrences"] = converted
    if "marc_occurrences" not in record:
        return
    for key in record.pop("_marc_derived_keys", []):
        if isinstance(key, str) and not re.match(r"^\d{3}(?:\$|$)", key):
            record.pop(key, None)
    rows = occurrences(record)
    record["marc_occurrences"] = deepcopy(rows)
    record.pop("fields", None)
    for key in list(record):
        if re.fullmatch(r"\d{3}(?:\$.)?", key):
            del record[key]
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["tag"])].append(row)
        if "data" in row:
            record[str(row["tag"])] = row["data"]
    for tag, fields in grouped.items():
        codes = {str(sf["code"]) for row in fields for sf in row["subfields"]}
        for code in codes:
            record[f"{tag}${code}"] = "|".join(" ".join(values(row, code)) for row in fields)


def mark_derived(record: dict[str, object], before: Mapping[str, object]) -> None:
    if "marc_occurrences" in record:
        record["_marc_derived_keys"] = sorted(
            key
            for key, value in record.items()
            if key not in {"_marc_derived_keys", "_control_number", "marc_occurrences", "fields"}
            and not re.match(r"^\d{3}(?:\$|$)", key)
            and (key not in before or before[key] != value)
        )


def content_inputs(record: Mapping[str, object]) -> list[dict[str, object]]:
    result = []
    for row in occurrences(record, "505"):
        row_start = len(result)
        for index, text in enumerate(values(row, "a"), 1):
            result.append({"text": text, "source_key": f"{source_key(row)}:a:{index}"})
        current = None
        title_index = 0
        for sf in row["subfields"]:
            if sf["code"] == "t":
                title_index += 1
                current = {
                    "text": sf["value"],
                    "source_key": f"{source_key(row)}:t:{title_index}",
                }
                result.append(current)
            elif sf["code"] == "r" and current is not None:
                # More than one responsibility stays literal; identity review
                # must establish its public authors rather than guess a split.
                current["author"] = " / ".join(filter(None, [current.get("author"), sf["value"]]))
        scopes = values(row, "g")
        if scopes:
            entries = result[row_start:]
            for entry in entries:
                entry["scope_text"] = " / ".join(scopes)
                if len(entries) != 1 or len(scopes) != 1:
                    entry["scope_alignment_unproven"] = True
    if occurrences(record, "505"):
        return result
    return [
        {"text": text, "source_key": f"505$a:{index}"}
        for index, text in enumerate(split_slots(record.get("505$a")), 1)
        if text
    ]


def from_marc_record(record: MarcRecord) -> list[dict[str, object]]:
    order = record.field_order or [
        (tag, index) for tag, fields in record.fields.items() for index in range(len(fields))
    ]
    rows = []
    for tag, index in order:
        field = record.fields[tag][index]
        subfields = field.ordered_subfields or [
            (code, value) for code, entries in field.subfields.items() for value in entries
        ]
        row = {
            "tag": tag,
            "indicators": [v or " " for v in field.indicators],
            "subfields": [{"code": code, "value": value} for code, value in subfields],
        }
        if field.data is not None:
            row["data"] = field.data
        rows.append(row)
    return occurrences({"marc_occurrences": rows})


def as_marc_fields(record: Mapping[str, object]) -> dict[str, list[MarcField]]:
    from converter.parser.marc_reader import MarcField

    result: dict[str, list[MarcField]] = defaultdict(list)
    for row in occurrences(record):
        subfields: dict[str, list[str]] = defaultdict(list)
        pairs = [(sf["code"], sf["value"]) for sf in row["subfields"]]
        for code, value in pairs:
            subfields[code].append(value)
        result[str(row["tag"])].append(
            MarcField(
                tag=str(row["tag"]),
                indicators=tuple(row["indicators"]),
                subfields=dict(subfields),
                data=row.get("data"),
                ordered_subfields=pairs,
            )
        )
    return dict(result)


def apply_people(record: dict[str, object]) -> None:
    from app.pipeline.entity_normalize import normalize_entity_text, normalize_role

    groups = {
        "authors": {"100", "110", "111"},
        "contributors": {"700", "710", "711", "800", "810", "811"},
        "subjects": {"600", "610", "611"},
    }
    rows = occurrences(record)
    for target, tags in groups.items():
        present = {str(row["tag"]) for row in rows if row["tag"] in tags}
        if not present:
            continue
        previous = record.get(target, [])
        entries = (
            [
                entry
                for entry in previous
                if isinstance(entry, Mapping) and entry.get("field") not in present
            ]
            if isinstance(previous, list)
            else []
        )
        for row in rows:
            tag = str(row["tag"])
            if tag not in present:
                continue
            names, dates, roles, titles = (values(row, c) for c in ("a", "d", "e", "c"))
            for i, name in enumerate(names):
                if not name.strip():
                    continue
                suffix = (
                    " ".join(titles)
                    if len(names) == 1
                    else titles[i]
                    if len(titles) == len(names)
                    else ""
                )
                role_values = (
                    roles if len(names) == 1 else [roles[i]] if len(roles) == len(names) else []
                )
                for role in role_values or ["author" if target == "authors" else "contributor"]:
                    entry = {
                        "name": normalize_entity_text(f"{name.rstrip(' ,')} {suffix}".strip()),
                        "field": tag,
                        "source_occurrence": source_key(row),
                    }
                    if target == "subjects":
                        entry["type"] = (
                            "person"
                            if tag == "600"
                            else "organization"
                            if tag == "610"
                            else "meeting"
                        )
                    else:
                        entry["role"] = normalize_role(role)
                    if len(dates) == len(names) and dates[i].strip():
                        entry["dates"] = dates[i]
                    if suffix:
                        entry["raw_name_a"] = normalize_entity_text(name)
                        entry["title_associated"] = normalize_entity_text(suffix)
                    authority_ids = values(row, "0")
                    if len(names) == 1 and authority_ids:
                        entry["authority_id"] = authority_ids[0]
                        entry["authority_ids_raw"] = authority_ids
                    for code, key in (("0", "authority_id"), ("2", "source")):
                        vals = values(row, code)
                        if len(vals) == len(names) and vals[i].strip():
                            entry[key] = vals[i]
                    entries.append(entry)
        record[target] = entries
