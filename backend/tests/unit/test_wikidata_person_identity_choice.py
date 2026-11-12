"""A source choice cannot change a global identity or exceed its source scope."""

from copy import deepcopy

import pytest

from app.pipeline.wikidata_person_identity_choice import (
    apply_choice,
    make_choice,
    validate_incoming_scope,
)

CN1, CN2 = "990001028160205171", "990035802410205171"
NLI = "987007507328605171"


def case():
    item = {
        "local_id": "person1",
        "entity_type": "person",
        "existing_qid": "Q20",
        "labels": {"he": "סעדיה בן שלמה"},
        "aliases": {},
        "records": [CN1, CN2, "other"],
        "statements": [
            {"property_id": "P8189", "value": NLI, "value_type": "external-id"},
            {"property_id": "P569", "value": "unproved", "value_type": "time"},
        ],
        "authority_evidence": [{"accepted": False, "identifier": "Q20"}],
    }
    source = {"source_fingerprint": "a" * 64, "source_uri": "https://example.org/person1"}
    records = {CN1: {"700$a": "סעדיה בן שלמה", "700$e": "scribe"}, CN2: {"600$a": "סעדיה בן שלמה"}}
    proof = {
        "qid": "Q100",
        "authority_id": NLI,
        "unique": True,
        "revision": 21,
        "entity_sha256": "b" * 64,
    }
    return item, source, records, proof


def choice(item, source, records, proof):
    return make_choice(
        item,
        source,
        records,
        proof,
        control_numbers=[CN1, CN2],
        expected_qid="Q20",
        expected_fingerprint="a" * 64,
        approved=True,
    )


def test_choice_is_source_scoped_and_does_not_retain_unproved_person_facts():
    item, source, records, proof = case()
    before = deepcopy(item)
    selected = choice(item, source, records, proof)
    result = apply_choice(item, selected, source=source, records=records)
    assert result["existing_qid"] == "Q100"
    assert result["records"] == [CN1, CN2]
    assert {s["property_id"] for s in result["statements"]} == {"P31", "P8189"}
    assert len(result["statements"][1]["reference_blocks"]) == 2
    assert result["authority_evidence"][-1]["reference_only"] is True
    assert item == before
    assert apply_choice(result, selected, source=source, records=records) == result


@pytest.mark.parametrize(
    "failure", ["patronymic", "role", "source", "qid", "approval", "ambiguous"]
)
def test_choice_rejects_changed_or_unproved_identity(failure):
    item, source, records, proof = case()
    if failure == "patronymic":
        records[CN1]["700$a"] = "שלמה בן סעדיה"
    elif failure == "source":
        source["source_fingerprint"] = "c" * 64
    elif failure == "qid":
        item["existing_qid"] = "Q21"
    elif failure == "ambiguous":
        proof["unique"] = False
    if failure == "approval":
        with pytest.raises(ValueError):
            make_choice(
                item,
                source,
                records,
                proof,
                control_numbers=[CN1],
                expected_qid="Q20",
                expected_fingerprint="a" * 64,
                approved=False,
            )
    elif failure == "role":
        selected = choice(item, source, records, proof)
        records[CN1]["700$e"] = "owner"
        with pytest.raises(ValueError):
            apply_choice(item, selected, source=source, records=records)
    else:
        with pytest.raises(ValueError):
            choice(item, source, records, proof)


def test_comma_name_is_supported_without_reversal_of_a_patronymic():
    item, source, records, proof = case()
    item["labels"]["he"] = "יחיא בן דוד אלחמדי"
    records[CN1]["700$a"] = "אלחמדי, יחיא בן דוד"
    records[CN2]["600$a"] = "אלחמדי, יחיא בן דוד"
    assert choice(item, source, records, proof)["target_qid"] == "Q100"


def test_an_incoming_relation_must_have_a_nonempty_subset_of_the_scope():
    item, source, records, proof = case()
    selected = choice(item, source, records, proof)
    apply = apply_choice(item, selected, source=source, records=records)
    incoming = {
        "local_id": "ms",
        "records": [CN1],
        "statements": [{"property_id": "P50", "value": "__LOCAL:person1", "value_type": "item"}],
    }
    validate_incoming_scope([apply, incoming])
    for scope in ([], ["other"], [CN1, "other"]):
        incoming["records"] = scope
        with pytest.raises(ValueError):
            validate_incoming_scope([apply, incoming])


def test_reference_only_choice_prevents_person_writes_in_both_namespaces(monkeypatch):
    from app.pipeline import wikidata_upload
    from app.pipeline.wikidata_studio import native_item_from_payload

    item, source, records, proof = case()
    native = native_item_from_payload(
        apply_choice(item, choice(item, source, records, proof), source=source, records=records)
    )
    monkeypatch.setattr(wikidata_upload, "_make_reconciler", lambda: object())
    for test in (False, True):
        monkeypatch.setenv("WIKIDATA_TEST_MODE", str(test).lower())
        results = wikidata_upload._reconcile_sync_with_ledger(
            [native], {"wikidata:person:person1": "Q20"}
        )
        assert results[0].existing_qid == (None if test else "Q100")
        prepared = wikidata_upload._prepare_for_upload([native], object(), is_test=test)
        assert prepared[0].block_status == "skipped"
        assert prepared[0].existing_qid == (None if test else "Q100")
        assert native.existing_qid == "Q100"


