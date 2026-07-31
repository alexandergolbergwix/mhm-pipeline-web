"""Wikidata Studio AI verdict cache invalidation."""

from __future__ import annotations

from app.pipeline.wikidata_verdict_cache import (
    WIKIDATA_VERDICT_SCHEMA,
    attach_local_reference_targets,
    sanitise_stale_wikidata_verdict,
    wikidata_verdict_input_fingerprint,
    wikidata_verdict_query_summary,
)


def test_query_summary_changes_when_labels_change() -> None:
    item = {
        "_local_id": "manuscript_1",
        "entity_type": "manuscript",
        "record_ids": ["990000403370205171"],
        "labels": {"en": "MS 1"},
        "descriptions": {"en": "Hebrew manuscript."},
        "statements": [],
        "validation_issues": [],
        "_marc_context": {"title": "MS 1"},
    }
    a = wikidata_verdict_input_fingerprint(item, "gemini-3.5-flash")
    item["labels"] = {"en": "MS 1 revised"}
    b = wikidata_verdict_input_fingerprint(item, "gemini-3.5-flash")
    assert a != b


def test_query_summary_changes_when_marc_context_changes() -> None:
    item = {
        "_local_id": "manuscript_1",
        "entity_type": "manuscript",
        "record_ids": ["990000403370205171"],
        "labels": {"en": "MS 1"},
        "descriptions": {"en": "Hebrew manuscript."},
        "statements": [],
        "validation_issues": [],
        "_marc_context": {"authors": "Author A"},
    }
    a = wikidata_verdict_input_fingerprint(item, "gemini-3.5-flash")
    item["_marc_context"] = {"authors": "Author A", "notes": "Colophon"}
    b = wikidata_verdict_input_fingerprint(item, "gemini-3.5-flash")
    assert a != b


def test_query_summary_includes_schema_salt() -> None:
    summary = wikidata_verdict_query_summary(
        {"_local_id": "x", "labels": {}, "descriptions": {}},
        "gemini-3.5-flash",
    )
    assert summary["wikidata_verdict_schema"] == WIKIDATA_VERDICT_SCHEMA


def test_sanitise_stale_wikidata_verdict_hides_mismatched_key() -> None:
    item = {
        "_local_id": "manuscript_1",
        "labels": {"en": "MS 1"},
        "descriptions": {"en": "Hebrew manuscript."},
        "statements": [],
        "_marc_context": {},
    }
    stored = {
        "overall": "full",
        "cache_key": "stale-eval-agent-prompt-hash",
        "model": "gemini-3.5-flash",
        "evaluator": "wikidata_item",
    }
    assert sanitise_stale_wikidata_verdict(item, stored) is None


def test_sanitise_stale_wikidata_verdict_keeps_matching_key() -> None:
    item = {
        "_local_id": "manuscript_1",
        "labels": {"en": "MS 1"},
        "descriptions": {"en": "Hebrew manuscript."},
        "statements": [],
        "_marc_context": {},
    }
    fp = wikidata_verdict_input_fingerprint(item, "gemini-3.5-flash")
    stored = {
        "overall": "full",
        "cache_key": fp,
        "model": "gemini-3.5-flash",
        "evaluator": "wikidata_item",
    }
    kept = sanitise_stale_wikidata_verdict(item, stored)
    assert kept is not None
    assert kept["cache_key"] == fp



def test_record_ids_recover_from_nli_reference_when_legacy_item_lacks_records() -> None:
    item = {
        "statements": [{
            "references": [
                {"property": "P248", "value": "Q118384267"},
                {"property": "P3959", "value": "990000000000000123"},
            ],
        }],
    }
    assert wikidata_verdict_query_summary(item)["record_ids"] == ["990000000000000123"]


def test_query_summary_changes_when_verifier_evidence_changes() -> None:
    item = {
        "_local_id": "person_1",
        "entity_type": "person",
        "labels": {"en": "Jane Doe"},
        "descriptions": {"en": "Person"},
        "statements": [],
        "authority_evidence": [{"source": "NLI", "birth_year": 1950}],
        "local_reference_targets": {},
    }
    a = wikidata_verdict_input_fingerprint(item, "gemini-3.5-flash")
    item["authority_evidence"] = [{"source": "NLI", "birth_year": 1951}]
    b = wikidata_verdict_input_fingerprint(item, "gemini-3.5-flash")
    assert a != b


