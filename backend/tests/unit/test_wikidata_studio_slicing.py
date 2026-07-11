"""Unit tests for the server-side slicing layer added to the Wikidata Studio.

Covers:
- entity_type filter
- substring search (q param) across labels / descriptions / aliases / QID / type
- sort by label / statements / entity_type / wikidata
- sort_dir asc vs desc
- pagination (page / page_size)
- approved_item_count precomputation
- properties / property_labels precomputation
- edge cases: empty list, page beyond results
"""

from __future__ import annotations

import pytest

from app.routers.wikidata_studio import _slice_items


# ── helpers ─────────────────────────────────────────────────────────────


def _item(
    entity_type: str = "manuscript",
    en_label: str = "Test",
    he_label: str = "",
    qid: str | None = None,
    statements: int = 2,
    approved: bool | None = None,
    props: list[str] | None = None,
) -> dict:
    stmts = []
    for i, pid in enumerate(props or ["P31"] * statements):
        stmts.append({
            "property": pid,
            "property_label": f"label-{pid}",
            "value": f"Q{100 + i}",
            "value_id": f"Q{100 + i}",
        })
    item: dict = {
        "entity_type": entity_type,
        "labels": {"en": en_label} | ({"he": he_label} if he_label else {}),
        "descriptions": {"en": f"desc of {en_label}"},
        "aliases": {},
        "statements": stmts,
        "existing_qid": qid,
        "local_id": f"{entity_type}::{en_label}",
        "approved": approved,
    }
    return item


def _slice(**kw):
    """Thin wrapper — supply defaults for all required kwargs."""
    defaults = dict(
        all_items=[],
        entity_type=None,
        q=None,
        upload_outcome=None,
        sort="label",
        sort_dir="asc",
        page=1,
        page_size=50,
    )
    defaults.update(kw)
    return _slice_items(**defaults)


# ── entity_type filter ───────────────────────────────────────────────────


class TestEntityTypeFilter:
    def test_all_when_entity_type_none(self) -> None:
        items = [_item("manuscript"), _item("person"), _item("work")]
        page_items, total, *_ = _slice(all_items=items)
        assert total == 3
        assert len(page_items) == 3

    def test_filter_manuscript(self) -> None:
        items = [_item("manuscript"), _item("person"), _item("work"), _item("manuscript")]
        page_items, total, *_ = _slice(all_items=items, entity_type="manuscript")
        assert total == 2
        assert all(it["entity_type"] == "manuscript" for it in page_items)

    def test_filter_person(self) -> None:
        items = [_item("manuscript"), _item("person")]
        page_items, total, *_ = _slice(all_items=items, entity_type="person")
        assert total == 1

    def test_all_value_treated_as_no_filter(self) -> None:
        items = [_item("manuscript"), _item("person")]
        # entity_type="all" triggers the branch that skips the filter
        page_items, total, *_ = _slice(all_items=items, entity_type="all")
        assert total == 2

    def test_unknown_type_returns_empty(self) -> None:
        items = [_item("manuscript"), _item("person")]
        page_items, total, *_ = _slice(all_items=items, entity_type="unknown_type")
        assert total == 0
        assert page_items == []


# ── substring search (q param) ───────────────────────────────────────────


class TestSubstringSearch:
    def test_match_en_label(self) -> None:
        items = [_item(en_label="Talmud Bavli"), _item(en_label="Mishneh Torah")]
        page_items, total, *_ = _slice(all_items=items, q="talmud")
        assert total == 1
        assert page_items[0]["labels"]["en"] == "Talmud Bavli"

    def test_match_description(self) -> None:
        items = [_item(en_label="Alpha"), _item(en_label="Beta")]
        # descriptions are "desc of Alpha" / "desc of Beta"
        page_items, total, *_ = _slice(all_items=items, q="desc of beta")
        assert total == 1

    def test_match_existing_qid(self) -> None:
        items = [_item(en_label="A", qid="Q12345"), _item(en_label="B")]
        page_items, total, *_ = _slice(all_items=items, q="Q12345")
        assert total == 1

    def test_match_entity_type(self) -> None:
        items = [_item("manuscript"), _item("person")]
        page_items, total, *_ = _slice(all_items=items, q="person")
        assert total == 1

    def test_case_insensitive(self) -> None:
        items = [_item(en_label="MAIMONIDES"), _item(en_label="Rashi")]
        page_items, total, *_ = _slice(all_items=items, q="maimonides")
        assert total == 1

    def test_empty_q_returns_all(self) -> None:
        items = [_item(), _item()]
        page_items, total, *_ = _slice(all_items=items, q="")
        assert total == 2

    def test_whitespace_only_q_returns_all(self) -> None:
        items = [_item(), _item()]
        page_items, total, *_ = _slice(all_items=items, q="   ")
        assert total == 2


