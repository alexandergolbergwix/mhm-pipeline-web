"""Unit tests for authority matcher routing fixes (Phase 1).

Tests:
- Place entities do NOT get a Mazal person ID from a homonym person name.
- KIMA hit propagates mazal_nli_id into the candidate's mazal_id.
- MARC $d dates narrow Mazal homonym resolution.
- Re-enrich upsert key includes role so author/subject rows are independent.
"""
from __future__ import annotations

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_entity(text: str, kind: str, role: str, dates: str = "") -> dict:
    ent = {"text": text, "kind": kind, "role": role}
    if dates:
        ent["dates"] = dates
    return ent


# ── Ingest: $d dates captured ──────────────────────────────────────────────


def test_author_dates_from_100d_collapse() -> None:
    """_collapse_marc_subfields should carry 100$d into the author dict."""
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {"100$a": "מימון, משה בן, 1138-1204", "100$d": "1138-1204"}
    _collapse_marc_subfields(record)
    authors = record.get("authors") or []
    assert authors, "should produce at least one author"
    assert any(a.get("dates") == "1138-1204" for a in authors if isinstance(a, dict)), (
        "dates from 100$d should be in the author dict"
    )


def test_contributor_dates_from_700d_collapse() -> None:
    """_collapse_marc_subfields should carry 700$d into the contributor dict."""
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {"700$a": "סעדיה גאון", "700$d": "882-942", "700$e": "editor"}
    _collapse_marc_subfields(record)
    contribs = record.get("contributors") or []
    assert any(c.get("dates") == "882-942" for c in contribs if isinstance(c, dict)), (
        "dates from 700$d should be in the contributor dict"
    )


def test_subject_dates_from_600d_collapse() -> None:
    """_collapse_marc_subfields should carry 600$d into the subject dict."""
    from app.pipeline.marc_ingest import _collapse_marc_subfields

    record: dict = {"600$a": "אלוני, נחמיה", "600$d": "1906-1983"}
    _collapse_marc_subfields(record)
    subjects = record.get("subjects") or []
    assert any(s.get("dates") == "1906-1983" for s in subjects if isinstance(s, dict)), (
        "dates from 600$d should be in the subject dict"
    )


def test_extract_named_entities_propagates_dates() -> None:
    """extract_named_entities must propagate 'dates' from author/contributor/subject dicts."""
    from app.pipeline.marc_ingest import extract_named_entities

    record = {
        "authors": [{"name": "מימון, משה בן", "role": "author", "field": "100", "dates": "1138-1204"}],
        "contributors": [{"name": "סעדיה גאון", "role": "editor", "field": "700", "dates": "882-942"}],
        "subjects": [{"name": "אלוני, נחמיה", "type": "person", "field": "600", "dates": "1906-1983"}],
    }
    entities = extract_named_entities(record)
    by_name = {e["text"]: e for e in entities}

    assert by_name.get("מימון, משה בן", {}).get("dates") == "1138-1204"
    assert by_name.get("סעדיה גאון", {}).get("dates") == "882-942"
    assert by_name.get("אלוני, נחמיה", {}).get("dates") == "1906-1983"


# ── Backend: Postgres ordered match_person ─────────────────────────────────


def test_postgres_backend_match_person_accepts_dates_param() -> None:
    """PostgresAuthorityBackend.match_person signature must accept a dates kwarg."""
    import inspect
    from app.pipeline.authority_backend import PostgresAuthorityBackend

    sig = inspect.signature(PostgresAuthorityBackend.match_person)
    assert "dates" in sig.parameters, "match_person must accept a 'dates' parameter"


def test_local_backend_match_person_accepts_dates_param() -> None:
    """LocalAuthorityBackend.match_person signature must accept a dates kwarg."""
    import inspect
    from app.pipeline.authority_backend import LocalAuthorityBackend

    sig = inspect.signature(LocalAuthorityBackend.match_person)
    assert "dates" in sig.parameters, "match_person must accept a 'dates' parameter"


def test_match_mazal_place_exists_on_backends() -> None:
    """All three backends must expose match_mazal_place."""
    from app.pipeline.authority_backend import (
        LocalAuthorityBackend,
        ModalAuthorityBackend,
        PostgresAuthorityBackend,
    )
    for cls in (LocalAuthorityBackend, ModalAuthorityBackend, PostgresAuthorityBackend):
        assert callable(getattr(cls, "match_mazal_place", None)), (
            f"{cls.__name__} must have match_mazal_place"
        )


# ── DesktopMatcher: place/person gating ────────────────────────────────────


