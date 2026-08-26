"""Skip CREATE of catalog works that already exist; keep link_qid (Rule W-196)."""

from __future__ import annotations

from app.pipeline.wikidata_duplicate_probe import probe_work_title_allowlisted
from app.pipeline.wikidata_duplicate_confirm import SAME_ITEM
from app.pipeline.wikidata_upload import (
    ForeignAccept,
    PreparedItem,
    _apply_existence_and_ownership,
    remember_created_qid,
)


def _p31_entity(qid: str, class_qid: str) -> dict:
    return {
        "id": qid,
        "claims": {
            "P31": [{
                "mainsnak": {
                    "datavalue": {"value": {"id": class_qid}},
                },
            }],
        },
    }


def test_probe_unique_prayer_qid() -> None:
    def fetch(url: str, timeout: float | None = None) -> dict:
        if "srsearch" in url or "list=search" in url:
            return {"query": {"search": [{"title": "Q2873224"}]}}
        return {"entities": {"Q2873224": _p31_entity("Q2873224", "Q1344")}}

    assert probe_work_title_allowlisted("אב הרחמים", fetch=fetch) == "Q2873224"


def test_probe_rejects_crossword_hit() -> None:
    def fetch(url: str, timeout: float | None = None) -> dict:
        if "srsearch" in url or "list=search" in url:
            return {"query": {"search": [{"title": "Q999"}]}}
        return {"entities": {"Q999": _p31_entity("Q999", "Q14456732")}}

    assert probe_work_title_allowlisted("אב הרחמים", fetch=fetch) is None


def test_probe_two_allowlisted_hits_are_ambiguous() -> None:
    def fetch(url: str, timeout: float | None = None) -> dict:
        if "srsearch" in url or "list=search" in url:
            return {"query": {"search": [{"title": "Q1"}, {"title": "Q2"}]}}
        return {
            "entities": {
                "Q1": _p31_entity("Q1", "Q1344"),
                "Q2": _p31_entity("Q2", "Q47461344"),
            },
        }

    assert probe_work_title_allowlisted("הגדה", fetch=fetch) is None


def test_tikkun_skip_keeps_link_qid(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_labels",
        lambda qids, *, is_test=False: {},
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_p31",
        lambda qids, *, is_test=False: {},
    )
    item = type("W", (), {
        "entity_type": "work",
        "existing_qid": "Q2740944",
        "labels": {"he": "תיקון חצות"},
        "statements": [],
    })()
    prepared = PreparedItem(
        item=item,
        local_id="QDraft_Work_85",
        label="תיקון חצות",
        entity_type="work",
        existing_qid="Q2740944",
        method="prebuilt",
        blocked=False,
        block_status="",
        block_message="",
    )
    out = _apply_existence_and_ownership(
        prepared,
        accept=ForeignAccept(),
        ownership_checker=None,
        is_test=False,
    )
    assert out.block_status == "skipped"
    assert out.link_qid == "Q2740944"
    assert out.existing_qid is None
    assert "W-196" in out.block_message


def test_same_item_work_skip_keeps_link_qid(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_labels",
        lambda qids, *, is_test=False: {"Q140051042": {"he": "בחינת עולם"}},
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_p31",
        lambda qids, *, is_test=False: {"Q140051042": ["Q47461344"]},
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_duplicate_confirm.confirm_uncertain_duplicate",
        lambda **kwargs: SAME_ITEM,
    )
    item = type("W", (), {
        "entity_type": "work",
        "existing_qid": "Q140051042",
        "labels": {"he": "בחינת עולם"},
        "statements": [],
    })()
    prepared = PreparedItem(
        item=item,
        local_id="QDraft_Work_exam",
        label="בחינת עולם",
        entity_type="work",
        existing_qid="Q140051042",
        method="label+author",
        blocked=False,
        block_status="",
        block_message="",
    )
    out = _apply_existence_and_ownership(
        prepared,
        accept=ForeignAccept(),
        ownership_checker=None,
        is_test=False,
    )
    assert out.block_status == "skipped"
    assert out.link_qid == "Q140051042"
    assert out.existing_qid is None
    assert "W-196" in out.block_message


def test_remember_skipped_work_qid_not_person() -> None:
    session: dict[str, str] = {}
    remember_created_qid(
        session, "QDraft_Work", "Q2873224", "skipped", entity_type="work",
    )
    remember_created_qid(
        session, "QDraft_Person", "Q5", "skipped", entity_type="person",
    )
    assert session == {"QDraft_Work": "Q2873224"}
