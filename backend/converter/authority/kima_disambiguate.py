"""Fail-closed KIMA place disambiguation (Rule W-84 / W-101).

A normalized place name may hit multiple KIMA rows. When those rows carry
conflicting Wikidata IDs, matching must abstain unless one exact primary
name uniquely disambiguates. Never pick an arbitrary first row.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

T = TypeVar("T", bound=Mapping[str, Any])


def pick_kima_place_row(
    rows: Sequence[T],
    normalized_query: str,
    *,
    normalize_primary: Callable[[str], str],
    primary_heb_key: str = "primary_heb",
    primary_rom_key: str = "primary_rom",
    wikidata_key: str = "wikidata_id",
    raw_query: str | None = None,
) -> T | None:
    """Return one unambiguous KIMA place row, or ``None`` to abstain."""
    if not rows:
        return None
    if not normalized_query:
        return None

    qids = {str(row.get(wikidata_key) or "").strip() for row in rows}
    qids.discard("")
    if len(qids) > 1:
        if raw_query and "(" in raw_query:
            import unicodedata

            raw_cf = " ".join(unicodedata.normalize("NFC", raw_query).split()).casefold()
            qual_exact = [
                row
                for row in rows
                if " ".join(unicodedata.normalize("NFC", str(row.get(primary_rom_key) or "")).split()).casefold() == raw_cf
                or " ".join(unicodedata.normalize("NFC", str(row.get(primary_heb_key) or "")).split()).casefold() == raw_cf
            ]
            qual_qids = {str(row.get(wikidata_key) or "").strip() for row in qual_exact}
            qual_qids.discard("")
            if len(qual_qids) == 1:
                chosen_qid = next(iter(qual_qids))
                for row in qual_exact:
                    if str(row.get(wikidata_key) or "").strip() == chosen_qid:
                        return row
                return qual_exact[0]

        exact = [
            row
            for row in rows
            if normalize_primary(str(row.get(primary_heb_key) or "")) == normalized_query
            or normalize_primary(str(row.get(primary_rom_key) or "")) == normalized_query
        ]
        exact_qids = {str(row.get(wikidata_key) or "").strip() for row in exact}
        exact_qids.discard("")
        if len(exact_qids) == 1:
            chosen_qid = next(iter(exact_qids))
            for row in exact:
                if str(row.get(wikidata_key) or "").strip() == chosen_qid:
                    return row
            return exact[0]
        return None
    return rows[0]


def cluster_authority_same_as_uris(cluster: Mapping[str, Any] | None) -> list[str]:
    """Mint owl:sameAs URIs from VIAF cluster ids (gnd/lc/isni/bnf/j9u)."""
    if not isinstance(cluster, Mapping):
        return []
    out: list[str] = []

    def add(uri: str) -> None:
        if uri and uri not in out:
            out.append(uri)

    gnd = str(cluster.get("gnd") or "").strip()
    if gnd:
        add(f"https://d-nb.info/gnd/{gnd}")

    lc = str(cluster.get("lc") or "").strip()
    if lc:
        add(f"http://id.loc.gov/authorities/names/{lc}")

    isni = str(cluster.get("isni") or "").strip().replace(" ", "")
    if isni:
        add(f"https://isni.org/isni/{isni}")

    bnf = str(cluster.get("bnf") or "").strip()
    if bnf:
        add(f"https://data.bnf.fr/ark:/12148/cb{bnf}")

    j9u = str(cluster.get("j9u") or "").strip()
    if j9u:
        add(f"https://www.nli.org.il/en/authorities/{j9u}")

    return out
