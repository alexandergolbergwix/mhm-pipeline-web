"""Rule W-139 — verify checks Wikidata for an existing item before CREATE."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
from unittest.mock import patch

from app.pipeline.wikidata_duplicate_probe import (
    STATUS_ABSENT,
    STATUS_CANDIDATES,
    STATUS_HAS_QID,
    STATUS_NOT_RUN,
    STATUS_SKIPPED,
    STATUS_UNAVAILABLE,
    _pair_result,
    attach_duplicate_evidence,
    duplicate_class_for_item,
    duplicate_status_for_item,
    identity_probes,
    search_by_statement,
    stamp_duplicate_check,
)
from app.pipeline.wikidata_verify_evidence import (
    build_verify_evidence_pack,
    enrich_items_with_verify_evidence,
)


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


def _probe_one(item: dict, *, fetch) -> dict:
    """The per-item answer, via the only production entry point."""
    asyncio.run(attach_duplicate_evidence(None, [item], fetch=fetch))
    return item["_wikidata_existence"]


class TestSingleItemAnswer:
    def test_duplicate_is_surfaced(self) -> None:
        result = _probe_one(_person(), fetch=_hit("Q118924043"))
        assert result["status"] == STATUS_CANDIDATES
        assert result["candidates"][0]["qid"] == "Q118924043"

    def test_item_with_existing_qid_is_an_update_not_a_create(self) -> None:
        result = _probe_one(_person(existing_qid="Q42"), fetch=_boom)
        assert result["status"] == STATUS_HAS_QID

    def test_item_without_identifiers_says_absence_is_not_established(self) -> None:
        result = _probe_one({"entity_type": "work", "statements": []}, fetch=_miss)
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
            # The composite key is batched on its selective PID (P217), so that is
            # the request to fail — and the per-key fallback fails with it.
            if "P217" in url or "P217%3D" in url:
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
            return {
                pairs[0]: _pair_result(
                    [{"qid": "Q999", "matched_on": "P3959=…", "label": "x"}],
                ),
            }

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
            return {("P3959", "990001404380205171"): _pair_result([])}

        with patch(
            "app.pipeline.wikidata_duplicate_probe._read_cached_pairs", fake_read,
        ):
            asyncio.run(attach_cached_duplicate_evidence(object(), [item]))
        assert item["_wikidata_existence"]["status"] == STATUS_SKIPPED
        assert "NOT established" in item["_wikidata_existence"]["note"]


def _is_group_query(url: str) -> bool:
    """True for a batched group query: `... OR ...` (titles) or `A|B` (composites)."""
    decoded = urllib.parse.unquote_plus(url)
    return " OR " in decoded or "|" in decoded


class TestUnbatchableProbesAreBounded:
    """Rule W-144/W-145: batched by group; the cap bounds only the residue."""

    def _works(self, count: int, *, class_qid: str = "Q47461344") -> list[dict[str, object]]:
        return [
            {
                "entity_type": "work",
                "statements": [
                    _stmt("P1476", f"title {i}"), _stmt("P31", class_qid),
                ],
            }
            for i in range(count)
        ]

    def _manuscripts(self, count: int) -> list[dict[str, object]]:
        return [
            {
                "entity_type": "manuscript",
                "statements": [
                    _stmt("P195", "Q1028334"), _stmt("P217", f"F {1000 + i}"),
                ],
            }
            for i in range(count)
        ]

    def test_title_keys_of_one_class_share_a_single_request(self) -> None:
        """The starvation fix: 10 works cost one search, not ten."""
        searches: list[str] = []

        def fetch(url: str, timeout: float | None = None) -> dict[str, object]:
            searches.append(url)
            return {"query": {"search": []}}

        items = self._works(10)
        stats = asyncio.run(attach_duplicate_evidence(None, items, fetch=fetch))
        assert len(searches) == 1
        assert "dropped_unbatched" not in stats
        assert all(
            i["_wikidata_existence"]["status"] == STATUS_ABSENT for i in items
        )

    def test_the_budget_caps_only_the_residue(self, monkeypatch) -> None:
        """When a group errors, its keys fall back one-by-one — and that is capped."""
        monkeypatch.setenv("WIKIDATA_DUPLICATE_PROBE_UNBATCHED_MAX", "3")
        monkeypatch.setenv("WIKIDATA_DUPLICATE_PROBE_TITLE_GROUP", "10")
        single_calls = 0

        def fetch(url: str, timeout: float | None = None) -> dict[str, object]:
            nonlocal single_calls
            if _is_group_query(url):
                raise urllib.error.URLError("group failed")
            single_calls += 1
            return {"query": {"search": []}}

        items = self._works(10)
        stats = asyncio.run(attach_duplicate_evidence(None, items, fetch=fetch))
        assert single_calls == 3
        assert stats["dropped_unbatched"] == 7

    def test_works_are_not_starved_when_manuscripts_fill_the_budget(
        self, monkeypatch,
    ) -> None:
        """The bug: "P195+P217" sorts before "title+P31", so works were always last."""
        monkeypatch.setenv("WIKIDATA_DUPLICATE_PROBE_UNBATCHED_MAX", "4")
        probed_classes: list[str] = []

        def fetch(url: str, timeout: float | None = None) -> dict[str, object]:
            if _is_group_query(url):
                raise urllib.error.URLError("group failed")
            decoded = urllib.parse.unquote_plus(url)
            probed_classes.append("title" if "inlabel" in decoded else "composite")
            return {"query": {"search": []}}

        items = self._manuscripts(10) + self._works(10)
        asyncio.run(attach_duplicate_evidence(None, items, fetch=fetch))
        # Both classes get a share; neither is systematically last.
        assert "title" in probed_classes
        assert "composite" in probed_classes

    def test_a_capped_key_reports_skipped_never_absent(self, monkeypatch) -> None:
        monkeypatch.setenv("WIKIDATA_DUPLICATE_PROBE_UNBATCHED_MAX", "1")

        def fetch(url: str, timeout: float | None = None) -> dict[str, object]:
            if _is_group_query(url):
                raise urllib.error.URLError("group failed")
            return {"query": {"search": []}}

        items = self._works(4)
        asyncio.run(attach_duplicate_evidence(None, items, fetch=fetch))
        statuses = [i["_wikidata_existence"]["status"] for i in items]
        assert statuses.count(STATUS_ABSENT) == 1
        assert statuses.count(STATUS_SKIPPED) == 3
        capped = [
            i for i in items
            if i["_wikidata_existence"].get("reason") == "capped"
        ]
        assert len(capped) == 3

    def test_repeated_rate_limiting_trips_the_breaker(self, monkeypatch) -> None:
        monkeypatch.setenv("WIKIDATA_DUPLICATE_PROBE_UNBATCHED_MAX", "50")
        monkeypatch.setenv("WIKIDATA_DUPLICATE_PROBE_TRIP", "2")
        single_calls = 0

        def fetch(url: str, timeout: float | None = None) -> dict[str, object]:
            nonlocal single_calls
            if not _is_group_query(url):
                single_calls += 1
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

        items = self._works(20)
        stats = asyncio.run(attach_duplicate_evidence(None, items, fetch=fetch))
        # Two per-key failures trip it; the remaining 18 are never attempted.
        assert single_calls == 2
        assert stats["dropped_unbatched"] == 18

    def test_a_recovery_resets_the_breaker(self, monkeypatch) -> None:
        monkeypatch.setenv("WIKIDATA_DUPLICATE_PROBE_UNBATCHED_MAX", "50")
        monkeypatch.setenv("WIKIDATA_DUPLICATE_PROBE_TRIP", "2")
        single_calls = 0

        def fetch(url: str, timeout: float | None = None) -> dict[str, object]:
            nonlocal single_calls
            if _is_group_query(url):
                raise urllib.error.URLError("group failed")
            single_calls += 1
            if single_calls == 1:
                raise urllib.error.HTTPError(url, 429, "Too Many", {}, None)
            return {"query": {"search": []}}

        items = self._works(5)
        stats = asyncio.run(attach_duplicate_evidence(None, items, fetch=fetch))
        assert single_calls == 5
        assert "dropped_unbatched" not in stats


class TestBatchedGroupProbes:
    """Rule W-144: a batched query is a superset; the AND is enforced locally."""

    def test_a_title_hit_is_attributed_only_to_the_title_it_carries(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import (
            _COMPOSITE_SEP,
            probe_titles_batch,
        )

        keys = [
            ("title+P31", _COMPOSITE_SEP.join(("Mahzor", "Q47461344"))),
            ("title+P31", _COMPOSITE_SEP.join(("Siddur", "Q47461344"))),
        ]

        def fetch(url: str, timeout: float | None = None) -> dict[str, object]:
            if "list=search" in url:
                return {"query": {"search": [{"title": "Q1"}, {"title": "Q2"}]}}
            return {
                "entities": {
                    "Q1": {"labels": {"en": {"value": "Mahzor"}}, "aliases": {}},
                    "Q2": {"labels": {"en": {"value": "Something else"}}, "aliases": {}},
                },
            }

        hits = probe_titles_batch(keys, fetch=fetch)
        assert [c["qid"] for c in hits[keys[0]]] == ["Q1"]
        assert hits[keys[1]] == []

    def test_a_title_candidate_still_requires_curator_confirmation(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import (
            _COMPOSITE_SEP,
            probe_titles_batch,
        )

        key = ("title+P31", _COMPOSITE_SEP.join(("Mahzor", "Q47461344")))

        def fetch(url: str, timeout: float | None = None) -> dict[str, object]:
            if "list=search" in url:
                return {"query": {"search": [{"title": "Q1"}]}}
            return {"entities": {"Q1": {"labels": {"en": {"value": "Mahzor"}}}}}

        candidate = probe_titles_batch([key], fetch=fetch)[key][0]
        assert candidate["requires_curator_confirmation"] == "true"
        assert candidate["matched_on"].startswith("title~")

    def test_a_shelfmark_only_hit_is_not_a_composite_candidate(self) -> None:
        """The batched query ORs P217; a hit must still carry the whole AND."""
        from app.pipeline.wikidata_duplicate_probe import (
            _COMPOSITE_SEP,
            probe_composites_batch,
        )

        key = ("P195+P217", _COMPOSITE_SEP.join(("Q1028334", "F 18760")))

        def fetch(url: str, timeout: float | None = None) -> dict[str, object]:
            if "list=search" in url:
                return {"query": {"search": [{"title": "Q9"}]}}
            return {
                "entities": {
                    "Q9": {
                        "claims": {
                            # Right shelfmark, WRONG holder.
                            "P217": [{"mainsnak": {"datavalue": {"value": "F 18760"}}}],
                            "P195": [
                                {"mainsnak": {"datavalue": {"value": {"id": "Q999"}}}},
                            ],
                        },
                    },
                },
            }

        assert probe_composites_batch([key], fetch=fetch)[key] == []

    def test_a_full_conjunction_hit_is_a_composite_candidate(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import (
            _COMPOSITE_SEP,
            probe_composites_batch,
        )

        key = ("P195+P217", _COMPOSITE_SEP.join(("Q1028334", "F 18760")))

        def fetch(url: str, timeout: float | None = None) -> dict[str, object]:
            if "list=search" in url:
                return {"query": {"search": [{"title": "Q9"}]}}
            return {
                "entities": {
                    "Q9": {
                        "claims": {
                            "P217": [{"mainsnak": {"datavalue": {"value": "F 18760"}}}],
                            "P195": [
                                {"mainsnak": {"datavalue": {"value": {"id": "Q1028334"}}}},
                            ],
                        },
                    },
                },
            }

        hits = probe_composites_batch([key], fetch=fetch)[key]
        assert [c["qid"] for c in hits] == ["Q9"]
        assert hits[0]["matched_on"] == "P195=Q1028334 AND P217=F 18760"


class TestDeferredMarkers:
    """Rule W-160: capped is a different fact from never attempted."""

    def test_a_capped_key_is_cached_as_deferred(self, monkeypatch) -> None:
        monkeypatch.setenv("WIKIDATA_DUPLICATE_PROBE_UNBATCHED_MAX", "0")
        written: dict = {}

        async def fake_write(_factory, results, *, deferred=None):
            written["results"] = results
            written["deferred"] = deferred or {}

        def fetch(url: str, timeout: float | None = None) -> dict[str, object]:
            raise urllib.error.URLError("group failed")

        items = [
            {
                "entity_type": "work",
                "statements": [_stmt("P1476", "t"), _stmt("P31", "Q47461344")],
            },
        ]
        with patch(
            "app.pipeline.wikidata_duplicate_probe._write_cached_pairs", fake_write,
        ):
            asyncio.run(attach_duplicate_evidence(object(), items, fetch=fetch))

        assert len(written["deferred"]) == 1
        reason = next(iter(written["deferred"].values()))
        assert "budget" in reason

    def test_a_deferred_row_is_a_miss_so_the_next_job_retries_it(self) -> None:
        """A deferred marker must never let the 7-day TTL freeze the cap."""
        from app.pipeline.wikidata_duplicate_probe import cached_pair_candidates

        assert cached_pair_candidates(_pair_result(None, deferred_reason="capped")) is None
        assert cached_pair_candidates(_pair_result([])) == []

    def test_a_legacy_cache_row_still_reads_as_answered(self) -> None:
        """A warm 7-day cache predates the `answer` field; do not throw it away."""
        from app.pipeline.wikidata_duplicate_probe import cached_pair_candidates

        assert cached_pair_candidates({"candidates": []}) == []

    def test_a_deferred_row_reads_as_capped_not_never_probed(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import (
            attach_cached_duplicate_evidence,
        )

        item = {
            "entity_type": "manuscript",
            "statements": [_stmt("P3959", "990001404380205171")],
        }

        async def fake_read(_factory, pairs):
            return {pairs[0]: _pair_result(None, deferred_reason="probe budget")}

        with patch(
            "app.pipeline.wikidata_duplicate_probe._read_cached_pairs", fake_read,
        ):
            asyncio.run(attach_cached_duplicate_evidence(object(), [item]))

        existence = item["_wikidata_existence"]
        assert existence["status"] == STATUS_SKIPPED
        assert existence["reason"] == "capped"
        assert "not probed" in existence["note"]


class TestOneWriterForTheDuplicateAnswer:
    """Rule W-159 — the two surfaces are the same object, in either order."""

    @staticmethod
    def _marc() -> list[dict[str, object]]:
        return [{"control_number": "990000403370205171", "title": "t"}]

    def test_probe_then_enrich_publishes_the_answer_into_the_pack(self) -> None:
        item = _person()
        asyncio.run(attach_duplicate_evidence(None, [item], fetch=_batch_hit))
        enrich_items_with_verify_evidence([item], self._marc())

        in_pack = item["verify_evidence"]["wikidata_existing"]["duplicate_check"]
        assert in_pack["status"] == STATUS_CANDIDATES
        assert in_pack is item["_wikidata_existence"]

    def test_enrich_then_probe_publishes_the_answer_into_the_pack(self) -> None:
        item = _person()
        enrich_items_with_verify_evidence([item], self._marc())
        assert (
            item["verify_evidence"]["wikidata_existing"]["duplicate_check"]["status"]
            == STATUS_NOT_RUN
        )

        asyncio.run(attach_duplicate_evidence(None, [item], fetch=_batch_hit))

        # This is the export (23) regression: the pack used to keep `not_run`
        # forever because it was built before the probe and never republished.
        in_pack = item["verify_evidence"]["wikidata_existing"]["duplicate_check"]
        assert in_pack["status"] == STATUS_CANDIDATES
        assert in_pack is item["_wikidata_existence"]

    def test_an_unprobed_item_reads_not_run_never_absent(self) -> None:
        item = _person()
        enrich_items_with_verify_evidence([item], self._marc())
        assert duplicate_status_for_item(item) == STATUS_NOT_RUN
        assert duplicate_class_for_item(item) == "unknown"

    def test_the_class_is_readable_from_the_pack_alone(self) -> None:
        """The read path has no `_wikidata_existence` — only the persisted pack."""
        item = _person()
        asyncio.run(attach_duplicate_evidence(None, [item], fetch=_batch_hit))
        enrich_items_with_verify_evidence([item], self._marc())
        item.pop("_wikidata_existence")

        assert duplicate_status_for_item(item) == STATUS_CANDIDATES
        assert duplicate_class_for_item(item) == "probed-conclusive"

    def test_stamping_never_invents_an_answer(self) -> None:
        item = _person()
        enrich_items_with_verify_evidence([item], self._marc())
        first = stamp_duplicate_check(item)
        assert first["status"] == STATUS_NOT_RUN
        assert first["candidates"] == []
        # The placeholder is NOT written back onto `_wikidata_existence`: doing so
        # made `attach_cached_duplicate_evidence` skip the item and read no cached
        # answer at all (export 24).
        assert item.get("_wikidata_existence") in (None, {})
        assert stamp_duplicate_check(item)["status"] == STATUS_NOT_RUN

    def test_a_skipped_answer_is_not_conclusive(self) -> None:
        item = {"entity_type": "work", "statements": []}
        asyncio.run(attach_duplicate_evidence(None, [item], fetch=_miss))
        assert duplicate_status_for_item(item) == STATUS_SKIPPED
        assert duplicate_class_for_item(item) == "unknown"


class TestThePlaceholderIsNotAnAnswer:
    """Export (24) read `not_run` on all 284 items — for the opposite reason to (23).

    The two surfaces agreed this time (Rule W-159 held), but both showed the
    PLACEHOLDER: `stamp_duplicate_check` wrote `not_run` onto
    `_wikidata_existence`, and `attach_cached_duplicate_evidence` skips any item
    that already has one, so the cached answer was never read. The persisted
    verdicts meanwhile held 255 absent / 15 candidates_found / 6 already_linked.
    """

    @staticmethod
    def _manuscript() -> dict:
        return {
            "local_id": "QDraft_MS_1",
            "entity_type": "manuscript",
            "statements": [
                _stmt("P31", "Q87167"),
                _stmt("P3959", "990000403370205171"),
            ],
        }

    def test_enriching_does_not_fake_an_answer(self) -> None:
        item = self._manuscript()
        enrich_items_with_verify_evidence([item], [])
        assert item.get("_wikidata_existence") in (None, {})
        # The pack still shows the placeholder, so the judge is told "unknown".
        pack = item["verify_evidence"]["wikidata_existing"]["duplicate_check"]
        assert pack["status"] == STATUS_NOT_RUN

    def test_a_cached_answer_is_read_after_enriching(self) -> None:
        """The exact export-(24) regression."""
        from app.pipeline.wikidata_duplicate_probe import (
            attach_cached_duplicate_evidence,
        )

        item = self._manuscript()
        enrich_items_with_verify_evidence([item], [])

        async def fake_read(_factory, pairs):
            return {
                p: _pair_result([{"qid": "Q999", "matched_on": "x", "label": "y"}])
                for p in pairs
            }

        with patch(
            "app.pipeline.wikidata_duplicate_probe._read_cached_pairs", fake_read,
        ):
            asyncio.run(attach_cached_duplicate_evidence(object(), [item]))

        answer = stamp_duplicate_check(item)
        assert answer["status"] == STATUS_CANDIDATES
        assert (
            item["verify_evidence"]["wikidata_existing"]["duplicate_check"]["status"]
            == STATUS_CANDIDATES
        )

    def test_has_duplicate_answer_rejects_the_placeholder(self) -> None:
        from app.pipeline.wikidata_duplicate_probe import has_duplicate_answer

        assert not has_duplicate_answer({})
        assert not has_duplicate_answer({"_wikidata_existence": {"status": "not_run"}})
        assert has_duplicate_answer({"_wikidata_existence": {"status": "absent"}})

    def test_a_real_answer_survives_a_later_stamp(self) -> None:
        item = self._manuscript()
        item["_wikidata_existence"] = {"status": STATUS_ABSENT, "candidates": []}
        enrich_items_with_verify_evidence([item], [])
        assert stamp_duplicate_check(item)["status"] == STATUS_ABSENT

    def test_an_existing_qid_beats_a_stale_duplicate_warning(self) -> None:
        item = self._manuscript()
        item["existing_qid"] = "Q118186113"
        item["ai_verdict"] = {
            "overall": "partial",
            "duplicate_status": STATUS_CANDIDATES,
        }
        enrich_items_with_verify_evidence([item], [])

        answer = stamp_duplicate_check(item)

        assert answer["status"] == STATUS_HAS_QID
        assert answer["existing_qid"] == "Q118186113"
        assert (
            item["verify_evidence"]["wikidata_existing"]["duplicate_check"]["status"]
            == STATUS_HAS_QID
        )

    def test_a_verdicts_recorded_status_survives_the_probe_cache_expiring(self) -> None:
        """The probe cache lives 7 days; a verdict lives 90 (Rule W-157)."""
        item = self._manuscript()
        item["ai_verdict"] = {
            "overall": "fail", "duplicate_status": STATUS_CANDIDATES,
            "duplicate_class": "probed-conclusive",
        }
        enrich_items_with_verify_evidence([item], [])
        answer = stamp_duplicate_check(item)
        assert answer["status"] == STATUS_CANDIDATES
        assert "stored verdict" in answer["note"]

    def test_a_stale_absent_never_suppresses_a_warning(self) -> None:
        """Only WARNINGS carry forward from a verdict — Rule W-144's whole point."""
        item = self._manuscript()
        item["ai_verdict"] = {"overall": "full", "duplicate_status": STATUS_ABSENT}
        enrich_items_with_verify_evidence([item], [])
        assert stamp_duplicate_check(item)["status"] == STATUS_NOT_RUN

    def test_a_live_conclusive_answer_beats_the_verdict(self) -> None:
        item = self._manuscript()
        item["ai_verdict"] = {"overall": "fail", "duplicate_status": STATUS_CANDIDATES}
        item["_wikidata_existence"] = {"status": STATUS_ABSENT, "candidates": []}
        enrich_items_with_verify_evidence([item], [])
        assert stamp_duplicate_check(item)["status"] == STATUS_ABSENT
