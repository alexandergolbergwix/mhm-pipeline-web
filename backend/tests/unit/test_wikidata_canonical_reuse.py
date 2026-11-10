"""The canonical freshness check reuses the stored fingerprint only for a matching stamp."""

import uuid

import pytest

from app.models.hmo_canonical_entity import HmoCanonicalEntity
from app.routers import wikidata_studio

RUN_ID = uuid.UUID(int=7)
STAMP = "3|0000000a-0000-0000-0000-000000000000|2026-11-10T10:00:00+00:00"
OTHER_STAMP = "4|0000000b-0000-0000-0000-000000000000|2026-11-10T11:00:00+00:00"
ENRICHMENT = "enrichment-now"
STORED_FINGERPRINT = "stored-canonical-fingerprint"
RECOMPUTED_FINGERPRINT = "full-recompute"


def _summary(*, stamp: str = STAMP, enrichment: str = ENRICHMENT) -> dict[str, str]:
    return {
        "canonical_reuse_key": f"{stamp}|{enrichment}",
        "canonical_reuse_fingerprint": STORED_FINGERPRINT,
    }


def _patch_stamp(monkeypatch, stamp: str) -> None:
    async def fake_stamp(db, run_id):
        return stamp

    monkeypatch.setattr(wikidata_studio, "_canonical_stamp", fake_stamp)


def _patch_full_recompute(monkeypatch) -> list[str]:
    calls: list[str] = []

    async def fake_recompute(db, run_id, enrichment_fingerprint, *, release_read_transaction=False):
        calls.append(enrichment_fingerprint)
        return RECOMPUTED_FINGERPRINT

    monkeypatch.setattr(wikidata_studio, "_canonical_cache_fingerprint", fake_recompute)
    return calls


async def test_matching_key_returns_stored_fingerprint_without_recompute(monkeypatch):
    _patch_stamp(monkeypatch, STAMP)

    async def must_not_run(*args, **kwargs):
        raise AssertionError("full canonical recompute must not run on a matching key")

    monkeypatch.setattr(wikidata_studio, "_canonical_cache_fingerprint", must_not_run)
    result = await wikidata_studio._canonical_fingerprint_for_check(
        None, RUN_ID, ENRICHMENT, _summary(),
    )
    assert result == STORED_FINGERPRINT


async def test_different_stamp_falls_back_to_full_recompute(monkeypatch):
    _patch_stamp(monkeypatch, OTHER_STAMP)
    calls = _patch_full_recompute(monkeypatch)
    result = await wikidata_studio._canonical_fingerprint_for_check(
        None, RUN_ID, ENRICHMENT, _summary(),
    )
    assert result == RECOMPUTED_FINGERPRINT
    assert calls == [ENRICHMENT]


async def test_different_enrichment_key_falls_back_to_full_recompute(monkeypatch):
    _patch_stamp(monkeypatch, STAMP)
    calls = _patch_full_recompute(monkeypatch)
    result = await wikidata_studio._canonical_fingerprint_for_check(
        None, RUN_ID, "enrichment-changed", _summary(),
    )
    assert result == RECOMPUTED_FINGERPRINT
    assert calls == ["enrichment-changed"]


@pytest.mark.parametrize(
    "summary",
    [
        {},
        {"canonical_reuse_key": f"{STAMP}|{ENRICHMENT}"},
        {"canonical_reuse_fingerprint": STORED_FINGERPRINT},
        {"canonical_reuse_key": f"{STAMP}|{ENRICHMENT}", "canonical_reuse_fingerprint": ""},
    ],
    ids=["no-keys", "no-fingerprint", "no-key", "empty-fingerprint"],
)
async def test_missing_reuse_keys_fall_back_to_full_recompute(monkeypatch, summary):
    _patch_stamp(monkeypatch, STAMP)
    calls = _patch_full_recompute(monkeypatch)
    result = await wikidata_studio._canonical_fingerprint_for_check(
        None, RUN_ID, ENRICHMENT, summary,
    )
    assert result == RECOMPUTED_FINGERPRINT
    assert calls == [ENRICHMENT]


async def test_build_stamp_is_not_read_when_rows_were_loaded_earlier(monkeypatch):
    async def must_not_run(db, run_id):
        raise AssertionError("a stamp read after an earlier load could hide a change")

    monkeypatch.setattr(wikidata_studio, "_canonical_stamp", must_not_run)
    stamp = await wikidata_studio._canonical_build_stamp(
        None, RUN_ID, {"entities": [{"local_id": "Work_1"}], "last_id": uuid.UUID(int=1)},
    )
    assert stamp is None


async def test_build_stamp_reuses_saved_stamp_on_fingerprint_resume(monkeypatch):
    async def must_not_run(db, run_id):
        raise AssertionError("a resumed fingerprint must reuse the saved stamp")

    monkeypatch.setattr(wikidata_studio, "_canonical_stamp", must_not_run)
    saved = {"canonical_fp": STORED_FINGERPRINT, "canonical_stamp": STAMP}
    assert await wikidata_studio._canonical_build_stamp(None, RUN_ID, saved) == STAMP
    assert await wikidata_studio._canonical_build_stamp(
        None, RUN_ID, {"canonical_fp": STORED_FINGERPRINT},
    ) is None


def _canonical_row(run_id: uuid.UUID, local_id: str) -> HmoCanonicalEntity:
    return HmoCanonicalEntity(
        run_id=run_id, local_id=local_id, source_uri=f"https://example.org/{local_id}",
        wikibase_id="Q1", source_fingerprint="a" * 64, snapshot={"local_id": local_id},
    )


async def test_canonical_stamp_changes_when_a_row_is_added(db_session):
    run_id = uuid.uuid4()
    empty = await wikidata_studio._canonical_stamp(db_session, run_id)
    db_session.add(_canonical_row(run_id, "Work_1"))
    await db_session.flush()
    one = await wikidata_studio._canonical_stamp(db_session, run_id)
    db_session.add(_canonical_row(run_id, "Work_2"))
    await db_session.flush()
    two = await wikidata_studio._canonical_stamp(db_session, run_id)
    assert empty == "0||"
    assert one.startswith("1|")
    assert two.startswith("2|")
    assert len({empty, one, two}) == 3
