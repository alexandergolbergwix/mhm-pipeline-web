"""Data structures shared by Wikidata item projection modules."""

from __future__ import annotations

from dataclasses import dataclass, field

from converter.wikidata.property_mapping import PRECISION_YEAR


@dataclass
class WikidataStatement:
    """A single Wikidata claim with qualifiers and references."""

    property_id: str
    value: str | int | float | None
    value_type: str
    qualifiers: list[dict[str, object]] = field(default_factory=list)
    references: list[dict[str, str]] = field(default_factory=list)
    precision: int = PRECISION_YEAR
    language: str = "he"
    unit: str = ""
    rank: str = "normal"


@dataclass
class WikidataItem:
    """A projected Wikidata item ready for validation and upload."""

    labels: dict[str, str] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    statements: list[WikidataStatement] = field(default_factory=list)
    existing_qid: str | None = None
    entity_type: str = ""
    local_id: str = ""
    records: list[str] = field(default_factory=list)
    authority_evidence: list[dict[str, object]] = field(default_factory=list)
    work_candidate_evidence: list[dict[str, object]] = field(default_factory=list)