# ── sort ─────────────────────────────────────────────────────────────────


class TestSort:
    def test_sort_label_asc(self) -> None:
        items = [_item(en_label="Zeta"), _item(en_label="Alpha"), _item(en_label="Mu")]
        page_items, *_ = _slice(all_items=items, sort="label", sort_dir="asc")
        labels = [it["labels"]["en"] for it in page_items]
        assert labels == sorted(labels, key=str.lower)

    def test_sort_label_desc(self) -> None:
        items = [_item(en_label="Zeta"), _item(en_label="Alpha"), _item(en_label="Mu")]
        page_items, *_ = _slice(all_items=items, sort="label", sort_dir="desc")
        labels = [it["labels"]["en"] for it in page_items]
        assert labels == sorted(labels, key=str.lower, reverse=True)

    def test_sort_statements_asc(self) -> None:
        items = [
            _item(en_label="Few", statements=1),
            _item(en_label="Many", statements=10),
            _item(en_label="Mid", statements=5),
        ]
        page_items, *_ = _slice(all_items=items, sort="statements", sort_dir="asc")
        counts = [len(it["statements"]) for it in page_items]
        assert counts == sorted(counts)

    def test_sort_entity_type_asc(self) -> None:
        items = [_item("work"), _item("manuscript"), _item("person")]
        page_items, *_ = _slice(all_items=items, sort="entity_type", sort_dir="asc")
        types = [it["entity_type"] for it in page_items]
        assert types == sorted(types)

    def test_sort_wikidata_asc_puts_existing_first(self) -> None:
        items = [
            _item(en_label="NoQID"),
            _item(en_label="HasQID", qid="Q999"),
        ]
        page_items, *_ = _slice(all_items=items, sort="wikidata", sort_dir="asc")
        assert page_items[0]["existing_qid"] == "Q999"

    def test_sort_wikidata_desc_puts_new_first(self) -> None:
        items = [
            _item(en_label="HasQID", qid="Q999"),
            _item(en_label="NoQID"),
        ]
        page_items, *_ = _slice(all_items=items, sort="wikidata", sort_dir="desc")
        assert page_items[0]["existing_qid"] is None


# ── pagination ───────────────────────────────────────────────────────────


class TestPagination:
    def test_page1_returns_first_n(self) -> None:
        items = [_item(en_label=str(i)) for i in range(10)]
        page_items, total, *_ = _slice(all_items=items, page=1, page_size=3)
        assert total == 10
        assert len(page_items) == 3

    def test_page2(self) -> None:
        items = [_item(en_label=str(i)) for i in range(10)]
        page_items, total, *_ = _slice(all_items=items, page=2, page_size=3)
        assert total == 10
        assert len(page_items) == 3

    def test_last_page_partial(self) -> None:
        items = [_item(en_label=str(i)) for i in range(7)]
        page_items, total, *_ = _slice(all_items=items, page=3, page_size=3)
        assert total == 7
        assert len(page_items) == 1

    def test_page_beyond_results_returns_empty(self) -> None:
        items = [_item() for _ in range(5)]
        page_items, total, *_ = _slice(all_items=items, page=99, page_size=10)
        assert total == 5
        assert page_items == []

    def test_page_size_larger_than_items(self) -> None:
        items = [_item() for _ in range(3)]
        page_items, total, *_ = _slice(all_items=items, page=1, page_size=100)
        assert total == 3
        assert len(page_items) == 3


# ── approved_item_count ──────────────────────────────────────────────────


class TestApprovedItemCount:
    def test_counts_only_true_not_none(self) -> None:
        items = [
            _item(approved=True),
            _item(approved=True),
            _item(approved=False),
            _item(approved=None),
            _item(),          # no key at all
        ]
        *_, approved_count = _slice(all_items=items)
        assert approved_count == 2

    def test_zero_when_none_approved(self) -> None:
        items = [_item(approved=False), _item(approved=None)]
        *_, approved_count = _slice(all_items=items)
        assert approved_count == 0

    def test_count_is_from_full_build_not_page(self) -> None:
        """approved_item_count must reflect ALL items, not just the current page."""
        items = [_item(approved=True) for _ in range(20)]
        # page_size=5 means only 5 items come back, but count should be 20
        page_items, total, props, plabels, approved_count = _slice(
            all_items=items, page=1, page_size=5,
        )
        assert len(page_items) == 5
        assert approved_count == 20


# ── properties / property_labels ────────────────────────────────────────


