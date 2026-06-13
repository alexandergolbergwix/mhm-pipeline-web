"""List every Wikidata item a user CREATED, via the MediaWiki API.

Authoritative source for "the items I created" — used to scope the 2026-06
Ktiv bulk-deletion request on ``User talk:Alexander Goldberg IL``. The QID
range in that request (Q138937341–Q139231612) is only the bounds; this script
asks Wikidata directly what was actually created, so the list is exact.

How it works:
  - ``action=query&list=usercontribs&ucshow=new&ucnamespace=0`` returns ONLY
    page-creation edits in the item namespace, paginated. That is precisely
    "items this user created".
  - Already-DELETED items do not appear in public contributions — which is
    what we want (the deletion request excludes already-deleted ones).
  - Items that were MERGED still exist as redirect pages, so they DO appear.
    With ``--check-redirects`` the script splits the list into standalone
    items (the real deletion targets) and redirects (already merged away).

This is READ-ONLY. It never writes to Wikidata.

Outputs (into ``--out-dir``, default = current directory):
  - ``created_qids.txt``            every created QID, one per line, sorted
  - ``created_qids_standalone.txt`` (only with --check-redirects) live items
  - ``created_qids_redirects.txt``  (only with --check-redirects) merged-away

Invoke (from inside ``backend/``)::

    python -m scripts.list_created_wikidata_items --user "Alexander Goldberg IL"
    python -m scripts.list_created_wikidata_items --user "Alexander Goldberg IL" --check-redirects
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import requests

API = "https://www.wikidata.org/w/api.php"
USER_AGENT = (
    "MHMPipeline-cleanup/0.1 "
    "(https://github.com/alexgoldberg/mhm-pipeline; alexander.goldberg@biu.ac.il) "
    "listing own created items for a bulk-deletion request"
)
_QID_BATCH = 50          # API title-query limit for normal accounts
_PAGINATE_SLEEP = 0.1    # be polite between paginated calls


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return s


def fetch_created_qids(session: requests.Session, user: str) -> list[str]:
    """Return every item-namespace page the user created (ucshow=new)."""
    qids: list[str] = []
    params: dict[str, str | int] = {
        "action": "query",
        "list": "usercontribs",
        "ucuser": user,
        "ucnamespace": 0,        # item namespace
        "ucshow": "new",         # only page-creation edits
        "ucprop": "title|timestamp",
        "uclimit": 500,          # max for non-bot accounts
        "format": "json",
        "formatversion": 2,
    }
    page = 0
    while True:
        resp = session.get(API, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"API error: {data['error']}")
        contribs = data.get("query", {}).get("usercontribs", [])
        for c in contribs:
            title = c.get("title", "")
            if title.startswith("Q") and title[1:].isdigit():
                qids.append(title)
        page += 1
        print(f"  …page {page}: {len(qids)} creations so far", file=sys.stderr)
        cont = data.get("continue")
        if not cont:
            break
        params.update(cont)
        time.sleep(_PAGINATE_SLEEP)
    return qids


def partition_redirects(
    session: requests.Session, qids: list[str],
) -> tuple[list[str], list[str]]:
    """Split *qids* into (standalone, redirects) by querying current page info.

    A merged item is now a redirect; ``prop=info`` marks it with a ``redirect``
    key. Standalone items are the genuine deletion targets.
    """
    standalone: list[str] = []
    redirects: list[str] = []
    total = len(qids)
    for i in range(0, total, _QID_BATCH):
        chunk = qids[i : i + _QID_BATCH]
        resp = session.get(
            API,
            params={
                "action": "query",
                "prop": "info",
                "titles": "|".join(chunk),
                "format": "json",
                "formatversion": 2,
            },
            timeout=30,
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", [])
        for p in pages:
            title = p.get("title", "")
            if "missing" in p:
                continue  # deleted since the contribs snapshot — skip
            if "redirect" in p:
                redirects.append(title)
            else:
                standalone.append(title)
        print(f"  …checked {min(i + _QID_BATCH, total)}/{total}", file=sys.stderr)
        time.sleep(_PAGINATE_SLEEP)
    return standalone, redirects


def _qid_num(qid: str) -> int:
    return int(qid[1:])


def _write(path: Path, qids: list[str]) -> None:
    path.write_text("\n".join(sorted(qids, key=_qid_num)) + "\n", encoding="utf-8")
    print(f"  wrote {len(qids):>5} → {path}")


def _write_csv(path: Path, status_by_qid: dict[str, str]) -> None:
    """Write ``qid,status,url`` sorted by QID number."""
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["qid", "status", "url"])
        for qid in sorted(status_by_qid, key=_qid_num):
            writer.writerow([qid, status_by_qid[qid], f"https://www.wikidata.org/wiki/{qid}"])
    print(f"  wrote {len(status_by_qid):>5} → {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", required=True, help='Wikidata username, e.g. "Alexander Goldberg IL"')
    ap.add_argument("--out-dir", default=".", help="Directory for the output files (default: cwd)")
    ap.add_argument(
        "--check-redirects",
        action="store_true",
        help="Also split the list into standalone items vs already-merged redirects",
    )
    args = ap.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    session = _session()

    print(f"Fetching item creations by {args.user!r} …", file=sys.stderr)
    qids = fetch_created_qids(session, args.user)
    if not qids:
        print("No created items found — check the username spelling.", file=sys.stderr)
        return 1

    qids_sorted = sorted(set(qids), key=_qid_num)
    _write(out_dir / "created_qids.txt", qids_sorted)

    print("\n── Summary ─────────────────────────────────────────────")
    print(f"  user:           {args.user}")
    print(f"  items created:  {len(qids_sorted)}  (still existing; deleted ones excluded)")
    print(f"  QID range:      {qids_sorted[0]} … {qids_sorted[-1]}")

    status_by_qid: dict[str, str] = {q: "created" for q in qids_sorted}
    if args.check_redirects:
        print("\nChecking which are now redirects (merged away) …", file=sys.stderr)
        standalone, redirects = partition_redirects(session, qids_sorted)
        _write(out_dir / "created_qids_standalone.txt", standalone)
        _write(out_dir / "created_qids_redirects.txt", redirects)
        status_by_qid = {q: "standalone" for q in standalone}
        status_by_qid.update({q: "redirect" for q in redirects})
        print(f"  standalone items (real deletion targets): {len(standalone)}")
        print(f"  redirects (already merged):               {len(redirects)}")

    _write_csv(out_dir / "created_qids.csv", status_by_qid)

    print("────────────────────────────────────────────────────────")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
