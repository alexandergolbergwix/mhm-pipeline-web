"""Rule W-80 — the judge sees the same gloss the curator does.

A QID reconciled at runtime (a KIMA place on P1071, a VIAF-matched person on
P3342) is in no static table, so it reached the judge as a bare Q-number while
the frontend lazy-fetched a label for the very same value. 13 such QIDs were
ungloss ed in the run-48ba6c13 export.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.pipeline.wikidata_verify_evidence import (
    attach_live_value_labels,
    build_statement_value_labels,
    statement_qids_needing_labels,
)


def _item(*values: str) -> dict:
    return {
        "local_id": "QDraft_MS_1",
        "entity_type": "manuscript",
        "statements": [{"property_id": "P1071", "value": v} for v in values],
    }


class TestWhichQidsNeedALiveLookup:
    def test_a_statically_glossed_qid_is_not_looked_up(self) -> None:
        """Q87167 is ours; we already decided what it means."""
        assert statement_qids_needing_labels([_item("Q87167")]) == []

    def test_a_runtime_reconciled_qid_is_looked_up(self) -> None:
        assert statement_qids_needing_labels([_item("Q160544")]) == ["Q160544"]

    def test_a_statement_that_already_carries_a_label_is_skipped(self) -> None:
        item = _item("Q160544")
        item["statements"][0]["value_label"] = "Fez"
        assert statement_qids_needing_labels([item]) == []

    def test_a_local_reference_is_not_a_qid(self) -> None:
        assert statement_qids_needing_labels([_item("__LOCAL:person::x")]) == []

    def test_duplicates_are_asked_for_once(self) -> None:
        assert statement_qids_needing_labels(
            [_item("Q160544"), _item("Q160544")],
        ) == ["Q160544"]


class TestAttachingThem:
    def test_a_resolved_label_reaches_the_verify_pack(self) -> None:
        item = _item("Q160544")

        async def fake_resolve(_db, ids, **_kw):
            return {i: "Fez" for i in ids}

        with patch("app.routers.wikidata_labels.resolve_labels", fake_resolve):
            count = asyncio.run(attach_live_value_labels(object(), [item]))

        assert count == 1
        assert build_statement_value_labels(item)["Q160544"] == "Fez"

    def test_a_lookup_failure_leaves_the_bare_qid(self) -> None:
        """A gloss is presentation; it must never fail a verify run."""
        item = _item("Q160544")

        async def boom(_db, _ids, **_kw):
            raise RuntimeError("wikidata unreachable")

        with patch("app.routers.wikidata_labels.resolve_labels", boom):
            assert asyncio.run(attach_live_value_labels(object(), [item])) == 0
        assert "Q160544" not in build_statement_value_labels(item)

    def test_nothing_to_resolve_makes_no_call(self) -> None:
        calls: list[int] = []

        async def counted(_db, ids, **_kw):
            calls.append(len(ids))
            return {}

        with patch("app.routers.wikidata_labels.resolve_labels", counted):
            asyncio.run(attach_live_value_labels(object(), [_item("Q87167")]))
        assert calls == []
