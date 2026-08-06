"""Rule W-168 — an item whose identifier already exists on Wikidata is an UPDATE.

The probe found 15 items in run 48ba6c13 whose own authority or catalog identifier
is already carried by a live Wikidata item: 14 persons on P8189 (Q55913805,
Q2820095, Q1970330, …) and one manuscript on P3959. The judge failed every one,
correctly, and told the curator to link instead of create. Re-keying a QID the
probe has already resolved is work the pipeline can do — and leaving the item as a
CREATE keeps the duplicate risk on the board.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.pipeline.wikidata_duplicate_probe import (
    adopt_identifier_matched_duplicates,
)


def _item(local_id: str, *candidates: dict, status: str = "candidates_found", **extra):
    item = {
        "local_id": local_id,
        "_local_id": local_id,
        "entity_type": "person",
        "_wikidata_existence": {"status": status, "candidates": list(candidates)},
    }
    item.update(extra)
    return item


def _identifier(qid: str, pid: str = "P8189", value: str = "987007267925005171") -> dict:
    return {"qid": qid, "matched_on": f"{pid}={value}", "label": "x"}


class TestAdoption:
    def test_an_identifier_match_becomes_an_update(self) -> None:
        item = _item("QDraft_Person_130", _identifier("Q55913805"))
        adopted = adopt_identifier_matched_duplicates([item])
        assert item["existing_qid"] == "Q55913805"
        assert adopted == [{
            "local_id": "QDraft_Person_130",
            "qid": "Q55913805",
            "matched_on": "P8189=987007267925005171",
        }]

    def test_a_catalog_id_match_works_for_manuscripts(self) -> None:
        item = _item(
            "QDraft_MS_1",
            _identifier("Q134603946", pid="P3959", value="990001343040205171"),
            entity_type="manuscript",
        )
        adopt_identifier_matched_duplicates([item])
        assert item["existing_qid"] == "Q134603946"

    def test_a_verified_composite_conjunction_is_an_identity(self) -> None:
        """W-144: holder+shelfmark identifies a manuscript; the AND was verified."""
        item = _item(
            "QDraft_MS_2",
            {"qid": "Q999", "matched_on": "P195=Q1028334 AND P217=F 18760"},
            entity_type="manuscript",
        )
        adopt_identifier_matched_duplicates([item])
        assert item["existing_qid"] == "Q999"

    def test_the_reason_is_recorded_on_the_item(self) -> None:
        item = _item("QDraft_Person_130", _identifier("Q55913805"))
        adopt_identifier_matched_duplicates([item])
        adoption = item["_wikidata_existence"]["adoption"]
        assert adoption["adopted"] is True
        assert adoption["qid"] == "Q55913805"
        assert "ownership" in adoption["note"]


class TestItStaysFailClosed:
    def test_a_title_match_is_never_adopted(self) -> None:
        """A title is a likeness, not an identity (Rule W-145)."""
        item = _item(
            "QDraft_Work_1",
            {
                "qid": "Q623354",
                "matched_on": "title~הגדה של פסח AND P31=Q47461344",
                "requires_curator_confirmation": "true",
            },
            entity_type="work",
        )
        assert adopt_identifier_matched_duplicates([item]) == []
        assert "existing_qid" not in item

    def test_a_candidate_requiring_confirmation_is_never_adopted(self) -> None:
        item = _item(
            "QDraft_Person_1",
            {**_identifier("Q1"), "requires_curator_confirmation": "true"},
        )
        assert adopt_identifier_matched_duplicates([item]) == []

    def test_two_identity_matches_are_ambiguous_and_left_to_the_curator(self) -> None:
        item = _item(
            "QDraft_Person_2",
            _identifier("Q1"),
            _identifier("Q2", value="987007267925005172"),
        )
        assert adopt_identifier_matched_duplicates([item]) == []
        assert "existing_qid" not in item
        adoption = item["_wikidata_existence"]["adoption"]
        assert adoption["adopted"] is False
        assert adoption["candidates"] == ["Q1", "Q2"]

    def test_an_item_that_already_targets_a_qid_is_untouched(self) -> None:
        item = _item("QDraft_Person_3", _identifier("Q1"), existing_qid="Q42")
        assert adopt_identifier_matched_duplicates([item]) == []
        assert item["existing_qid"] == "Q42"

    def test_a_non_conclusive_status_is_never_adopted(self) -> None:
        for status in ("absent", "skipped", "not_run", "not_probed", "unavailable"):
            item = _item("QDraft_Person_4", _identifier("Q1"), status=status)
            assert adopt_identifier_matched_duplicates([item]) == []

    def test_adoption_does_not_authorise_the_write(self) -> None:
        """Ownership is still classified at upload; a foreign item is blocked (W-99)."""
        item = _item("QDraft_Person_130", _identifier("Q55913805"))
        adopt_identifier_matched_duplicates([item])
        assert "accept_foreign_modify" not in item
        assert "accepted_foreign_qid" not in item


@dataclass
class _Native:
    local_id: str = ""
    existing_qid: str | None = None
    entity_type: str = "person"


class TestBothItemShapesAreTold:
    def test_the_native_item_learns_the_qid_too(self) -> None:
        """The dicts drive the table; the native items drive upload."""
        import asyncio
        from unittest.mock import patch

        from app.routers.wikidata_studio import _adopt_probed_duplicate_qids

        serialised = [{
            "local_id": "QDraft_Person_130",
            "entity_type": "person",
            "statements": [{"property": "P8189", "value": "987007267925005171"}],
        }]
        native = [_Native(local_id="QDraft_Person_130")]

        async def fake_attach(_factory, items):
            for it in items:
                it["_wikidata_existence"] = {
                    "status": "candidates_found",
                    "candidates": [_identifier("Q55913805")],
                }

        with patch(
            "app.pipeline.wikidata_duplicate_probe.attach_cached_duplicate_evidence",
            fake_attach,
        ):
            count = asyncio.run(_adopt_probed_duplicate_qids(serialised, native))

        assert count == 1
        assert serialised[0]["existing_qid"] == "Q55913805"
        assert native[0].existing_qid == "Q55913805"
        # The probe scratch field must not leak into the cached Studio row.
        assert "_wikidata_existence" not in serialised[0]

    def test_a_failure_leaves_every_item_as_a_create(self) -> None:
        import asyncio
        from unittest.mock import patch

        from app.routers.wikidata_studio import _adopt_probed_duplicate_qids

        serialised = [{"local_id": "QDraft_Person_130", "entity_type": "person"}]

        async def boom(_factory, _items):
            raise RuntimeError("cache unreachable")

        with patch(
            "app.pipeline.wikidata_duplicate_probe.attach_cached_duplicate_evidence",
            boom,
        ):
            assert asyncio.run(_adopt_probed_duplicate_qids(serialised, [])) == 0
        assert "existing_qid" not in serialised[0]
