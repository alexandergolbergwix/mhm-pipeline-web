"""Sample KIMA place matches and report abstentions for collision review."""
from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

from converter.authority.kima_index import KimaIndex


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tsv", type=Path)
    parser.add_argument("--kima-db", type=Path, default=Path("data/kima/kima_index.db"))
    parser.add_argument("--sample", type=int, default=200)
    args = parser.parse_args()
    names: list[str] = []
    place_columns = {
        "260$a", "260$b", "264$a", "264$b", "651$a", "651$z",
        "752$a", "752$b", "752$c", "752$d",
    }
    with args.tsv.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            for key, value in row.items():
                if key not in place_columns:
                    continue
                if value and value.strip():
                    names.append(value.strip().strip('"'))
            if len(names) >= args.sample * 10:
                break
    sample = random.Random(83).sample(names, min(args.sample, len(names)))
    index = KimaIndex(str(args.kima_db))
    hits = sum(1 for name in sample if index.lookup_place(name))
    index.close()
    print({"checked": len(sample), "kima_hits": hits, "abstained_or_missed": len(sample) - hits})


if __name__ == "__main__":
    main()
