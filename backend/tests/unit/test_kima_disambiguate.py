"""Fail-closed KIMA place disambiguation + cluster URI minting (W-84 / W-101)."""

from __future__ import annotations

from converter.authority.kima_disambiguate import (
    cluster_authority_same_as_uris,
    pick_kima_place_row,
)


def _norm(value: str) -> str:
    return value.strip().lower()


class TestPickKimaPlaceRow:
    def test_single_qid_accepts_first_row(self) -> None:
        rows = [
            {"primary_heb": "ירושלים", "wikidata_id": "Q1218", "kima_id": 1},
            {"primary_heb": "ירושלים (מחוז)", "wikidata_id": "Q1218", "kima_id": 2},
        ]
        picked = pick_kima_place_row(rows, "ירושלים", normalize_primary=_norm)
        assert picked is not None
        assert picked["kima_id"] == 1

    def test_conflicting_qids_abstain(self) -> None:
        # Both primaries normalize to the same query key after stripping
        # parentheticals — conflicting QIDs with no unique winner.
        rows = [
            {"primary_heb": "עזה (עיר)", "wikidata_id": "Q39760", "kima_id": 1},
            {"primary_heb": "עזה (מחוז)", "wikidata_id": "Q999", "kima_id": 2},
        ]

        def strip_paren(value: str) -> str:
            import re
            return re.sub(r"\s*\([^)]*\)\s*$", "", value).strip().lower()

        assert pick_kima_place_row(rows, "עזה", normalize_primary=strip_paren) is None

    def test_exact_primary_disambiguates_conflict(self) -> None:
        rows = [
            {"primary_heb": "עזה", "wikidata_id": "Q39760", "kima_id": 1},
            {"primary_heb": "עזה (מחוז)", "wikidata_id": "Q999", "kima_id": 2},
        ]
        picked = pick_kima_place_row(rows, "עזה", normalize_primary=_norm)
        assert picked is not None
        assert picked["wikidata_id"] == "Q39760"
        assert picked["kima_id"] == 1

    def test_empty_rows_abstain(self) -> None:
        assert pick_kima_place_row([], "x", normalize_primary=_norm) is None


class TestClusterAuthoritySameAs:
    def test_mints_known_namespaces(self) -> None:
        uris = cluster_authority_same_as_uris(
            {
                "gnd": "118540238",
                "lc": "n79021759",
                "isni": "0000 0001 2124 418X",
                "bnf": "11894274",
                "j9u": "987007263063205171",
            }
        )
        assert "https://d-nb.info/gnd/118540238" in uris
        assert "http://id.loc.gov/authorities/names/n79021759" in uris
        assert "https://isni.org/isni/000000012124418X" in uris
        assert "https://data.bnf.fr/ark:/12148/cb11894274" in uris
        assert "https://www.nli.org.il/en/authorities/987007263063205171" in uris

    def test_skips_empty(self) -> None:
        assert cluster_authority_same_as_uris({}) == []
        assert cluster_authority_same_as_uris(None) == []
