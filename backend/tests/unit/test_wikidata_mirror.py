from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select

from app.models.wikidata_mirror import WikidataMirrorEntity, WikidataMirrorScope
from app.pipeline.wikidata_mirror import filter_entities, local_probe_candidates, store_entities
from app.pipeline.wikidata_mirror_sync import (
    MirrorReadUnavailableError,
    discover_candidates,
    discovery_queries,
    refresh_qids,
)

pytestmark = pytest.mark.usefixtures("db_session")


def claim(pid, value, *, rank="normal"):
    return {
        "type": "statement",
        "rank": rank,
        "mainsnak": {
            "property": pid,
            "snaktype": "value",
            "datavalue": {"value": value},
        },
        "qualifiers": {"P1932": [{"snaktype": "value", "datavalue": {"value": "Source name"}}]},
        "references": [
            {"snaks": {"P854": [{"datavalue": {"value": "https://example.org/catalogue"}}]}}
        ],
    }


def entity(qid="Q123", revision=10, identifier="12345"):
    return {
        "id": qid,
        "type": "item",
        "lastrevid": revision,
        "labels": {"en": {"language": "en", "value": "Example person"}},
        "aliases": {"he": [{"language": "he", "value": "שם לדוגמה"}]},
        "descriptions": {"en": {"language": "en", "value": "scribe"}},
        "sitelinks": {},
        "claims": {
            "P31": [claim("P31", {"id": "Q5"})],
            "P214": [claim("P214", identifier)],
        },
    }


async def put(db, rows, observed=None):
    result = await store_entities(
        db, rows, observed_at=observed or datetime.now(UTC), source="fixture"
    )
    await db.commit()
    return result


@pytest.mark.asyncio
async def test_full_source_survives_while_filters_use_active_conjunctive_facts(db_session):
    original = entity()
    original["claims"]["P214"].append(claim("P214", "deprecated-id", rank="deprecated"))
    assert await put(db_session, [original]) == 1
    rows = await filter_entities(db_session, [("P214", "12345"), ("P31", "Q5")])
    assert rows[0].document == original
    assert not await filter_entities(db_session, [("P214", "deprecated-id")])
    assert not await filter_entities(db_session, [("P214", "12345"), ("P31", "Q99")])
    assert await filter_entities(db_session, [("@name", "EXAMPLE  PERSON")])
    assert await filter_entities(db_session, [("@name", "שם לדוגמה")])


@pytest.mark.asyncio
async def test_new_revision_replaces_indexes_and_old_snapshots_cannot_rollback(db_session):
    first = entity()
    now = datetime.now(UTC)
    assert await put(db_session, [first], now - timedelta(hours=1)) == 1
    updated = entity(revision=11, identifier="new-id")
    assert await put(db_session, [updated], now) == 1
    assert not await filter_entities(db_session, [("P214", "12345")])
    assert await filter_entities(db_session, [("P214", "new-id")])
    assert await put(db_session, [first], now + timedelta(seconds=1)) == 0
    conflict = deepcopy(updated)
    conflict["labels"]["en"]["value"] = "Different data at the same revision"
    assert await put(db_session, [conflict], now + timedelta(seconds=2)) == 0
    assert await filter_entities(db_session, [("@name", "Example person")])
    assert not await filter_entities(db_session, [("@name", "Different data at the same revision")])


@pytest.mark.asyncio
async def test_stale_data_is_explicit_and_never_a_fresh_candidate(db_session, _sessionmaker):
    await put(db_session, [entity()], datetime.now(UTC) - timedelta(days=8))
    assert not await filter_entities(db_session, [("P214", "12345")])
    assert await filter_entities(db_session, [("P214", "12345")], include_stale=True)
    assert await local_probe_candidates(_sessionmaker, [("P214", "12345")]) == {}


@pytest.mark.asyncio
async def test_foreign_candidates_require_current_proof_and_do_not_authorize_adoption(
    db_session, _sessionmaker
):
    from app.pipeline.wikidata_duplicate_probe import _adoptable_qids

    await put(db_session, [entity()])
    found = await local_probe_candidates(_sessionmaker, [("P214", "12345")])
    candidate = found[("P214", "12345")][0]
    assert candidate["qid"] == "Q123"
    assert candidate["coverage"] == "partial"
    assert candidate["requires_curator_confirmation"] == "true"
    assert "ours" not in candidate
    assert _adoptable_qids({"candidates": [candidate]}) == set()


@pytest.mark.asyncio
async def test_many_local_filters_use_bounded_queries(db_session, _sessionmaker, _engine):
    await put(db_session, [entity(qid=f"Q{i + 100}", identifier=str(i)) for i in range(80)])
    queries = []

    def observe(conn, cursor, statement, parameters, context, many):
        queries.append(statement)

    event.listen(_engine.sync_engine, "before_cursor_execute", observe)
    try:
        found = await local_probe_candidates(_sessionmaker, [("P214", str(i)) for i in range(80)])
    finally:
        event.remove(_engine.sync_engine, "before_cursor_execute", observe)
    assert len(found) == 80
    assert len(queries) <= 4


