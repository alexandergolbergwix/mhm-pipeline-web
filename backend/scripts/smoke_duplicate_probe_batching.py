"""Verify the batched duplicate-probe queries against the live Action API.

Read-only. Nothing here writes to Wikidata, to Postgres, or to the probe cache.

Why this exists: `probe_titles_batch` ORs `inlabel:` clauses and
`probe_composites_batch` ORs quoted `haswbstatement:` values. Both syntaxes are
accepted by CirrusSearch in principle, but quoting inside a `|`-joined
`haswbstatement` list is the kind of thing that silently returns nothing rather
than erroring — which would read as "no duplicate exists" for a whole corpus.
So the batched form is checked against the single-key form it replaces, and the
batched composite path stays behind
``WIKIDATA_DUPLICATE_PROBE_BATCH_COMPOSITE`` until this passes.

    cd backend && .venv/bin/python -m scripts.smoke_duplicate_probe_batching
"""

from __future__ import annotations

import sys

from app.pipeline.wikidata_duplicate_probe import (
    _COMPOSITE_SEP,
    probe_composites_batch,
    probe_titles_batch,
    probe_title,
    probe_composite,
)

# Known-live fixtures. Titles that exist as written works on Wikidata, and a
# holder+shelfmark pair from the Samaritan corpus that motivated Rule W-144.
_TITLE_CLASS = "Q47461344"  # written work
_TITLES = ["Passover Haggadah", "Mishneh Torah"]
_COMPOSITES = [("Q1028334", "F 18760")]


def _check_titles() -> list[str]:
    failures: list[str] = []
    keys = [
        ("title+P31", _COMPOSITE_SEP.join((title, _TITLE_CLASS)))
        for title in _TITLES
    ]
    batched = probe_titles_batch(keys)
    for key in keys:
        title = key[1].split(_COMPOSITE_SEP)[0]
        single = {c["qid"] for c in probe_title(key[0], key[1])}
        grouped = {c["qid"] for c in batched.get(key, [])}
        # The batched query may rank differently, so the check is that batching
        # does not LOSE a candidate the single query found.
        missing = single - grouped
        print(f"  title {title!r}: single={len(single)} batched={len(grouped)}")
        if missing:
            failures.append(f"title {title!r} lost candidates when batched: {missing}")
    return failures


def _check_composites() -> list[str]:
    failures: list[str] = []
    keys = [
        ("P195+P217", _COMPOSITE_SEP.join(pair)) for pair in _COMPOSITES
    ]
    batched = probe_composites_batch(keys)
    for key in keys:
        single = {c["qid"] for c in probe_composite(key[0], key[1])}
        grouped = {c["qid"] for c in batched.get(key, [])}
        print(f"  composite {key[1]!r}: single={len(single)} batched={len(grouped)}")
        if single - grouped:
            failures.append(
                f"composite {key[1]!r} lost candidates when batched: {single - grouped}",
            )
        if grouped - single:
            # The batched form must never be LESS strict than the AND it replaces.
            failures.append(
                f"composite {key[1]!r} gained candidates when batched: {grouped - single}",
            )
    return failures


def main() -> int:
    print("checking batched title probes…")
    failures = _check_titles()
    print("checking batched composite probes…")
    failures.extend(_check_composites())
    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nOK — batched queries agree with the single-key queries they replace.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