class TestPropertiesPrecomputation:
    def test_distinct_properties_sorted(self) -> None:
        items = [
            _item(props=["P31", "P50"]),
            _item(props=["P31", "P407"]),
        ]
        _, _, props, plabels, _ = _slice(all_items=items)
        prop_ids = [p.id for p in props]
        # Distinct: P31, P50, P407 (3 unique)
        assert len(set(prop_ids)) == len(prop_ids)   # no duplicates
        assert set(prop_ids) == {"P31", "P50", "P407"}

    def test_property_labels_populated(self) -> None:
        # The _item helper stamps property_label="label-P31" on the statement.
        # _slice_items reads that first, so the label in plabels is "label-P31".
        # When no property_label is present on the statement, it falls back to
        # the static PROPERTY_LABELS dict.
        items = [_item(props=["P31"])]
        _, _, props, plabels, _ = _slice(all_items=items)
        assert "P31" in plabels
        # The statement carries property_label="label-P31" (set by _item helper)
        assert plabels["P31"] == "label-P31"

    def test_property_labels_falls_back_to_static_dict(self) -> None:
        # Build an item whose statement has no property_label key so the
        # static dict path is exercised.
        stmt = {"property": "P31", "value": "Q5"}   # no property_label
        item = {
            "entity_type": "manuscript",
            "labels": {"en": "Test"},
            "descriptions": {},
            "aliases": {},
            "statements": [stmt],
            "existing_qid": None,
            "local_id": "manuscript::Test",
            "approved": None,
        }
        _, _, props, plabels, _ = _slice(all_items=[item])
        assert "P31" in plabels
        assert plabels["P31"] == "instance of"

    def test_properties_cover_full_build_not_page(self) -> None:
        """properties must come from ALL items, even those not on the current page."""
        items = [
            _item(en_label="A", props=["P31"]),
            _item(en_label="B", props=["P50"]),   # on page 2
        ]
        page_items, total, props, plabels, _ = _slice(
            all_items=items, page=1, page_size=1,
        )
        # Page 1 has only item A (P31), but properties should include P50 too.
        assert len(page_items) == 1
        prop_ids = {p.id for p in props}
        assert "P31" in prop_ids
        assert "P50" in prop_ids

    def test_empty_items_returns_empty_aggregates(self) -> None:
        page_items, total, props, plabels, approved_count = _slice(all_items=[])
        assert total == 0
        assert page_items == []
        assert props == []
        assert plabels == {}
        assert approved_count == 0


# ── cache_stale response flag ────────────────────────────────────────────


class TestCacheStaleResponse:
    def test_studio_response_sets_cache_stale_flag(self) -> None:
        from app.models.wikidata_studio_cache import WikidataStudioCache
        from app.routers.wikidata_studio import (
            StudioSummary,
            _studio_response_from_cache,
        )

        cached = WikidataStudioCache(
            run_id=__import__("uuid").uuid4(),
            approved_only=True,
            input_fingerprint="a" * 64,
            result_items=[_item(en_label="Cached MS")],
            quickstatements="",
            summary={
                "total_items": 1,
                "manuscripts": 1,
                "persons": 0,
                "works": 0,
                "statements": 1,
            },
            approved_match_count=1,
            pending_match_count=0,
            used_match_count=1,
            record_count=1,
        )
        resp = _studio_response_from_cache(
            cached,
            cached.result_items or [],
            approved_only=True,
            entity_type=None,
            q=None,
            upload_outcome=None,
            sort="label",
            sort_dir="asc",
            page=1,
            page_size=50,
            cache_stale=True,
        )
        assert resp.cache_stale is True
        assert resp.summary == StudioSummary(**cached.summary)
        assert len(resp.items) == 1

    def test_studio_response_defaults_cache_stale_false(self) -> None:
        from app.models.wikidata_studio_cache import WikidataStudioCache
        from app.routers.wikidata_studio import _studio_response_from_cache

        cached = WikidataStudioCache(
            run_id=__import__("uuid").uuid4(),
            approved_only=True,
            input_fingerprint="b" * 64,
            result_items=[],
            quickstatements="",
            summary={
                "total_items": 0,
                "manuscripts": 0,
                "persons": 0,
                "works": 0,
                "statements": 0,
            },
            approved_match_count=0,
            pending_match_count=0,
            used_match_count=0,
            record_count=0,
        )
        resp = _studio_response_from_cache(
            cached,
            [],
            approved_only=True,
            entity_type=None,
            q=None,
            upload_outcome=None,
            sort="label",
            sort_dir="asc",
            page=1,
            page_size=50,
        )
        assert resp.cache_stale is False


class TestUploadOutcomeFilter:
    def test_filter_by_upload_outcome(self) -> None:
        items = [
            {**_item(en_label="A"), "upload_outcome": "create"},
            {**_item(en_label="B"), "upload_outcome": "blocked"},
            {**_item(en_label="C"), "upload_outcome": "create"},
        ]
        page_items, total, *_ = _slice(all_items=items, upload_outcome="create")
        assert total == 2
        assert {it["labels"]["en"] for it in page_items} == {"A", "C"}