@pytest.mark.asyncio
async def test_local_hit_avoids_remote_queries_but_a_miss_stays_unavailable(
    db_session, _sessionmaker
):
    from app.pipeline.wikidata_duplicate_probe import attach_duplicate_evidence

    await put(db_session, [entity()])
    items = [
        {
            "entity_type": "person",
            "labels": {"en": "Example person"},
            "statements": [{"property_id": "P214", "value": "12345"}],
        }
    ]

    def unavailable(url, *, timeout):
        raise RuntimeError("Remote endpoint unavailable")

    stats = await attach_duplicate_evidence(_sessionmaker, items, fetch=unavailable)
    assert stats["local_mirror"] == 1
    assert items[0]["_wikidata_existence"]["status"] == "candidates_found"
    missing = [
        {
            "entity_type": "person",
            "labels": {"en": "Other"},
            "statements": [{"property_id": "P214", "value": "absent-locally"}],
        }
    ]
    await attach_duplicate_evidence(_sessionmaker, missing, fetch=unavailable)
    assert missing[0]["_wikidata_existence"]["status"] != "absent"


@pytest.mark.asyncio
async def test_remote_failure_does_not_erase_a_local_positive(db_session, _sessionmaker):
    from app.pipeline.wikidata_duplicate_probe import attach_duplicate_evidence

    await put(db_session, [entity()])
    items = [
        {
            "entity_type": "person",
            "labels": {"en": "Example person"},
            "statements": [
                {"property_id": "P214", "value": "12345"},
                {"property_id": "P244", "value": "another-key"},
            ],
        }
    ]

    def fail(url, *, timeout):
        raise RuntimeError("Remote read unavailable")

    await attach_duplicate_evidence(_sessionmaker, items, fetch=fail)
    evidence = items[0]["_wikidata_existence"]
    assert evidence["status"] == "candidates_found"
    assert evidence["candidates"][0]["qid"] == "Q123"
    assert evidence["coverage"] == "local_partial"


@pytest.mark.asyncio
async def test_live_probe_preserves_full_entity_for_later_local_filters(_sessionmaker):
    from urllib.parse import parse_qs, urlparse

    from app.pipeline.wikidata_duplicate_probe import attach_duplicate_evidence

    original = entity()

    def fetch(url, *, timeout):
        action = parse_qs(urlparse(url).query)["action"][0]
        if action == "query":
            return {"query": {"search": [{"title": "Q123"}]}}
        assert action == "wbgetentities"
        return {"entities": {"Q123": original}}

    items = [
        {
            "entity_type": "person",
            "labels": {"en": "Example person"},
            "statements": [{"property_id": "P214", "value": "12345"}],
        }
    ]
    await attach_duplicate_evidence(_sessionmaker, items, fetch=fetch)
    async with _sessionmaker() as db:
        stored = await db.get(WikidataMirrorEntity, "Q123")
        assert stored is not None and stored.document == original
        await db.rollback()


def test_snapshot_cli_retains_redirect_scope_and_resumes_without_a_network_call(tmp_path):
    import json
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path

    folder = Path(__file__).resolve().parents[2]
    snapshot = tmp_path / "snapshot.json"
    database = tmp_path / "mirror.sqlite"
    output = tmp_path / "result.json"
    start = datetime.now(UTC) - timedelta(hours=1)
    snapshot.write_text(
        json.dumps(
            {
                "started_at": start.isoformat(),
                "completed_at": datetime.now(UTC).isoformat(),
                "entities": {"Q123": entity(), "Q999": entity()},
            }
        )
    )
    command = [
        sys.executable,
        str(folder / "scripts/sync_wikidata_mirror.py"),
        "--database-file",
        str(database),
        "--snapshot",
        str(snapshot),
        "--filter",
        "P214=12345",
        "--output",
        str(output),
    ]
    for _ in range(2):
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)  # noqa: S603
        assert result.returncode == 0, result.stdout
        data = json.loads(output.read_text())
        assert data["imported"] == 1 and data["public_writes"] == 0
        assert data["redirects_require_review"] == {"Q999": "Q123"}
        assert [row["qid"] for row in data["matches"]] == ["Q123"]
    with sqlite3.connect(database) as db:
        status, progress = db.execute(
            "select status, progress from wikidata_mirror_scopes"
        ).fetchone()
        assert status == "partial"
        assert json.loads(progress)["visited"] == 1


