"""Shared local-identifier helpers for the offline Wikibase draft modules.

Used by both :mod:`hmo_exporter` (RDF instance drafts) and
:mod:`ontology_schema_reader` (RDF schema/class/property drafts) so the
slugging rules never drift between the two.
"""

from __future__ import annotations

import re

from rdflib.term import Node


def local_name(node: Node) -> str:
    """Extract the final local component of an RDF URI-like node."""
    text = str(node)
    if "#" in text:
        return text.rsplit("#", maxsplit=1)[-1]
    if "/" in text:
        return text.rstrip("/").rsplit("/", maxsplit=1)[-1]
    return text


def safe_local_id(value: str) -> str:
    """Normalise an RDF local name into a safe local draft identifier."""
    cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", value).strip("_")
    return cleaned or "Entity"
