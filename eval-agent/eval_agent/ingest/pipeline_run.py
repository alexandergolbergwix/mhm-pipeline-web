"""Discover and validate an MHM-Pipeline output directory.

A pipeline run is a directory containing ``marc_extracted.json`` (from
Stage 1) and one evaluator-specific JSON file. The eval-agent
never imports any pipeline Python code — this module is the only
contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelineRun:
    root: Path
    marc_extract: Path
    ner_results: Path | None
    authority_results: Path | None = None
    wikidata_items: Path | None = None
    hmo_wikibase_schema: Path | None = None
    hmo_wikibase_items: Path | None = None


def discover(root: str | Path) -> PipelineRun:
    """Validate ``root`` is a pipeline output dir; return its key paths.

    Requires ``marc_extracted.json`` plus at least one of
    ``ner_results.json`` (Stage 2), ``authority_enriched.json``
    (Stage 3), ``wikidata_items.json`` (Wikidata Studio), or
    ``hmo_wikibase_schema.json`` (HMO Wikibase Studio schema
    bootstrap). Raises ``FileNotFoundError`` otherwise.
    """
    root_path = Path(root).expanduser().resolve()
    marc = root_path / "marc_extracted.json"
    ner = root_path / "ner_results.json"
    authority = root_path / "authority_enriched.json"
    wikidata = root_path / "wikidata_items.json"
    hmo_schema = root_path / "hmo_wikibase_schema.json"
    hmo_items = root_path / "hmo_wikibase_items.json"

    ner_path = ner if ner.is_file() else None
    authority_path = authority if authority.is_file() else None
    wikidata_path = wikidata if wikidata.is_file() else None
    hmo_schema_path = hmo_schema if hmo_schema.is_file() else None
    hmo_items_path = hmo_items if hmo_items.is_file() else None

    missing: list[str] = []
    if not marc.is_file():
        missing.append("marc_extracted.json")
    if (
        ner_path is None
        and authority_path is None
        and wikidata_path is None
        and hmo_schema_path is None
        and hmo_items_path is None
    ):
        missing.append(
            "ner_results.json, authority_enriched.json, wikidata_items.json, "
            "hmo_wikibase_schema.json, or hmo_wikibase_items.json"
        )
    if missing:
        raise FileNotFoundError(
            f"pipeline output dir at {root_path} is missing: {', '.join(missing)}"
        )
    return PipelineRun(
        root=root_path,
        marc_extract=marc,
        ner_results=ner_path,
        authority_results=authority_path,
        wikidata_items=wikidata_path,
        hmo_wikibase_schema=hmo_schema_path,
        hmo_wikibase_items=hmo_items_path,
    )