@pytest.mark.asyncio
async def test_refresh_batches_full_entities_without_a_credential_and_keeps_source_time(
    monkeypatch, _sessionmaker
):
    from app.pipeline import wikidata_mirror_sync as sync

    calls = []
    observed = datetime.now(UTC) - timedelta(hours=2)

    def fetch(params):
        calls.append(params)
        ids = params["ids"].split("|")
        return {
            "observed_at": observed.isoformat(),
            "response": {"entities": {qid: entity(qid) for qid in ids}},
        }

    monkeypatch.setattr(sync, "_request_json", fetch)
    qids = [f"Q{i + 1000}" for i in range(51)]
    result = await refresh_qids(_sessionmaker, qids)
    assert result["stored"] == 51 and len(calls) == 2
    assert all(
        len(call["ids"].split("|")) <= 50 and call["action"] == "wbgetentities" for call in calls
    )
    assert all("claims" in call["props"] and "sitelinks" in call["props"] for call in calls)
    async with _sessionmaker() as db:
        row = await db.get(WikidataMirrorEntity, qids[0])
        assert row.observed_at.replace(tzinfo=UTC) == observed
        await db.rollback()
    await refresh_qids(_sessionmaker, qids)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_an_api_failure_stays_partial_and_never_erases_the_snapshot(
    monkeypatch, db_session, _sessionmaker
):
    from app.pipeline import wikidata_mirror_sync as sync

    await put(db_session, [entity()])

    def fail(params):
        raise MirrorReadUnavailableError("maxlag")

    monkeypatch.setattr(sync, "_request_json", fail)
    with pytest.raises(MirrorReadUnavailableError):
        await refresh_qids(_sessionmaker, ["Q123"], refresh=True)
    async with _sessionmaker() as db:
        assert await db.get(WikidataMirrorEntity, "Q123")
        scope = (await db.execute(select(WikidataMirrorScope))).scalar_one()
        assert scope.status == "partial"
        await db.rollback()


@pytest.mark.asyncio
async def test_confirmed_missing_item_invalidates_local_positive(
    monkeypatch, db_session, _sessionmaker
):
    from app.pipeline import wikidata_mirror_sync as sync

    await put(db_session, [entity()])
    monkeypatch.setattr(
        sync,
        "_request_json",
        lambda params: {
            "observed_at": datetime.now(UTC).isoformat(),
            "response": {"entities": {"Q123": {"id": "Q123", "missing": ""}}},
        },
    )
    result = await refresh_qids(_sessionmaker, ["Q123"], refresh=True)
    assert result["missing"] == 1
    async with _sessionmaker() as db:
        assert not await filter_entities(db, [("P214", "12345")])
        await db.rollback()


@pytest.mark.asyncio
async def test_discovery_resumes_a_capped_query_without_remote_class_joins(
    monkeypatch, _sessionmaker
):
    from app.pipeline import wikidata_mirror_sync as sync

    calls = []

    def fetch(params):
        calls.append(params)
        offset = params["sroffset"]
        return {
            "observed_at": datetime.now(UTC).isoformat(),
            "response": {
                "query": {"search": [{"title": "Q123" if offset == 0 else "Q456"}]},
                **({"continue": {"sroffset": 50}} if offset == 0 else {}),
            },
        }

    monkeypatch.setattr(sync, "_request_json", fetch)
    items = [
        {
            "entity_type": "person",
            "labels": {"en": "Person"},
            "statements": [{"property_id": "P214", "value": "12345"}],
        }
    ]
    first = await discover_candidates(_sessionmaker, items, max_pages=1)
    assert first["partial_queries"] == 1 and first["qids"] == ["Q123"]
    second = await discover_candidates(_sessionmaker, items, max_pages=1)
    assert second["partial_queries"] == 0 and second["qids"] == ["Q123", "Q456"]
    assert [row["sroffset"] for row in calls] == [0, 50]
    assert all("P31" not in row["srsearch"] for row in calls)
    assert all(row["action"] == "query" for row in calls)


def test_discovery_does_not_restrict_candidates_to_our_creator():
    items = [
        {
            "entity_type": "work",
            "labels": {"he": "ספר לדוגמה"},
            "statements": [
                {"property_id": "P31", "value": "Q47461344"},
                {"property_id": "P1476", "value": "ספר לדוגמה"},
            ],
        }
    ]
    queries = discovery_queries(items)
    assert queries == ['"ספר לדוגמה"']
    assert all("Alexander" not in query and "P31" not in query for query in queries)


@pytest.mark.parametrize(
    "params",
    [
        {"action": "wbeditentity", "id": "Q123"},
        {"action": "query", "list": "search", "srnamespace": 0, "token": "forbidden"},
    ],
)
def test_mirror_transport_cannot_send_an_edit_or_a_token(monkeypatch, params):
    from app.pipeline import wikidata_mirror_sync as sync

    def unexpected_session():
        pytest.fail("An unsupported request cannot reach the network.")

    monkeypatch.setattr(sync.requests, "Session", unexpected_session)
    with pytest.raises(ValueError):
        sync._request_json(params)


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["missing_fields", "bad_revision", "bad_qid"])
async def test_incomplete_snapshots_cannot_replace_the_index(db_session, defect):
    bad = entity()
    if defect == "missing_fields":
        del bad["claims"]
    elif defect == "bad_revision":
        bad["lastrevid"] = True
    else:
        bad["id"] = "not-a-qid"
    with pytest.raises(ValueError):
        await put(db_session, [bad])
    assert not (await db_session.execute(select(WikidataMirrorEntity))).scalars().all()
