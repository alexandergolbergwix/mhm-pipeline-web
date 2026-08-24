"""Bidirectional person identity (Rule W-190)."""

from __future__ import annotations

from app.pipeline.wikidata_duplicate_probe import (
    _cross_script_names_compatible,
    adopt_identifier_matched_duplicates,
    person_heading_conflicts_live_label,
)
from app.pipeline.wikidata_upload import ForeignAccept, PreparedItem, _apply_existence_and_ownership


def test_victor_amadeus_savoy_leftover_refuses() -> None:
    assert not _cross_script_names_compatible(
        "ויטוריו אמדיאו",
        "Victor Amadeus II of Savoy",
    )


def test_david_solal_does_not_map_to_sultan() -> None:
    assert not _cross_script_names_compatible("דוד סולל", "David Sultan")


def test_kostlitz_leftover_refuses_ben_shaul() -> None:
    assert not _cross_script_names_compatible(
        "יהודה שאול בן דוד איש קוסטליץ",
        "Yehuda Ben-Shaul",
    )


def test_monson_and_curiel_and_briel_cover() -> None:
    assert _cross_script_names_compatible("אברהם מונסון", "Abraham Monson")
    assert _cross_script_names_compatible(
        "ישראל די קוריאל",
        "Israel ben Meir di Curiel",
    )
    assert _cross_script_names_compatible(
        "יהודה בן אליעזר בריאל",
        "Judah ben Eliezer Briel",
    )


def test_adopt_refuses_savoy_king() -> None:
    item = {
        "local_id": "QDraft_Person_71",
        "entity_type": "person",
        "labels": {"he": "ויטוריו אמדיאו"},
        "_wikidata_existence": {
            "status": "candidates_found",
            "candidates": [{
                "qid": "Q209579",
                "matched_on": "P8189=1",
                "label": "Victor Amadeus II of Savoy",
            }],
        },
    }
    assert adopt_identifier_matched_duplicates([item]) == []
    assert "existing_qid" not in item


def test_heading_conflicts_live_label_savoy() -> None:
    item = {
        "entity_type": "person",
        "labels": {"he": "ויטוריו אמדיאו"},
    }
    assert person_heading_conflicts_live_label(
        item, live_en="Victor Amadeus II of Savoy",
    )


def test_heading_conflicts_hayim_shor_not_avraham_hayim_shor() -> None:
    item = {
        "entity_type": "person",
        "labels": {"he": "חיים בן נפתלי הירש שור"},
    }
    assert person_heading_conflicts_live_label(
        item,
        live_en="Avraham Hayim Shor",
        live_he="אברהם חיים שור",
    )


def test_apply_identity_clash_clears_foreign_for_create(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_labels",
        lambda qids, *, is_test=False: {
            "Q209579": {"en": "Victor Amadeus II of Savoy"},
        },
    )

    class _Foreign:
        def _is_our_item(self, qid: str) -> bool:
            return False

    item = type("P", (), {
        "entity_type": "person",
        "existing_qid": "Q209579",
        "labels": {"he": "ויטוריו אמדיאו"},
        "authority_evidence": [],
    })()
    prepared = PreparedItem(
        item=item,
        local_id="QDraft_Person_71",
        label="ויטוריו אמדיאו",
        entity_type="person",
        existing_qid="Q209579",
        method="prebuilt",
        blocked=False,
        block_status="",
        block_message="",
    )
    out = _apply_existence_and_ownership(
        prepared,
        accept=ForeignAccept(),
        ownership_checker=_Foreign(),
        is_test=False,
    )
    assert out.blocked is False
    assert out.existing_qid is None
    assert out.block_status == "skipped"
    assert "cleared_identity_clash" in out.method
    assert "skipped" in out.method


def test_apply_identity_clash_blocks_own(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_labels",
        lambda qids, *, is_test=False: {
            "Q209579": {"en": "Victor Amadeus II of Savoy"},
        },
    )

    class _Own:
        def _is_our_item(self, qid: str) -> bool:
            return True

    item = type("P", (), {
        "entity_type": "person",
        "existing_qid": "Q209579",
        "labels": {"he": "ויטוריו אמדיאו"},
        "authority_evidence": [],
    })()
    prepared = PreparedItem(
        item=item,
        local_id="QDraft_Person_71",
        label="ויטוריו אמדיאו",
        entity_type="person",
        existing_qid="Q209579",
        method="prebuilt",
        blocked=False,
        block_status="",
        block_message="",
    )
    out = _apply_existence_and_ownership(
        prepared,
        accept=None,
        ownership_checker=_Own(),
        is_test=False,
    )
    assert out.blocked is True
    assert out.existing_qid == "Q209579"
    assert "W-190" in out.block_message


def test_identity_clash_strips_viaf_and_skips(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_labels",
        lambda qids, *, is_test=False: {
            "Q209579": {"en": "Victor Amadeus II of Savoy"},
        },
    )

    class _Foreign:
        def _is_our_item(self, qid: str) -> bool:
            return False

    stmt = type("S", (), {"property_id": "P214", "value": "123456"})()
    item = type("P", (), {
        "entity_type": "person",
        "existing_qid": "Q209579",
        "labels": {"he": "ויטוריו אמדיאו"},
        "statements": [stmt],
        "authority_evidence": [],
    })()
    prepared = PreparedItem(
        item=item,
        local_id="QDraft_Person_71",
        label="ויטוריו אמדיאו",
        entity_type="person",
        existing_qid="Q209579",
        method="prebuilt",
        blocked=False,
        block_status="",
        block_message="",
    )
    out = _apply_existence_and_ownership(
        prepared,
        accept=ForeignAccept(),
        ownership_checker=_Foreign(),
        is_test=False,
    )
    assert out.block_status == "skipped"
    assert not any(
        getattr(s, "property_id", "") == "P214" for s in item.statements
    )
