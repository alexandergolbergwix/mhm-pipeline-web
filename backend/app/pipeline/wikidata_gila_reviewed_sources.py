"""Bind reviewed requirement 8 proof to one original catalogue occurrence."""

import json
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from app.pipeline import marc_occurrences as marc

_DATA = Path(__file__).resolve().parents[1] / "data" / "gila_reviewed_source_proof.json"


@lru_cache(maxsize=1)
def _reviewed_sources():
    entries = json.loads(_DATA.read_text(encoding="utf-8"))
    index = {(row["control_number"], row["occurrence"]): row for row in entries}
    if len(index) != len(entries):
        raise ValueError("The source proof contains repeated MARC occurrences.")
    return index


def reviewed_source_items():
    return {row["value"] for row in _reviewed_sources().values() if row["type"] == "item"}


def _digest(text):
    return sha256(text.strip().encode("utf-8")).hexdigest()


def reviewed_source_proof(cn, occurrence, text, entity, source_rows=()):
    proof = _reviewed_sources().get((cn, occurrence))
    if (
        proof and entity and entity.get("id") == proof["qid"]
        and _digest(text) == proof["source_sha256"]
    ):
        note = proof.get("supporting_note")
        if note and not any(
            marc.source_key(row) == note["occurrence"]
            and note["source_sha256"] in {_digest(value) for value in marc.values(row, "a")}
            for row in source_rows
        ):
            return None
        return proof
    return None
