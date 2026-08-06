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


class TestOneFidelityDecisionPerRow:
    """The rebuild regression: gating only the Hebrew slot split the item in two.

    Run 48ba6c13's rebuild shipped `en="Sara Molho"` beside `he="שבתי מולכו"` —
    the Latin heading from the very authority row whose Hebrew heading had just
    been refused. An internally contradictory item is worse than a consistently
    uncertain one, and the judge failed it for naming two different people.
    """

    @staticmethod
    def _person(pref_heb: str, pref_lat: str, marc: str):
        from converter.wikidata.item_builder import WikidataItemBuilder

        record = {
            "_control_number": "990001404380205171",
            "control_number": "990001404380205171",
            "title": "ספר", "shelfmark": "F 1",
            "contributors": [{"name": marc, "role": "מעתיק", "field": "700"}],
            "marc_authority_matches": [{
                "name": marc, "entity_text": marc, "role": "מעתיק",
                "mazal_id": "987007299516905171",
                "preferred_name_heb": pref_heb,
                "preferred_name_lat": pref_lat,
                "approved": True,
            }],
        }
        people = [
            i for i in WikidataItemBuilder().build_all([record])
            if (i.entity_type or "") == "person"
        ]
        assert people, "expected a person item"
        return people[0]

    def test_a_refused_row_supplies_neither_label(self) -> None:
        person = self._person("מולכו, שרה", "Molho, Sara", "מולכו, שבתי")
        assert person.labels.get("en") != "Sara Molho"
        assert "Sara Molho" in person.aliases.get("en", [])
        assert person.heading_mismatch

    def test_the_two_label_slots_never_name_different_people(self) -> None:
        person = self._person("מולכו, שרה", "Molho, Sara", "מולכו, שבתי")
        # Both slots now derive from the MARC heading, or the en slot is absent.
        assert not (
            person.labels.get("en") == "Sara Molho"
            and "שבתי" in str(person.labels.get("he") or "")
        )

    def test_a_trusted_row_still_supplies_both(self) -> None:
        person = self._person("מולכו, שבתי", "Molho, Shabtai", "מולכו, שבתי")
        assert person.heading_mismatch is None
        assert person.labels.get("en") == "Shabtai Molho"


class TestPersonMergesMayNotUnionTwoIdentities:
    """Rule W-166's label change quietly loosened label-keyed person matching.

    54 persons vanished from run 48ba6c13's rebuild — 39 absorbed into another
    person and 15 taking their only publishable identifier with them — because two
    persons that previously had different labels now shared the MARC-derived one.
    A corpus-wide matching change is what the homonym flag exists to gate; it must
    not arrive through a label key instead.
    """

    @staticmethod
    def _person(local_id: str, *, label: str, pid: str, value: str):
        from converter.wikidata.item_models import WikidataItem, WikidataStatement

        return WikidataItem(
            local_id=local_id,
            entity_type="person",
            labels={"he": label},
            statements=[
                WikidataStatement(property_id="P31", value="Q5", value_type="item"),
                WikidataStatement(property_id=pid, value=value, value_type="string"),
            ],
        )

    def test_two_identified_people_sharing_a_label_do_not_merge(self) -> None:
        from app.pipeline.wikidata_canonical_enrichment import merge_legacy_into_canonical

        a = self._person("mazal:1", label="גבאי, יצחק", pid="P8189", value="1")
        b = self._person("mazal:2", label="גבאי, יצחק", pid="P8189", value="2")
        merged = merge_legacy_into_canonical([a], [b])
        assert len(merged) == 2, "a shared heading is the homonym problem, not identity"

    def test_a_merge_never_loses_a_publishable_identifier(self) -> None:
        from app.pipeline.wikidata_canonical_enrichment import merge_legacy_into_canonical

        a = self._person("mazal:1", label="גבאי, יצחק", pid="P8189", value="1")
        b = self._person("mazal:1b", label="גבאי, יצחק", pid="P214", value="61512894")
        before = {
            (s.property_id, s.value)
            for item in (a, b) for s in item.statements
            if s.property_id in {"P214", "P8189"}
        }
        merged = merge_legacy_into_canonical([a], [b])
        after = {
            (s.property_id, s.value)
            for item in merged for s in item.statements
            if s.property_id in {"P214", "P8189"}
        }
        assert before <= after

    def test_an_identifierless_stub_still_merges_into_an_identified_person(self) -> None:
        """The case this merge exists for."""
        from app.pipeline.wikidata_canonical_enrichment import merge_legacy_into_canonical
        from converter.wikidata.item_models import WikidataItem, WikidataStatement

        identified = self._person("mazal:1", label="גבאי, יצחק", pid="P8189", value="1")
        stub = WikidataItem(
            local_id="person::gabbai",
            entity_type="person",
            labels={"he": "גבאי, יצחק"},
            statements=[
                WikidataStatement(property_id="P31", value="Q5", value_type="item"),
            ],
        )
        assert len(merge_legacy_into_canonical([identified], [stub])) == 1

    def test_matching_identifiers_still_merge(self) -> None:
        from app.pipeline.wikidata_canonical_enrichment import merge_legacy_into_canonical

        a = self._person("mazal:1", label="גבאי, יצחק", pid="P8189", value="1")
        b = self._person("mazal:1b", label="גבאי, יצחק אחר", pid="P8189", value="1")
        assert len(merge_legacy_into_canonical([a], [b])) == 1