def test_query_summary_changes_when_work_candidate_evidence_changes() -> None:
    item = {
        "_local_id": "work:test",
        "entity_type": "work",
        "statements": [],
        "work_candidate_evidence": {"source_text": "Work by Author A"},
    }
    before = wikidata_verdict_input_fingerprint(item)
    item["work_candidate_evidence"] = {"source_text": "Work by Author B"}
    assert wikidata_verdict_input_fingerprint(item) != before


def test_query_summary_changes_when_statement_labels_change() -> None:
    item = {
        "_local_id": "manuscript:1",
        "statements": [{
            "property": "P921",
            "property_label": "main subject",
            "value": "Q107427",
            "value_label": "Halakha",
        }],
    }
    before = wikidata_verdict_input_fingerprint(item)
    item["statements"][0]["value_label"] = "incorrect label"
    assert wikidata_verdict_input_fingerprint(item) != before


def test_attach_local_reference_targets_uses_full_item_set() -> None:
    person = {
        "local_id": "person:1",
        "entity_type": "person",
        "labels": {"en": "Jane Doe"},
        "authority_evidence": [{"source": "NLI", "role": "author"}],
    }
    manuscript = {
        "local_id": "manuscript:1",
        "entity_type": "manuscript",
        "statements": [{"property": "P50", "value": "__LOCAL:person:1"}],
    }

    attach_local_reference_targets([manuscript, person])

    assert manuscript["local_reference_targets"]["person:1"] == {
        "entity_type": "person",
        "labels": {"en": "Jane Doe"},
        "existing_qid": None,
        "authority_evidence": [{"source": "NLI", "role": "author"}],
        "semantic_type": "",
        "descriptions": {},
        "aliases": {},
        "records": [],
    }


def _verify_scope_item() -> dict:
    return {
        "_local_id": "person::x",
        "local_id": "person::x",
        "entity_type": "person",
        "records": ["990000403370205171"],
        "labels": {"en": "Abraham Firkowitsch"},
        "descriptions": {"en": "Karaite collector."},
        "aliases": {},
        "statements": [
            {
                "property": "P214",
                "property_label": "VIAF ID",
                "value_type": "string",
                "value": "12345",
                "references": [{"property": "P3959", "value": "990000403370205171"}],
            },
        ],
        "validation_issues": [],
        "authority_evidence": [{"kind": "viaf", "id": "12345"}],
        "verify_evidence": {
            "marc": {"title": "MS 1"},
            "viaf": [{"id": "12345"}],
        },
        "_marc_context": {"title": "MS 1"},
    }


def test_slimmed_persist_item_reproduces_the_full_item_fingerprint() -> None:
    """Rule W-136 — the worker slims items before persisting the verdict."""
    from app.pipeline.wikidata_verify_fixture import slim_item_for_verdict_persist

    item = _verify_scope_item()
    full = wikidata_verdict_input_fingerprint(item, "moonshotai/Kimi-K2.5")
    slim = wikidata_verdict_input_fingerprint(
        slim_item_for_verdict_persist(item), "moonshotai/Kimi-K2.5",
    )
    assert full == slim


def test_verdict_survives_when_evidence_pack_is_absent() -> None:
    """A verdict keyed without derived evidence stays visible."""
    item = _verify_scope_item()
    evidence_free = {**item, "verify_evidence": {}, "local_reference_targets": {}}
    stored = {
        "overall": "pass",
        "model": "moonshotai/Kimi-K2.5",
        "cache_key": wikidata_verdict_input_fingerprint(
            evidence_free, "moonshotai/Kimi-K2.5",
        ),
        "cache_key_version": "records_marc_v6",
        "evaluator": "wikidata_item",
    }
    kept = sanitise_stale_wikidata_verdict(
        item, stored, marc_context=item["_marc_context"],
    )
    assert kept is not None
    assert kept["overall"] == "pass"
    assert kept["cache_key"] == wikidata_verdict_input_fingerprint(
        item, "moonshotai/Kimi-K2.5",
    )


