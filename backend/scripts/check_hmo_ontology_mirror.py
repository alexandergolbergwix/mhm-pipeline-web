#!/usr/bin/env python3
"""Compare ontology URI JSON with a Wikibase schema mapping JSON export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.pipeline.hmo_ontology_mirror import compare_ontology_mirror


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ontology", type=Path, help="JSON list of ontology URIs")
    parser.add_argument("mapping", type=Path, help="JSON list or {ontology_uri: wikibase_id}")
    args = parser.parse_args()
    if args.ontology.suffix.lower() == ".ttl":
        from converter.wikibase.ontology_schema_reader import read_hmo_schema
        schema = read_hmo_schema(args.ontology)
        ontology = [entry.uri for entry in (*schema.classes, *schema.properties)]
    else:
        ontology = json.loads(args.ontology.read_text(encoding="utf-8"))
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    mapped = list(mapping.keys()) if isinstance(mapping, dict) else mapping
    print(json.dumps(compare_ontology_mirror(ontology, mapped), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
