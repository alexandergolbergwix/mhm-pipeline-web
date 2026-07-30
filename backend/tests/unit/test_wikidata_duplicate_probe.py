"""Rule W-139 — verify checks Wikidata for an existing item before CREATE."""

from __future__ import annotations

import asyncio
import urllib.error

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
