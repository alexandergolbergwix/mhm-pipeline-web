"""Wikidata Studio AI verdict cache invalidation."""

from __future__ import annotations

from app.pipeline.wikidata_verdict_cache import (
    WIKIDATA_VERDICT_SCHEMA,
    attach_local_reference_targets,
    sanitise_stale_wikidata_verdict,
    wikidata_verdict_input_fingerprint,
    wikidata_verdict_stable_input_fingerprint,
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


def test_query_summary_ignores_presentation_value_label_changes() -> None:
    """Gloss-only enrich must not bust cache keys (Rule W-175 / W-150)."""
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
    assert wikidata_verdict_input_fingerprint(item) == before


def test_fixture_statements_keep_value_label() -> None:
    from app.pipeline.wikidata_verdict_cache import fixture_statements

    rows = fixture_statements({
        "statements": [{
            "property_id": "P195",
            "value": "Q24568958",
            "value_label": "University of Leeds Libraries",
        }],
    })
    assert rows[0]["value_label"] == "University of Leeds Libraries"


def test_sanitise_sticky_full_survives_schema_bump() -> None:
    from app.pipeline.wikidata_verdict_cache import (
        sanitise_stale_wikidata_verdict,
        wikidata_claims_fingerprint,
        wikidata_verdict_input_fingerprint,
    )

    item = {
        "local_id": "SYNTH-STICKY-1",
        "entity_type": "manuscript",
        "labels": {"en": "Test"},
        "statements": [
            {"property_id": "P31", "value": "Q87167", "value_type": "item"},
        ],
    }
    claims = wikidata_claims_fingerprint(item, "gemini-3.5-flash")
    stored = {
        "overall": "full",
        "reasoning": "ok under prior schema",
        "model": "gemini-3.5-flash",
        "evaluator": "wikidata_item",
        "cache_key": "stale-schema-key",
        "cache_key_version": "records_marc_v6",
        "claims_fingerprint": claims,
    }
    # Full key mismatches (schema salt), but sticky-full must keep the pill.
    assert wikidata_verdict_input_fingerprint(item, "gemini-3.5-flash") != "stale-schema-key"
    kept = sanitise_stale_wikidata_verdict(item, stored)
    assert kept is not None
    assert kept["overall"] == "full"
    assert kept["claims_fingerprint"] == claims


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


def test_verdict_survives_subset_verify_evidence_drift() -> None:
    item = _verify_scope_item()
    stored = {
        "overall": "pass",
        "model": "moonshotai/Kimi-K2.5",
        "cache_key": wikidata_verdict_input_fingerprint(
            item, "moonshotai/Kimi-K2.5",
        ),
        "stable_cache_key": wikidata_verdict_stable_input_fingerprint(
            item, "moonshotai/Kimi-K2.5",
        ),
        "cache_key_version": "records_marc_v6",
        "evaluator": "wikidata_item",
    }
    read_item = {**item}
    read_item["verify_evidence"] = {"different_scope": True}
    read_item["local_reference_targets"] = {"person::other": {"labels": {}}}
    read_item["_marc_context"] = {"title": "read-time enrichment"}

    kept = sanitise_stale_wikidata_verdict(
        read_item, stored, marc_context=read_item["_marc_context"],
    )

    assert kept is not None
    assert kept["overall"] == "pass"


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


class TestJudgeProjectionIsNotTheFingerprintProjection:
    """Rule W-156 — what the judge reads and what keys the verdict differ."""

    @staticmethod
    def _item() -> dict:
        from app.pipeline.wikidata_verify_evidence import build_verify_evidence_pack

        item = {
            "local_id": "QDraft_MS_1",
            "entity_type": "manuscript",
            "labels": {"en": "Cambridge University Library, F 18702"},
            "statements": [{"property_id": "P31", "value": "Q87167"}],
            "record_ids": ["990001402000205171"],
            "_wikidata_existence": {"status": "absent", "candidates": []},
            "_llm_proposals": {"status": "ok", "proposals": [{"span": "x"}]},
        }
        item["verify_evidence"] = build_verify_evidence_pack(item, [])
        return item

    def test_the_fixture_projection_keeps_the_duplicate_check(self) -> None:
        """The bug: the judge was asked about a channel it was never shown."""
        from app.pipeline.wikidata_verdict_cache import judge_evidence_projection

        pack = judge_evidence_projection(self._item())
        assert pack["wikidata_existing"]["duplicate_check"]["status"] == "absent"

    def test_the_fixture_projection_keeps_the_llm_proposals(self) -> None:
        from app.pipeline.wikidata_verdict_cache import judge_evidence_projection

        assert judge_evidence_projection(self._item())["llm_proposals"]["status"] == "ok"

    def test_the_fixture_projection_still_drops_marc(self) -> None:
        """MARC travels in marc_extracted.json, not twice."""
        from app.pipeline.wikidata_verdict_cache import judge_evidence_projection

        assert "marc" not in judge_evidence_projection(self._item())

    def test_the_fingerprint_projection_still_drops_both(self) -> None:
        """Rule W-136 must not regress: these channels may not key a verdict."""
        from app.pipeline.wikidata_verdict_cache import fingerprint_verify_evidence

        pack = fingerprint_verify_evidence(self._item())
        assert "llm_proposals" not in pack
        assert "duplicate_check" not in pack["wikidata_existing"]

    def test_the_written_fixture_carries_the_duplicate_check(self, tmp_path) -> None:
        import json

        from app.pipeline.wikidata_verify_fixture import write_wikidata_verify_fixture

        write_wikidata_verify_fixture(
            dest_dir=tmp_path, marc_records=[], items=[self._item()],
        )
        written = json.loads((tmp_path / "wikidata_items.json").read_text())
        check = written[0]["verify_evidence"]["wikidata_existing"]["duplicate_check"]
        assert check["status"] == "absent"


class TestDuplicateRejudge:
    """Rule W-157 — a verdict judged without a duplicate answer is re-judged once."""

    @staticmethod
    def _item(status: str) -> dict:
        return {"local_id": "QDraft_MS_1", "_duplicate_status": status}

    def test_unknown_to_conclusive_triggers_a_rejudge(self) -> None:
        from app.pipeline.wikidata_verdict_cache import (
            cached_verdict_needs_duplicate_rejudge,
        )

        cached = {"verdict": {"overall": "partial", "duplicate_class": "unknown"}}
        assert cached_verdict_needs_duplicate_rejudge(cached, self._item("absent"))

    def test_a_verdict_with_no_class_reads_as_unknown(self) -> None:
        """Every verdict written before Rule W-157 was judged with the probe hidden."""
        from app.pipeline.wikidata_verdict_cache import (
            cached_verdict_needs_duplicate_rejudge,
        )

        cached = {"verdict": {"overall": "partial"}}
        assert cached_verdict_needs_duplicate_rejudge(cached, self._item("absent"))

    def test_a_conclusive_verdict_stays_a_cache_hit(self) -> None:
        from app.pipeline.wikidata_verdict_cache import (
            cached_verdict_needs_duplicate_rejudge,
        )

        cached = {
            "verdict": {"overall": "full", "duplicate_class": "probed-conclusive"},
        }
        assert not cached_verdict_needs_duplicate_rejudge(cached, self._item("absent"))

    def test_a_still_unknown_probe_does_not_loop_forever(self) -> None:
        from app.pipeline.wikidata_verdict_cache import (
            cached_verdict_needs_duplicate_rejudge,
        )

        cached = {"verdict": {"overall": "partial", "duplicate_class": "unknown"}}
        assert not cached_verdict_needs_duplicate_rejudge(cached, self._item("skipped"))
        assert not cached_verdict_needs_duplicate_rejudge(cached, self._item("not_run"))

    def test_the_annotator_never_drops_a_verdict(self) -> None:
        from app.pipeline.wikidata_verdict_cache import annotate_duplicate_rejudge

        verdict = {"overall": "partial", "reasoning": "…", "duplicate_class": "unknown"}
        out = annotate_duplicate_rejudge(verdict, self._item("candidates_found"))
        assert out["overall"] == "partial"
        assert out["reasoning"] == "…"
        assert out["needs_rejudge"] is True
        assert out["rejudge_reason"] == "duplicate_check_resolved"

    def test_the_annotator_leaves_a_fresh_verdict_alone(self) -> None:
        from app.pipeline.wikidata_verdict_cache import annotate_duplicate_rejudge

        verdict = {"overall": "full", "duplicate_class": "probed-conclusive"}
        assert annotate_duplicate_rejudge(verdict, self._item("absent")) == verdict

    def test_a_slim_persist_item_still_answers_its_duplicate_class(self) -> None:
        """`fingerprint_verify_evidence` strips the probe, so persist keys off this."""
        from app.pipeline.wikidata_duplicate_probe import duplicate_class_for_item
        from app.pipeline.wikidata_verify_evidence import build_verify_evidence_pack
        from app.pipeline.wikidata_verify_fixture import slim_item_for_verdict_persist

        item = {
            "local_id": "QDraft_MS_1",
            "entity_type": "manuscript",
            "statements": [],
            "_wikidata_existence": {"status": "candidates_found", "candidates": []},
        }
        item["verify_evidence"] = build_verify_evidence_pack(item, [])
        slim = slim_item_for_verdict_persist(item)
        assert "duplicate_check" not in slim["verify_evidence"]["wikidata_existing"]
        assert duplicate_class_for_item(slim) == "probed-conclusive"
