"""Rule W-139 — verify checks Wikidata for an existing item before CREATE."""

from __future__ import annotations

import asyncio
import json
import urllib.error
from unittest.mock import patch

from app.pipeline.wikidata_duplicate_probe import (
    STATUS_ABSENT,
    STATUS_CANDIDATES,
    STATUS_HAS_QID,
    STATUS_SKIPPED,
    STATUS_UNAVAILABLE,
    attach_duplicate_evidence,
    identity_probes,
    probe_item,
    search_by_statement,
)
from app.pipeline.wikidata_verify_evidence import build_verify_evidence_pack


def _hit(qid: str, *, pid: str = "P214", value: str = "61512894"):
    """Fake API: a search hit, plus the claims lookup that attributes it."""
    def fetch(url: str, *, timeout: float) -> dict:
        if "list=search" in url:
            return {"query": {"search": [{"title": qid, "snippet": "Mordecai Bassani"}]}}
        assert "wbgetentities" in url
        return {
            "entities": {
                qid: {
                    "labels": {"en": {"value": "Mordecai Bassani"}},
                    "claims": {
                        pid: [{"mainsnak": {"datavalue": {"value": value}}}],
                    },
                },
            },
        }

    return fetch


def _miss(url: str, *, timeout: float) -> dict:
    return {"query": {"search": []}}


def _batch_hit(url: str, *, timeout: float) -> dict:
    return _hit("Q118924043")(url, timeout=timeout)


def _boom(url: str, *, timeout: float) -> dict:
    raise urllib.error.URLError("429 too many requests")


def _person(**extra) -> dict:
    item = {
        "local_id": "QDraft_Person_45",
        "entity_type": "person",
        "statements": [{"property_id": "P214", "value": "61512894"}],
    }
    item.update(extra)
    return item


class TestIdentityProbes:
    def test_manuscript_probes_its_catalog_id(self) -> None:
        item = {
            "entity_type": "manuscript",
            "statements": [{"property_id": "P3959", "value": "990001"}],
        }
        assert identity_probes(item) == [
            {"kind": "identifier", "pid": "P3959", "value": "990001"},
        ]

    def test_person_probes_every_authority_identifier(self) -> None:
        item = {
            "entity_type": "person",
            "statements": [
                {"property_id": "P214", "value": "1"},
                {"property_id": "P8189", "value": "2"},
                {"property_id": "P31", "value": "Q5"},
            ],
        }
        assert [p["pid"] for p in identity_probes(item)] == ["P214", "P8189"]

    def test_item_without_identifiers_has_nothing_to_probe(self) -> None:
        assert identity_probes({"entity_type": "work", "statements": []}) == []


class TestSearchByStatement:
    def test_existing_item_is_reported_with_its_qid(self) -> None:
        result = search_by_statement("P214", "61512894", fetch=_hit("Q118924043"))
        assert result["status"] == STATUS_CANDIDATES
        assert result["candidates"][0]["qid"] == "Q118924043"
        assert result["candidates"][0]["matched_on"] == "P214=61512894"

    def test_no_hit_is_absent(self) -> None:
        assert search_by_statement("P3959", "990001", fetch=_miss)["status"] == STATUS_ABSENT

    def test_network_failure_is_unavailable_never_absent(self) -> None:
        """A failed lookup must not read as permission to create."""
        result = search_by_statement("P214", "1", fetch=_boom)
        assert result["status"] == STATUS_UNAVAILABLE
        assert result["candidates"] == []


class TestProbeItem:
    def test_duplicate_is_surfaced(self) -> None:
        result = asyncio.run(probe_item(None, _person(), fetch=_hit("Q118924043")))
        assert result["status"] == STATUS_CANDIDATES
        assert result["candidates"][0]["qid"] == "Q118924043"

    def test_item_with_existing_qid_is_an_update_not_a_create(self) -> None:
        result = asyncio.run(probe_item(None, _person(existing_qid="Q42"), fetch=_boom))
        assert result["status"] == STATUS_HAS_QID

    def test_item_without_identifiers_says_absence_is_not_established(self) -> None:
        result = asyncio.run(
            probe_item(None, {"entity_type": "work", "statements": []}, fetch=_miss),
        )
        assert result["status"] == STATUS_SKIPPED
        assert "NOT established" in result["note"]


