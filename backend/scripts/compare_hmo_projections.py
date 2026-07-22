#!/usr/bin/env python3
"""Compare legacy and canonical RDF projection files read-only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.pipeline.projection_shadow_compare import compare_rdf_projections


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy", type=Path)
    parser.add_argument("canonical", type=Path)
    args = parser.parse_args()
    print(json.dumps(compare_rdf_projections(args.legacy, args.canonical), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