def test_match_one_gates_person_matchers_for_places(monkeypatch: pytest.MonkeyPatch) -> None:
    """_match_one must NOT call _mazal_match_person for place entities."""
    import asyncio
    from app.pipeline import authority as auth_mod

    called_person: list[str] = []
    called_place: list[str] = []

    async def fake_mazal_person(self, text, *, db_session, user_id, skip_cache, marc_dates=None):
        called_person.append(text)
        return None

    async def fake_mazal_place_auth(self, text, *, db_session, user_id, skip_cache):
        called_place.append(text)
        return None

    async def fake_kima(self, text, *, db_session, user_id, skip_cache):
        return None

    async def fake_ashk_lookup(text):
        return None

    monkeypatch.setattr(auth_mod.DesktopMatcher, "_mazal_match_person", fake_mazal_person)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_mazal_match_place_authority", fake_mazal_place_auth)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_kima_match_place", fake_kima)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_kima_enrich_place", fake_kima)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_viaf_match_with_metadata", fake_mazal_person)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_match_person", fake_mazal_person)

    matcher = object.__new__(auth_mod.DesktopMatcher)
    matcher._mazal = object()  # truthy — Mazal is available (required to fire the place guard)
    matcher._viaf = None
    matcher._wikidata = None
    matcher._kima = None
    matcher._mazal_detail_cache = {}
    matcher._kima_detail_cache = {}
    matcher._authority_backend = None

    asyncio.run(
        matcher._match_one(
            text="ירושלים",
            role="production_place",
            entity_kind="place",
            marc_record={},
            db_session=None,
            user_id=None,
            skip_cache=False,
        )
    )
    assert "ירושלים" not in called_person, (
        "_mazal_match_person must NOT be called for place entities"
    )
    assert "ירושלים" in called_place, (
        "_mazal_match_place_authority must be called for place entities"
    )


def test_match_one_calls_person_matchers_for_persons(monkeypatch: pytest.MonkeyPatch) -> None:
    """_match_one must call _mazal_match_person for person entities."""
    import asyncio
    from app.pipeline import authority as auth_mod

    called_person: list[str] = []

    async def fake_mazal_person(self, text, *, db_session, user_id, skip_cache, marc_dates=None):
        called_person.append(text)
        return None

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(auth_mod.DesktopMatcher, "_mazal_match_person", fake_mazal_person)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_mazal_match_place_authority", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_kima_match_place", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_kima_enrich_place", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_viaf_match_with_metadata", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_match_person", noop)

    matcher = object.__new__(auth_mod.DesktopMatcher)
    matcher._mazal = object()  # truthy — Mazal is available
    matcher._viaf = None
    matcher._wikidata = None
    matcher._kima = None
    matcher._mazal_detail_cache = {}
    matcher._kima_detail_cache = {}
    matcher._authority_backend = None

    asyncio.run(
        matcher._match_one(
            text="אלוני, נחמיה",
            role="author",
            entity_kind="person",
            marc_record={},
            db_session=None,
            user_id=None,
            skip_cache=False,
        )
    )
    assert "אלוני, נחמיה" in called_person, (
        "_mazal_match_person must be called for person entities"
    )


# ── KIMA mazal_nli_id backfill ─────────────────────────────────────────────


def test_kima_payload_includes_mazal_nli_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """When KIMA returns a mazal_nli_id, kima_payload must preserve it so
    the mazal_id backfill logic in _match_one can use it."""
    import asyncio
    from app.pipeline import authority as auth_mod

    FAKE_KIMA_ROW = {
        "wikidata_uri": "https://www.wikidata.org/entity/Q1492",
        "kima_id": "kima-1",
        "primary_heb": "ירושלים",
        "primary_rom": "Jerusalem",
        "lat": 31.7683,
        "lon": 35.2137,
        "geonames_id": "293196",
        "viaf_id": "",
        "mazal_nli_id": "987007533094005171",
    }

    async def fake_kima_match(self, text, *, db_session, user_id, skip_cache):
        return FAKE_KIMA_ROW.get("wikidata_uri")

    async def fake_kima_enrich(self, text, *, db_session, user_id, skip_cache):
        return FAKE_KIMA_ROW

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(auth_mod.DesktopMatcher, "_kima_match_place", fake_kima_match)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_kima_enrich_place", fake_kima_enrich)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_mazal_match_place_authority", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_mazal_match_person", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_viaf_match_with_metadata", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_match_person", noop)

    matcher = object.__new__(auth_mod.DesktopMatcher)
    matcher._mazal = None
    matcher._viaf = None
    matcher._wikidata = None
    matcher._kima = object()  # truthy
    matcher._mazal_detail_cache = {}
    matcher._kima_detail_cache = {}
    matcher._authority_backend = None

    candidates = asyncio.run(
        matcher._match_one(
            text="ירושלים",
            role="production_place",
            entity_kind="place",
            marc_record={},
            db_session=None,
            user_id=None,
            skip_cache=False,
        )
    )
    assert candidates, "should produce a candidate from KIMA hit"
    c = candidates[0]
    assert c.mazal_id == "987007533094005171", (
        "mazal_id must be backfilled from kima_payload.mazal_nli_id"
    )


# ── Re-enrich upsert key includes role ─────────────────────────────────────


