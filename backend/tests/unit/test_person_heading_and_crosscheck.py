"""Rule W-166 — an unconfirmed authority identity may not name or date a person.

`mazal:987007299516905171` shipped labelled `יצחק בן שלמה בן חיים גבאי` with a
death year of 1640, for a manuscript created 1655-1660 whose MARC contributor is
`גבאי, טוביה בן חיים יצחק` (role: מעתיק, scribe). The authority row already
carried `wikidata_crosscheck_fail`. Two independent gaps let it through: the flag
was in neither hard-reject set, and `preferred_name_heb` overwrote the MARC
heading with no comparison at all.
"""

from __future__ import annotations

import pytest

from converter.authority.heading_fidelity import heading_matches, heading_mismatch_reason

_MARC_SCRIBE = "גבאי, טוביה בן חיים יצחק"
_MAZAL_ROW = "יצחק בן שלמה בן חיים גבאי"


class TestHeadingFidelity:
    def test_the_wrong_gabbai_is_refused(self) -> None:
        """Same family, same two patronymics — different given name."""
        assert not heading_matches(_MARC_SCRIBE, _MAZAL_ROW)

    def test_the_reason_names_the_given_name(self) -> None:
        reason = heading_mismatch_reason(_MARC_SCRIBE, _MAZAL_ROW)
        assert "given name" in reason
        assert "טוביה" in reason and "יצחק" in reason

    @pytest.mark.parametrize(
        ("marc", "authority"),
        [
            ("ויטל, חיים בן יוסף", "חיים ויטל"),
            ("לוריא, יצחק", "יצחק לוריא"),
            ("פנצירי, אפרים", "אפרים פנציירי"),        # spelling variant
            ("גבאי, יצחק בן שלמה", _MAZAL_ROW),        # the RIGHT Gabbai
        ],
    )
    def test_the_same_person_still_matches(self, marc, authority) -> None:
        assert heading_matches(marc, authority)

    def test_a_different_family_is_refused(self) -> None:
        assert not heading_matches("כהן, משה", "משה לוי")

    def test_an_empty_heading_never_matches(self) -> None:
        assert not heading_matches("", _MAZAL_ROW)
        assert not heading_matches(_MARC_SCRIBE, "")


class TestProjectionKeepsTheMarcHeading:
    @staticmethod
    def _person(pref_heb: str, *, flags: list[str] | None = None):
        from converter.wikidata.item_builder import WikidataItemBuilder

        record = {
            "_control_number": "990001404380205171",
            "control_number": "990001404380205171",
            "title": "ספר",
            "shelfmark": "F 1",
            "contributors": [
                {"name": _MARC_SCRIBE, "role": "מעתיק", "field": "700"},
            ],
            "marc_authority_matches": [{
                "name": _MARC_SCRIBE,
                "entity_text": _MARC_SCRIBE,
                "role": "מעתיק",
                "mazal_id": "987007299516905171",
                "preferred_name_heb": pref_heb,
                "death_year": 1640,
                "dates": "-1640",
                "guard_flags": flags or [],
                "approved": True,
            }],
        }
        items = WikidataItemBuilder().build_all([record])
        return [i for i in items if (i.entity_type or "") == "person"]

    def test_a_mismatched_authority_heading_does_not_take_the_label(self) -> None:
        people = self._person(_MAZAL_ROW)
        assert people, "expected a person item"
        person = people[0]
        assert person.labels.get("he") != _MAZAL_ROW
        assert _MAZAL_ROW in person.aliases.get("he", [])
        assert person.heading_mismatch
        assert "given name" in str(person.heading_mismatch["reason"])

    def test_a_matching_authority_heading_still_wins(self) -> None:
        people = self._person("גבאי, טוביה בן חיים יצחק")
        assert people
        assert people[0].heading_mismatch is None

    def test_the_crosscheck_flag_suppresses_the_dates(self) -> None:
        people = self._person(_MAZAL_ROW, flags=["wikidata_crosscheck_fail"])
        assert people
        pids = {s.property_id for s in people[0].statements}
        assert "P570" not in pids
        assert "P569" not in pids


class TestCanonicalPathSuppressesUnconfirmedDates:
    @staticmethod
    def _item(flags: list[str]):
        from converter.wikidata.item_models import WikidataItem, WikidataStatement

        return WikidataItem(
            local_id="mazal:987007299516905171",
            entity_type="person",
            labels={"he": _MARC_SCRIBE},
            descriptions={"en": "Hebrew manuscript scribe (1580-1640)"},
            statements=[
                WikidataStatement(property_id="P31", value="Q5", value_type="item"),
                WikidataStatement(property_id="P8189", value="987007299516905171",
                                  value_type="string"),
                WikidataStatement(property_id="P570", value="+1640-00-00T00:00:00Z",
                                  value_type="time"),
            ],
            authority_evidence=[{"mazal_id": "1", "guard_flags": flags}],
        )

    def test_the_dates_and_their_description_are_suppressed(self) -> None:
        from app.pipeline.hmo_canonical_wikidata import (
            _suppress_unconfirmed_person_dates,
        )

        item = self._item(["wikidata_crosscheck_fail"])
        suppressed = _suppress_unconfirmed_person_dates([item])
        assert suppressed == ["mazal:987007299516905171"]
        assert not any(s.property_id == "P570" for s in item.statements)
        assert "1640" not in item.descriptions["en"]

    def test_the_item_itself_is_not_dropped(self) -> None:
        """It survives on its MARC attestation and its publishable P8189."""
        from app.pipeline.hmo_canonical_wikidata import (
            _HARD_REJECT_AUTHORITY_FLAGS,
            _SOFT_REJECT_AUTHORITY_FLAGS,
        )

        assert not (_SOFT_REJECT_AUTHORITY_FLAGS & _HARD_REJECT_AUTHORITY_FLAGS)

    def test_a_clean_person_keeps_its_dates(self) -> None:
        from app.pipeline.hmo_canonical_wikidata import (
            _suppress_unconfirmed_person_dates,
        )

        item = self._item([])
        assert _suppress_unconfirmed_person_dates([item]) == []
        assert any(s.property_id == "P570" for s in item.statements)
