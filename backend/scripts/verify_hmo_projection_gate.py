#!/usr/bin/env python3
"""Fail-closed shadow gate for legacy versus canonical HMO RDF."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline.projection_shadow_compare import compare_rdf_projections

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy", type=Path)
    parser.add_argument("canonical", type=Path)
    parser.add_argument("--allow-difference", action="store_true", help="report differences without failing")
    args = parser.parse_args()
    result = compare_rdf_projections(args.legacy, args.canonical)
    result["promotion_ready"] = bool(result["identical"] or args.allow_difference)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["promotion_ready"] else 1

if __name__ == "__main__": raise SystemExit(main())
