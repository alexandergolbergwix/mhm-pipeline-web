"""Live-native hygiene: identity clear + ``__LOCAL:`` rewrite (Rule W-194)."""

from __future__ import annotations

from app.pipeline.wikidata_live_native_hygiene import (
    existing_qid_of,
    sanitize_studio_items_for_live,
)
from converter.wikidata.property_mapping import (
    known_work_qid_for_title,
    work_item_existing_qid_for_title,
)
from scripts.judge_test_wikidata_live_ready import DEFAULT_TIER_MODEL


def test_savoy_heading_clears_live_qid_and_identity_pids() -> None:
    person = {
        "local_id": "QDraft_Person_71",
        "entity_type": "person",
        "existing_qid": "Q209579",
        "labels": {"he": "ויטוריו אמדיאו"},
        "statements": [{"property_id": "P214", "value": "123"}],
    }
    live = {
        "Q209579": {
            "labels": {"en": {"value": "Victor Amadeus II of Savoy"}},
        },
    }
    stats = sanitize_studio_items_for_live([person], live_entities=live)
    assert stats["cleared_person_qids"] == 1
    assert not existing_qid_of(person)
    assert person["statements"] == []


def test_tikkun_chatzot_work_item_must_not_update_ritual_q() -> None:
    assert known_work_qid_for_title("תיקון חצות") == "Q2740944"
    assert work_item_existing_qid_for_title("תיקון חצות") is None
    work = {
        "local_id": "QDraft_Work_85",
        "entity_type": "work",
        "existing_qid": "Q2740944",
        "labels": {"he": "תקון חצות"},
        "statements": [],
    }
    stats = sanitize_studio_items_for_live([work], live_entities={})
    assert stats["cleared_work_qids"] == 1
    assert not existing_qid_of(work)


def test_local_rewrites_to_remaining_live_q() -> None:
    work = {
        "local_id": "QDraft_Work_1",
        "entity_type": "work",
        "existing_qid": "Q123",
        "statements": [],
    }
    manuscript = {
        "local_id": "QDraft_MS_1",
        "entity_type": "manuscript",
        "statements": [
            {"property_id": "P1574", "value": "__LOCAL:QDraft_Work_1"},
        ],
    }
    stats = sanitize_studio_items_for_live([work, manuscript])
    assert stats["local_rewritten"] == 1
    assert manuscript["statements"][0]["value"] == "Q123"


def test_dangling_exemplar_degrades_to_unknown_text() -> None:
    manuscript = {
        "local_id": "QDraft_MS_2",
        "entity_type": "manuscript",
        "statements": [
            {"property_id": "P1574", "value": "__LOCAL:missing"},
        ],
    }
    stats = sanitize_studio_items_for_live([manuscript])
    assert stats["local_degraded"] == 1
    assert manuscript["statements"][0]["value"] == "Q234460"


def test_in_batch_create_local_is_kept_for_w192() -> None:
    work = {
        "local_id": "QDraft_Work_3",
        "entity_type": "work",
        "existing_qid": None,
        "statements": [],
    }
    manuscript = {
        "local_id": "QDraft_MS_3",
        "entity_type": "manuscript",
        "statements": [
            {"property_id": "P1574", "value": "__LOCAL:QDraft_Work_3"},
        ],
    }
    stats = sanitize_studio_items_for_live([work, manuscript])
    assert stats["local_rewritten"] == 0
    assert manuscript["statements"][0]["value"] == "__LOCAL:QDraft_Work_3"


def test_hayim_shor_heading_clears_avraham_hayim_shor() -> None:
    person = {
        "local_id": "QDraft_Person_199",
        "entity_type": "person",
        "existing_qid": "Q6580025",
        "labels": {"he": "חיים בן נפתלי הירש שור"},
        "statements": [{"property_id": "P8189", "value": "987654321"}],
    }
    live = {
        "Q6580025": {
            "labels": {
                "en": {"value": "Avraham Hayim Shor"},
                "he": {"value": "אברהם חיים שור"},
            },
        },
    }
    stats = sanitize_studio_items_for_live([person], live_entities=live)
    assert stats["cleared_person_qids"] == 1
    assert not existing_qid_of(person)


def test_live_ready_judge_defaults_to_deepseek_v4_flash() -> None:
    assert DEFAULT_TIER_MODEL == "deepseek-ai/DeepSeek-V4-Flash"


def test_string_title_coerces_to_monolingualtext() -> None:
    work = {
        "local_id": "QDraft_Work_title",
        "entity_type": "work",
        "statements": [
            {"property_id": "P1476", "value": "אב הרחמים", "value_type": "string"},
        ],
    }
    stats = sanitize_studio_items_for_live([work])
    assert stats["coerced_monolingualtext"] == 1
    assert work["statements"][0]["value_type"] == "monolingualtext"


def test_implausible_width_is_omitted() -> None:
    manuscript = {
        "local_id": "QDraft_MS_wide",
        "entity_type": "manuscript",
        "statements": [
            {"property_id": "P2048", "value": 290, "value_type": "quantity"},
            {"property_id": "P2049", "value": 5180, "value_type": "quantity"},
        ],
    }
    stats = sanitize_studio_items_for_live([manuscript])
    assert stats["omitted_implausible_dimensions"] == 1
    pids = {row["property_id"] for row in manuscript["statements"]}
    assert pids == {"P2048"}