class TestAdvisoryEvidenceNeverKeysAVerdict:
    """Rule W-140 — `llm_proposals` must not participate in the verdict key.

    The rubric forbids a proposal from moving any verdict axis, so letting it key
    the verdict made the AI-verdict column go empty the moment the build mined a
    record: the read path hashed `status: ok` where verify had hashed `not_run`.
    """

    @staticmethod
    def _item(proposals: dict) -> dict:
        return {
            "local_id": "QDraft_MS_1",
            "entity_type": "manuscript",
            "labels": {"en": "Jerusalem, NLI, Ms. Heb. 1"},
            "statements": [{"property_id": "P31", "value": "Q87167"}],
            "record_ids": ["990001"],
            "verify_evidence": {
                "marc": {"shelfmark": "Ms. Heb. 1"},
                "viaf": {},
                "llm_proposals": proposals,
            },
        }

    def test_fingerprint_ignores_the_proposal_channel(self) -> None:
        not_run = wikidata_verdict_input_fingerprint(
            self._item({"status": "not_run", "proposals": []}),
        )
        mined = wikidata_verdict_input_fingerprint(
            self._item({
                "status": "ok",
                "proposals": [{"property_id": "P186", "value": "Q226697"}],
            }),
        )
        assert not_run == mined

    def test_a_real_evidence_change_still_changes_the_fingerprint(self) -> None:
        """The exclusion must be surgical, not a blanket bypass."""
        base = self._item({"status": "not_run", "proposals": []})
        changed = self._item({"status": "not_run", "proposals": []})
        changed["verify_evidence"]["viaf"] = {"authority_rows": [{"identifier": "9"}]}
        assert wikidata_verdict_input_fingerprint(base) != (
            wikidata_verdict_input_fingerprint(changed)
        )

    def test_a_verdict_keyed_the_old_way_is_recovered_and_rewritten(self) -> None:
        item = self._item({"status": "not_run", "proposals": []})
        legacy_key = wikidata_verdict_input_fingerprint(
            item, evidence_drop=("marc",),
        )
        stored = {
            "overall": "full",
            "reasoning": "looks right",
            "cache_key": legacy_key,
            "cache_key_version": "records_marc_v6",
            "model": "gemini-3.5-flash",
            "evaluator": "wikidata_item",
        }

        recovered = sanitise_stale_wikidata_verdict(item, stored)

        assert recovered is not None, "verdict written before the fix was lost"
        assert recovered["overall"] == "full"
        assert recovered["cache_key"] == wikidata_verdict_input_fingerprint(item)


class TestPathDependentEvidenceNeverKeysAVerdict:
    """Rule W-136 — only state the READ path can reproduce may key a verdict.

    The duplicate probe (Rule W-139) runs on the verify path only, so verify
    hashed `duplicate_check: absent` while the review table hashed `not_run`.
    Every verdict therefore read as stale and the AI-verdict column was empty.
    """

    @staticmethod
    def _item(existence: dict | None) -> dict:
        from app.pipeline.wikidata_verify_evidence import build_verify_evidence_pack

        item = {
            "local_id": "QDraft_MS_1",
            "entity_type": "manuscript",
            "labels": {"en": "Cambridge University Library, F 18702"},
            "statements": [{"property_id": "P31", "value": "Q87167"}],
            "record_ids": ["990001402000205171"],
        }
        if existence is not None:
            item["_wikidata_existence"] = existence
        item["verify_evidence"] = build_verify_evidence_pack(item, [])
        return item

    def test_probed_and_unprobed_items_hash_identically(self) -> None:
        verify = self._item({"status": "absent", "candidates": []})
        read = self._item(None)
        assert verify["verify_evidence"]["wikidata_existing"]["duplicate_check"][
            "status"
        ] == "absent"
        assert read["verify_evidence"]["wikidata_existing"]["duplicate_check"][
            "status"
        ] == "not_run"
        assert wikidata_verdict_input_fingerprint(
            verify,
        ) == wikidata_verdict_input_fingerprint(read)

    def test_a_found_duplicate_also_hashes_identically(self) -> None:
        found = self._item(
            {"status": "candidates_found", "candidates": [{"qid": "Q66439"}]},
        )
        assert wikidata_verdict_input_fingerprint(
            found,
        ) == wikidata_verdict_input_fingerprint(self._item(None))

    def test_the_stable_part_of_the_channel_still_keys_the_verdict(self) -> None:
        """Only `duplicate_check` is excluded — not the whole channel."""
        read = self._item(None)
        linked = self._item(None)
        linked["verify_evidence"]["wikidata_existing"]["existing_qid"] = "Q42"
        assert wikidata_verdict_input_fingerprint(
            read,
        ) != wikidata_verdict_input_fingerprint(linked)

    def test_a_claim_change_is_still_detected(self) -> None:
        read = self._item(None)
        changed = self._item(None)
        changed["statements"] = [{"property_id": "P31", "value": "Q5"}]
        assert wikidata_verdict_input_fingerprint(
            read,
        ) != wikidata_verdict_input_fingerprint(changed)
