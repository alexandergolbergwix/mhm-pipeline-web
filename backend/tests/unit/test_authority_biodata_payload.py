"""Unit tests — biodata fields attached during DesktopMatcher._match_one."""

from __future__ import annotations

import pytest

from tests.unit.test_biodata_enrich import VIAF_RASHI_CLUSTER


@pytest.mark.asyncio
async def test_match_one_attaches_biodata_from_viaf_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.pipeline import authority as auth_mod

    cluster_calls: list[str] = []

    class FakeViaf:
        def get_cluster_biodata(self, viaf_id: str) -> dict:
            cluster_calls.append(viaf_id)
            return VIAF_RASHI_CLUSTER

    async def fake_mazal_person(self, text, *, db_session, user_id, skip_cache, marc_dates=None):
        return None

    async def fake_viaf_meta(self, text, *, db_session, user_id, skip_cache, marc_dates=None):
        return {
            "viaf_id": "27066507",
            "preferred_name_lat": "Rashi, 1040-1105",
            "birth_year": 1040,
            "death_year": 1105,
            "name_type": "Personal",
            "gnd": "118596606",
            "lc": "n79021400",
        }

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(auth_mod.DesktopMatcher, "_mazal_match_person", fake_mazal_person)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_mazal_get_details", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_mazal_match_place_authority", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_kima_match_place", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_kima_enrich_place", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_viaf_match_with_metadata", fake_viaf_meta)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_match_person", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_match_by_mazal", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_dates", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_enrich_qid", noop)

    matcher = object.__new__(auth_mod.DesktopMatcher)
    matcher._mazal = None
    matcher._viaf = FakeViaf()
    matcher._wikidata = None
    matcher._kima = None
    matcher._mazal_detail_cache = {}
    matcher._kima_detail_cache = {}
    matcher._authority_backend = None

    results = await matcher._match_one(
        text="שלמה בן יצחק",
        role="author",
        entity_kind="person",
        marc_record={"dates": {"year": 1650}},
        db_session=None,
        user_id=None,
        skip_cache=False,
    )

    assert len(results) == 1
    payload = results[0].payload
    assert payload.get("biodata_sources") == ["viaf"]
    assert "rabbi" in (payload.get("occupations") or [])
    assert "Bible commentator" in (payload.get("occupations") or [])
    assert cluster_calls == ["27066507"]
