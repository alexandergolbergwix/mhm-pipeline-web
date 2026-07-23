#!/usr/bin/env python3
"""Fail-closed shadow gate for legacy versus canonical HMO RDF."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline.projection_shadow_compare import compare_rdf_projections
from app.pipeline.hmo_canonical_readiness import CanonicalReadiness

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy", type=Path)
    parser.add_argument("canonical", type=Path)
    parser.add_argument(
        "--canonical-readiness", type=Path,
        help="JSON readiness result from check_hmo_canonical_gate.py",
    )
    parser.add_argument("--allow-difference", action="store_true", help="diagnostic compatibility mode; never promotes a projection")
    args = parser.parse_args()
    result = compare_rdf_projections(args.legacy, args.canonical)
    result["diagnostic_only"] = bool(args.allow_difference)
    readiness = None
    if args.canonical_readiness:
        readiness = CanonicalReadiness.from_dict(json.loads(args.canonical_readiness.read_text(encoding="utf-8")))
        result["canonical_readiness"] = readiness.to_dict()
    result["promotion_ready"] = bool(result["identical"] and (readiness is None or readiness.ready))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["promotion_ready"] else 1

if __name__ == "__main__": raise SystemExit(main())
