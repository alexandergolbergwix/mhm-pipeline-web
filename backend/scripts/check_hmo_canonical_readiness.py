#!/usr/bin/env python3
"""Check a JSON HMO snapshot/export before canonical projection cutover."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from app.pipeline.hmo_canonical_readiness import check


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
    items = payload if isinstance(payload, list) else payload.get("items", [])
    print(json.dumps(check(items), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