@pytest.mark.asyncio
async def test_reference_only_outcome_never_changes_the_pooled_global_key(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.pipeline import wikidata_upload
    from app.pipeline.wikidata_studio import native_item_from_payload

    item, source, records, proof = case()
    native = native_item_from_payload(
        apply_choice(item, choice(item, source, records, proof), source=source, records=records)
    )
    audit, ledger = AsyncMock(), AsyncMock()
    monkeypatch.setattr(wikidata_upload, "_record_outcome_audit", audit)
    monkeypatch.setattr(wikidata_upload, "record_ledger_mapping", ledger)
    outcome = SimpleNamespace(local_id="person1", qid="Q100", status="skipped", label="person")
    await wikidata_upload.persist_upload_outcomes(object(), [native], [outcome], audit_ctx=object())
    audit.assert_awaited_once()
    ledger.assert_not_awaited()


@pytest.mark.asyncio
async def test_endpoint_records_the_full_proof_before_the_read_model(monkeypatch):
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.pipeline import marc_verify_context
    from app.pipeline import wikidata_person_identity_choice as choices
    from app.pipeline.hmo_canonical import normalize_live_entity
    from app.routers import wikidata_studio as router

    item, _, records, proof = case()
    snapshot = {
        "local_id": "person1",
        "source_uri": "https://example.org/person1",
        "wikibase_id": "Q4000",
        "labels": item["labels"],
        "control_numbers": [CN1, CN2],
    }
    source = normalize_live_entity(snapshot)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        approved=True,
        person_identity_choice=None,
        labels={},
        descriptions={},
        aliases={},
        add_statements=[],
        remove_statements=[],
        statement_edits={},
        accept_foreign_modify=None,
        accepted_foreign_qid=None,
        ai_verdict=None,
        ai_verdict_at=None,
    )
    answers = [
        SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(snapshot=snapshot)),
        SimpleNamespace(scalar_one_or_none=lambda: row),
        SimpleNamespace(scalar_one=lambda: snapshot),
        SimpleNamespace(scalar_one_or_none=lambda: None),
    ]
    db = SimpleNamespace(execute=AsyncMock(side_effect=answers), commit=AsyncMock())
    monkeypatch.setattr(
        router,
        "_lookup_run_with_access",
        AsyncMock(return_value=SimpleNamespace(project_id=uuid.uuid4())),
    )
    monkeypatch.setattr(router, "_studio_item_from_cache", AsyncMock(return_value=item))
    monkeypatch.setattr(router, "fetch_merged_wikidata_item", AsyncMock(return_value=item))
    monkeypatch.setattr(
        marc_verify_context,
        "load_run_marc_records_scoped",
        AsyncMock(return_value=[{"001": cn, **record} for cn, record in records.items()]),
    )
    monkeypatch.setattr(choices, "load_public_proofs", AsyncMock(return_value={"Q100": proof}))
    events = []

    async def event(*args, **kwargs):
        assert row.person_identity_choice is None
        events.append(kwargs["new_state"])

    monkeypatch.setattr(router, "apply_event", event)
    result = await router.choose_person_identity(
        uuid.uuid4(),
        "person1",
        router.PersonIdentityChoicePayload(
            expected_source_fingerprint=source.source_fingerprint,
            expected_current_qid="Q20",
            target_qid="Q100",
            control_numbers=[CN1, CN2],
        ),
        SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4())),
        db,
    )
    assert result["rebuild_required"] is True
    assert events[0]["person_identity_choice"] == row.person_identity_choice
    db.commit.assert_awaited_once()


def test_a_pooled_alias_cannot_authorize_a_different_person():
    item, source, records, proof = case()
    item["aliases"] = {"he": ["שלמה בן סעדיה"]}
    records[CN1]["700$a"] = "שלמה בן סעדיה"
    with pytest.raises(ValueError, match="exact person name"):
        choice(item, source, records, proof)


def test_a_ledger_only_previous_target_keeps_its_separate_projection_basis():
    item, source, records, proof = case()
    item["projection_existing_qid"] = None
    selected = choice(item, source, records, proof)
    item["existing_qid"] = None
    assert apply_choice(item, selected, source=source, records=records)["existing_qid"] == "Q100"
    item["existing_qid"] = "Q999"
    with pytest.raises(ValueError):
        apply_choice(item, selected, source=source, records=records)


@pytest.mark.asyncio
async def test_single_item_read_uses_one_exact_sql_row(monkeypatch):
    import uuid
    from unittest.mock import AsyncMock

    from app.pipeline import wikidata_item_row_views as rows
    from app.pipeline import wikidata_item_views as views

    db, run = object(), uuid.uuid4()
    backfill = AsyncMock()
    page = AsyncMock(return_value={"items": [{"local_id": "person1", "statements": []}]})
    monkeypatch.setattr(rows, "ensure_rows_backfilled", backfill)
    monkeypatch.setattr(rows, "page_wikidata_items", page)
    monkeypatch.setattr(
        views,
        "fetch_merged_wikidata_items",
        AsyncMock(side_effect=AssertionError("full corpus read")),
    )
    result = await views.fetch_merged_wikidata_item(db, run, "person1", source="canonical")
    assert result == {"local_id": "person1", "statements": []}
    assert page.await_args.kwargs["local_id"] == "person1"
    assert page.await_args.kwargs["full_item"] is True
    assert page.await_args.kwargs["limit"] == 1