class TestAttachDuplicateEvidence:
    def test_stats_and_stamping(self) -> None:
        items = [_person(), _person(local_id="p2", existing_qid="Q42")]
        stats = asyncio.run(attach_duplicate_evidence(None, items, fetch=_batch_hit))
        assert stats["duplicates"] == 1
        assert items[0]["_wikidata_existence"]["status"] == STATUS_CANDIDATES
        assert items[1]["_wikidata_existence"]["status"] == STATUS_HAS_QID

    def test_budget_exhaustion_is_reported_not_silent(self) -> None:
        items = [_person(local_id=f"p{i}") for i in range(3)]
        stats = asyncio.run(
            attach_duplicate_evidence(None, items, fetch=_miss, budget=1),
        )
        assert stats["probed"] == 1
        assert stats["skipped"] == 2
        assert items[2]["_wikidata_existence"]["status"] == STATUS_SKIPPED
        assert "budget" in items[2]["_wikidata_existence"]["note"]

    def test_evidence_pack_exposes_the_duplicate_check(self) -> None:
        items = [_person()]
        asyncio.run(attach_duplicate_evidence(None, items, fetch=_batch_hit))
        pack = build_verify_evidence_pack(items[0], [])
        check = pack["wikidata_existing"]["duplicate_check"]
        assert check["status"] == STATUS_CANDIDATES
        assert check["candidates"][0]["qid"] == "Q118924043"

    def test_unprobed_item_reports_not_run(self) -> None:
        pack = build_verify_evidence_pack(_person(), [])
        assert pack["wikidata_existing"]["duplicate_check"]["status"] == "not_run"


class TestBatchedProbeCaching:
    """Rule W-140 — the batched path caches per identifier and never holds a txn."""

    @staticmethod
    def _factory(store: dict, order: list, open_count: dict):
        import contextlib

        @contextlib.asynccontextmanager
        async def factory():
            open_count["now"] += 1
            open_count["max"] = max(open_count["max"], open_count["now"])
            order.append("session")
            try:
                yield object()
            finally:
                open_count["now"] -= 1

        return factory

    def test_absent_answers_are_cached_so_a_rerun_makes_no_calls(self) -> None:
        store: dict = {}
        order: list[str] = []
        open_count = {"now": 0, "max": 0}
        factory = self._factory(store, order, open_count)
        http_calls: list[int] = []

        def fetch(url: str, *, timeout: float) -> dict:
            http_calls.append(1)
            if open_count["now"]:
                raise AssertionError("HTTP ran inside an open transaction")
            return {"query": {"search": []}}

        from app.pipeline.inference_cache import canonical_hash

        async def read_many(_db, *, kind, query_summaries):
            return {
                canonical_hash(s): store[canonical_hash(s)]
                for s in query_summaries
                if canonical_hash(s) in store
            }

        async def write_many(_db, *, kind, entries):
            for summary, result in entries:
                store[canonical_hash(summary)] = result

        async def run() -> None:
            with patch(
                "app.pipeline.inference_cache.read_many_from_inference_cache",
                new=read_many,
            ), patch(
                "app.pipeline.inference_cache.write_many_to_inference_cache",
                new=write_many,
            ):
                first = await attach_duplicate_evidence(
                    factory, [_person()], fetch=fetch,
                )
                assert first["cached"] == 0
                second = await attach_duplicate_evidence(
                    factory, [_person()], fetch=fetch,
                )
                assert second["cached"] == 1

        asyncio.run(run())
        assert len(http_calls) == 1, f"re-run hit the network: {len(http_calls)} calls"
        assert open_count["now"] == 0, "a session leaked"

    def test_cached_duplicate_is_still_reported(self) -> None:
        store: dict = {}
        order: list[str] = []
        open_count = {"now": 0, "max": 0}
        factory = self._factory(store, order, open_count)

        from app.pipeline.inference_cache import canonical_hash

        async def read_many(_db, *, kind, query_summaries):
            return {
                canonical_hash(s): {
                    "candidates": [
                        {"qid": "Q118924043", "matched_on": "P214=61512894"},
                    ],
                }
                for s in query_summaries
            }

        async def write_many(_db, **_kwargs):
            return None

        async def run() -> dict:
            items = [_person()]
            with patch(
                "app.pipeline.inference_cache.read_many_from_inference_cache",
                new=read_many,
            ), patch(
                "app.pipeline.inference_cache.write_many_to_inference_cache",
                new=write_many,
            ):
                stats = await attach_duplicate_evidence(
                    factory, items, fetch=_boom,
                )
            return {"stats": stats, "item": items[0]}

        out = asyncio.run(run())
        existence = out["item"]["_wikidata_existence"]
        assert existence["status"] == STATUS_CANDIDATES
        assert existence["candidates"][0]["qid"] == "Q118924043"
        assert out["stats"]["duplicates"] == 1

    def test_a_broken_cache_does_not_break_the_probe(self) -> None:
        import contextlib

        @contextlib.asynccontextmanager
        async def factory():
            raise RuntimeError("pool exhausted")
            yield  # pragma: no cover

        items = [_person()]
        stats = asyncio.run(
            attach_duplicate_evidence(factory, items, fetch=_batch_hit),
        )
        assert stats["probed"] == 1
        assert items[0]["_wikidata_existence"]["status"] == STATUS_CANDIDATES


