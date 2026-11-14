"""Keep one current retrieval for an otherwise identical, source-bound citation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date


def _normalized(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: _normalized(item)
            for key, item in value.items()
            if key not in {"hash", "snaks-order"}
        }
    if isinstance(value, list):
        return sorted(
            (_normalized(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True)
        )
    return value


def _identity(reference: Mapping[str, object]) -> str:
    return json.dumps(_normalized(reference), sort_keys=True, ensure_ascii=False)


def _retrieval(reference: Mapping[str, object]) -> tuple[str, date] | None:
    snaks = reference.get("snaks")
    if not isinstance(snaks, Mapping):
        raise ValueError("A reference needs a snaks object.")
    dates = snaks.get("P813")
    if not isinstance(dates, list) or len(dates) != 1 or not isinstance(dates[0], Mapping):
        return None
    snak = dates[0]
    data = snak.get("datavalue")
    value = data.get("value") if isinstance(data, Mapping) else None
    if (
        not isinstance(data, Mapping)
        or data.get("type") != "time"
        or snak.get("property") != "P813"
        or snak.get("snaktype") != "value"
        or snak.get("datatype") != "time"
        or not isinstance(value, Mapping)
        or value.get("precision") != 11
        or value.get("calendarmodel") != "http://www.wikidata.org/entity/Q1985727"
        or any(value.get(key, 0) != 0 for key in ("timezone", "before", "after"))
    ):
        return None
    match = re.fullmatch(r"\+(\d{4,})-(\d{2})-(\d{2})T00:00:00Z", str(value.get("time") or ""))
    if match is None:
        return None
    try:
        year, month, day = (int(part) for part in match.groups())
        retrieved = date(year, month, day)
    except ValueError:
        return None
    located = False
    for pid, rows in snaks.items():
        if pid == "P813" or not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping) or row.get("snaktype") != "value":
                continue
            payload = row.get("datavalue")
            literal = payload.get("value") if isinstance(payload, Mapping) else None
            if (
                isinstance(literal, str)
                and literal.strip()
                and (
                    pid == "P854"
                    and row.get("datatype") == "url"
                    or row.get("datatype") == "external-id"
                )
            ):
                located = True
    if not located:
        return None
    shape = deepcopy(dict(reference))
    shape_snaks = dict(snaks)
    shape["snaks"] = shape_snaks
    shape_snaks["P813"] = [
        {
            **dict(snak),
            "datavalue": {
                **dict(data),
                "value": {**dict(value), "time": "RETRIEVAL_DATE"},
            },
        }
    ]
    return _identity(shape), retrieved


def reference_source_key(reference: Mapping[str, object]) -> str:
    retrieval = _retrieval(reference)
    return "retrieval:" + retrieval[0] if retrieval else "exact:" + _identity(reference)


def current_reference_indices(
    references: Sequence[Mapping[str, object]],
    *,
    merge_retrieval: bool = True,
) -> list[int]:
    """Retain other source snaks and the latest valid day for each exact citation."""
    kept: list[int] = []
    positions: dict[str, int] = {}
    latest: dict[str, date] = {}
    for index, reference in enumerate(references):
        retrieval = _retrieval(reference) if merge_retrieval else None
        key = "retrieval:" + retrieval[0] if retrieval else "exact:" + _identity(reference)
        if key not in positions:
            positions[key] = len(kept)
            kept.append(index)
            if retrieval:
                latest[key] = retrieval[1]
        elif retrieval and retrieval[1] > latest[key]:
            kept[positions[key]] = index
            latest[key] = retrieval[1]
    return kept
