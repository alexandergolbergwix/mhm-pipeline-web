"""Preflight gate for enabling canonical-first HMO projections."""
from __future__ import annotations
import argparse, asyncio, json, sys, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline.projection_shadow_compare import compare_rdf_projections
from app.settings import Settings
from scripts.check_hmo_canonical_gate import inspect as inspect_canonical

async def main_async(args: argparse.Namespace) -> int:
    canonical = await inspect_canonical(args.run_id)
    settings = Settings()
    rollout_enabled = bool(args.run_id and settings.canonical_first_for_run(args.run_id))
    result = {"canonical": canonical, "shadow": None, "rollout_enabled": rollout_enabled, "ready": bool(canonical["ready"])}
    if args.legacy and args.canonical:
        shadow = compare_rdf_projections(args.legacy, args.canonical)
        result["shadow"] = shadow
        result["ready"] = bool(result["ready"] and shadow["identical"])
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["ready"] else 1

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=uuid.UUID)
    parser.add_argument("--legacy", type=Path)
    parser.add_argument("--canonical", type=Path)
    args = parser.parse_args()
    if bool(args.legacy) != bool(args.canonical): parser.error("--legacy and --canonical must be supplied together")
    return asyncio.run(main_async(args))
if __name__ == "__main__": raise SystemExit(main())