def _stmt(pid: str, value: str) -> dict[str, str]:
    return {"property_id": pid, "value": value}


class TestHolderPlusShelfmarkKey:
    """Rule W-144: a second key for manuscripts no P3959 probe can reach."""

    def test_holder_and_shelfmark_make_a_composite_probe(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import composite_probes

        item = {
            "entity_type": "manuscript",
            "statements": [_stmt("P195", "Q1028334"), _stmt("P217", "F 18760")],
        }
        probes = composite_probes(item)
        assert [p["pid"] for p in probes] == ["P195+P217"]
        assert probes[0]["kind"] == "composite"

    def test_an_abstained_holder_yields_no_composite_probe(self) -> None:
        # Rule W-143 abstains on ambiguous institutions, so there is no P195 and
        # this key must not silently become a one-sided lookup.
        from app.pipeline.wikidata_duplicate_probe import composite_probes

        item = {"entity_type": "manuscript", "statements": [_stmt("P217", "F 18760")]}
        assert composite_probes(item) == []

    def test_two_shelfmarks_abstain_rather_than_pick_one(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import composite_probes

        item = {
            "entity_type": "manuscript",
            "statements": [
                _stmt("P195", "Q1028334"),
                _stmt("P217", "F 18760"),
                _stmt("P217", "Add. 1234"),
            ],
        }
        assert composite_probes(item) == []

    def test_the_conjunction_query_is_an_AND_not_an_OR(self) -> None:
        # `haswbstatement` joins with `|` as OR. Using it here would return every
        # manuscript at Cambridge, which reads as a duplicate for all of them.
        from app.pipeline.wikidata_duplicate_probe import _conjunction_query

        query = _conjunction_query([("P195", "Q1028334"), ("P217", "F 18760")])
        assert "|" not in query
        assert query == 'haswbstatement:P195=Q1028334 haswbstatement:P217="F 18760"'

    def test_a_composite_hit_is_reported_as_a_candidate(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import probe_composite

        def fetch(url: str, timeout: float | None = None) -> dict[str, object]:
            assert "haswbstatement:P195=Q1028334" in url or "P195%3DQ1028334" in url
            return {"query": {"search": [{"title": "Q999"}]}}

        hits = probe_composite("P195+P217", "Q1028334␟F 18760", fetch=fetch)
        assert [h["qid"] for h in hits] == ["Q999"]
        assert "AND" in hits[0]["matched_on"]


class TestWorkTitleProbe:
    """Rule W-145: works had no duplicate check at all — 0 identifiers."""

    def test_title_and_class_make_a_probe(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import title_probes

        item = {
            "entity_type": "work",
            "statements": [_stmt("P1476", "הגדה של פסח"), _stmt("P31", "Q47461344")],
        }
        probes = title_probes(item)
        assert probes[0]["pid"] == "title+P31"
        assert probes[0]["value"].startswith("הגדה של פסח")

    def test_a_manuscript_never_gets_a_title_probe(self) -> None:
        # Manuscripts have real identifiers; a title likeness must not weaken them.
        from app.pipeline.wikidata_duplicate_probe import title_probes

        item = {
            "entity_type": "manuscript",
            "statements": [_stmt("P1476", "כתב יד"), _stmt("P31", "Q87167")],
        }
        assert title_probes(item) == []

    def test_ambiguous_class_abstains(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import title_probes

        item = {
            "entity_type": "work",
            "statements": [
                _stmt("P1476", "x"), _stmt("P31", "Q47461344"), _stmt("P31", "Q571"),
            ],
        }
        assert title_probes(item) == []

    def test_a_title_candidate_demands_curator_confirmation(self) -> None:
        # A title is a likeness, not an identity. Nothing may auto-match on it.
        from app.pipeline.wikidata_duplicate_probe import probe_title

        def fetch(url: str, timeout: float | None = None) -> dict[str, object]:
            return {"query": {"search": [{"title": "Q623354"}]}}

        hits = probe_title("title+P31", "הגדה של פסח␟Q47461344", fetch=fetch)
        assert hits[0]["qid"] == "Q623354"
        assert hits[0]["requires_curator_confirmation"] == "true"
        assert "title~" in hits[0]["matched_on"]

    def test_a_work_is_no_longer_skipped_outright(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import decide_without_network

        item = {
            "entity_type": "work",
            "statements": [_stmt("P1476", "הגדה של פסח"), _stmt("P31", "Q47461344")],
        }
        assert decide_without_network(item) is None


class TestAbsentMeansEveryKeyAnswered:
    """Rule W-144: a partial probe is `skipped`, never `absent`."""

    def test_a_failed_composite_downgrades_absent_to_skipped(self) -> None:
        item = {
            "entity_type": "manuscript",
            "statements": [
                _stmt("P3959", "990001404380205171"),
                _stmt("P195", "Q1028334"),
                _stmt("P217", "F 18760"),
            ],
        }

        def fetch(url: str, timeout: float | None = None) -> dict[str, object]:
            if "P195" in url or "P195%3D" in url:
                raise urllib.error.URLError("boom")
            return {"query": {"search": []}}

        stats = asyncio.run(attach_duplicate_evidence(None, [item], fetch=fetch))
        existence = item["_wikidata_existence"]
        assert existence["status"] == STATUS_SKIPPED
        assert "NOT established" in existence["note"]
        assert stats["skipped"] == 1
        assert "_unanswered" not in existence

    def test_all_keys_answering_absent_stays_absent(self) -> None:
        item = {
            "entity_type": "manuscript",
            "statements": [
                _stmt("P3959", "990001404380205171"),
                _stmt("P195", "Q1028334"),
                _stmt("P217", "F 18760"),
            ],
        }

        def fetch(url: str, timeout: float | None = None) -> dict[str, object]:
            return {"query": {"search": []}}

        asyncio.run(attach_duplicate_evidence(None, [item], fetch=fetch))
        assert item["_wikidata_existence"]["status"] == STATUS_ABSENT


class TestCachedAnswerIsVisibleWithoutProbing:
    """Rule W-144: 207 cached answers were invisible in every export."""

    def test_a_cached_candidate_surfaces_with_no_network_call(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import (
            attach_cached_duplicate_evidence,
        )

        item = {
            "entity_type": "manuscript",
            "statements": [_stmt("P3959", "990001404380205171")],
        }

        async def fake_read(_factory, pairs):
            assert pairs == [("P3959", "990001404380205171")]
            return {pairs[0]: [{"qid": "Q999", "matched_on": "P3959=…", "label": "x"}]}

        with patch(
            "app.pipeline.wikidata_duplicate_probe._read_cached_pairs", fake_read,
        ):
            asyncio.run(attach_cached_duplicate_evidence(object(), [item]))
        existence = item["_wikidata_existence"]
        assert existence["status"] == STATUS_CANDIDATES
        assert existence["candidates"][0]["qid"] == "Q999"

    def test_no_cache_entry_reads_as_not_probed_never_absent(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import (
            STATUS_NOT_PROBED,
            attach_cached_duplicate_evidence,
        )

        item = {
            "entity_type": "manuscript",
            "statements": [_stmt("P3959", "990001404380205171")],
        }

        async def fake_read(_factory, _pairs):
            return {}

        with patch(
            "app.pipeline.wikidata_duplicate_probe._read_cached_pairs", fake_read,
        ):
            asyncio.run(attach_cached_duplicate_evidence(object(), [item]))
        assert item["_wikidata_existence"]["status"] == STATUS_NOT_PROBED

    def test_a_partially_cached_item_is_skipped_not_absent(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import (
            attach_cached_duplicate_evidence,
        )

        item = {
            "entity_type": "manuscript",
            "statements": [
                _stmt("P3959", "990001404380205171"),
                _stmt("P195", "Q1028334"),
                _stmt("P217", "F 18760"),
            ],
        }

        async def fake_read(_factory, _pairs):
            return {("P3959", "990001404380205171"): []}

        with patch(
            "app.pipeline.wikidata_duplicate_probe._read_cached_pairs", fake_read,
        ):
            asyncio.run(attach_cached_duplicate_evidence(object(), [item]))
        assert item["_wikidata_existence"]["status"] == STATUS_SKIPPED
        assert "NOT established" in item["_wikidata_existence"]["note"]