def test_infer_entity_kind_allony_fixture() -> None:
    """Regression: 710 second segment must route as person for matchers."""
    from app.pipeline.entity_kind_infer import infer_entity_kind

    assert infer_entity_kind("Allony, Nehemia", "710") == "person"
    assert infer_entity_kind("The National Library of Israel", "710") == "corporate"


def test_reenrich_upsert_key_includes_role() -> None:
    """The re-enrich index key must be a 4-tuple including role so author and
    subject rows for the same entity text are not collapsed into each other."""
    import inspect
    from app.pipeline import authority_re_enrich as re_mod

    src = inspect.getsource(re_mod.re_enrich_run)
    assert "m.role" in src, "re-enrich must include m.role in the existing_idx key"
    assert "entity.get(\"role\"" in src or "entity.get('role'" in src, (
        "re-enrich must include entity role in the lookup key"
    )
    assert inspect.getsource(re_mod.match_key).count("normalize_role") >= 1


# ── Non-person external enrichment (fail-closed) ──────────────────────────


def test_work_without_mazal_skips_wikidata_label(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unanchored work headings must not get Wikidata label enrichment."""
    import asyncio
    from app.pipeline import authority as auth_mod

    wd_calls: list[str] = []

    async def fake_wd_label(self, *, op, text, matcher_name, db_session, user_id, skip_cache, author=None):
        wd_calls.append(matcher_name)
        return "Q999"

    async def noop_viaf(*args, **kwargs):
        return "", {}

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(auth_mod.DesktopMatcher, "_mazal_match_work", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_match_typed", fake_wd_label)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_match_by_mazal", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_enrich_qid", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_viaf_match_typed", noop_viaf)

    matcher = object.__new__(auth_mod.DesktopMatcher)
    matcher._mazal = object()
    matcher._viaf = object()
    matcher._wikidata = object()
    matcher._kima = None
    matcher._mazal_detail_cache = {}
    matcher._kima_detail_cache = {}
    matcher._authority_backend = None

    candidates = asyncio.run(
        matcher._match_one(
            text="תלמוד בבלי",
            role="contained_work",
            entity_kind="work",
            marc_record={},
            db_session=None,
            user_id=None,
            skip_cache=False,
        )
    )
    assert candidates == []
    assert wd_calls == []


def test_work_with_mazal_allows_wikidata_label(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from app.pipeline import authority as auth_mod

    wd_calls: list[str] = []

    async def fake_mazal_work(self, text, *, marc_record, db_session, user_id, skip_cache):
        return "MAZAL_WORK_1"

    async def fake_wd_label(self, *, op, text, matcher_name, db_session, user_id, skip_cache, author=None):
        wd_calls.append(matcher_name)
        return "Q192043"

    async def noop_viaf(*args, **kwargs):
        return "", {}

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(auth_mod.DesktopMatcher, "_mazal_match_work", fake_mazal_work)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_match_typed", fake_wd_label)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_match_by_mazal", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_enrich_qid", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_viaf_match_typed", noop_viaf)

    matcher = object.__new__(auth_mod.DesktopMatcher)
    matcher._mazal = object()
    matcher._viaf = object()
    matcher._wikidata = object()
    matcher._kima = None
    matcher._mazal_detail_cache = {}
    matcher._kima_detail_cache = {}
    matcher._authority_backend = None

    candidates = asyncio.run(
        matcher._match_one(
            text="תלמוד בבלי",
            role="contained_work",
            entity_kind="work",
            marc_record={"authors": [{"name": "אנונימי"}]},
            db_session=None,
            user_id=None,
            skip_cache=False,
        )
    )
    assert candidates
    assert candidates[0].wikidata_qid == "Q192043"
    assert wd_calls == ["match_work"]


def test_topic_entity_kind_not_person(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from app.pipeline import authority as auth_mod

    async def fake_mazal_subject(self, text, *, db_session, user_id, skip_cache):
        return "MAZAL_SUBJ_1"

    async def fake_p8189(self, mazal_id, *, db_session, user_id, skip_cache):
        return "Q12345"

    async def noop_viaf(*args, **kwargs):
        return "", {}

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(auth_mod.DesktopMatcher, "_mazal_match_subject", fake_mazal_subject)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_match_by_mazal", fake_p8189)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_match_typed", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_wikidata_enrich_qid", noop)
    monkeypatch.setattr(auth_mod.DesktopMatcher, "_viaf_match_typed", noop_viaf)

    matcher = object.__new__(auth_mod.DesktopMatcher)
    matcher._mazal = object()
    matcher._viaf = None
    matcher._wikidata = object()
    matcher._kima = None
    matcher._mazal_detail_cache = {}
    matcher._kima_detail_cache = {}
    matcher._authority_backend = None

    candidates = asyncio.run(
        matcher._match_one(
            text="קבלה",
            role="subject",
            entity_kind="topic",
            marc_record={},
            db_session=None,
            user_id=None,
            skip_cache=False,
        )
    )
    assert candidates
    assert candidates[0].payload.get("wikidata_resolve_op") == "p8189"
    assert candidates[0].wikidata_qid == "Q12345"
