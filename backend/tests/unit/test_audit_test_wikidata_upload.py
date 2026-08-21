"""Pure helpers for the test.wikidata.org live-readiness audit."""

from __future__ import annotations

from scripts.audit_test_wikidata_upload import (
    count_wikibase_claims,
    entity_has_live_wikidata_uri,
    live_value_uris,
    unresolved_local_refs,
)


def test_count_wikibase_claims() -> None:
    entity = {
        "claims": {
            "P31": [{}, {}],
            "P217": [{}],
        },
    }
    assert count_wikibase_claims(entity) == 3
    assert count_wikibase_claims({"missing": ""}) == 0


def test_live_uri_in_test_claim_is_detected() -> None:
    entity = {
        "claims": {
            "P31": [{
                "mainsnak": {
                    "datavalue": {
                        "value": {
                            "id": "Q5",
                            "entity-type": "item",
                        },
                    },
                },
            }],
            "P571": [{
                "mainsnak": {
                    "datavalue": {
                        "value": {
                            "calendarmodel": "http://www.wikidata.org/entity/Q1985727",
                        },
                    },
                },
            }],
        },
    }
    assert entity_has_live_wikidata_uri(entity) is False
    assert live_value_uris(entity) == []
    entity["claims"]["P31"][0]["mainsnak"]["datavalue"]["value"] = (
        "http://www.wikidata.org/entity/Q5"
    )
    assert entity_has_live_wikidata_uri(entity) is True


def test_unresolved_local_refs() -> None:
    class _Stmt:
        value = "__LOCAL:person:a"
        qualifiers = [{"value": "ok"}]
        references = [{"value": "__LOCAL:work:1"}]

    assert unresolved_local_refs([_Stmt()]) == [
        "__LOCAL:person:a",
        "__LOCAL:work:1",
    ]


def test_identity_clash_is_blocker() -> None:
    from scripts.audit_test_wikidata_upload import audit_one

    studio = {
        "local_id": "QDraft_Person_71",
        "entity_type": "person",
        "existing_qid": "Q209579",
        "labels": {"he": "ויטוריו אמדיאו"},
        "statements": [{
            "property_id": "P8189",
            "value": "1",
            "value_type": "external-id",
        }],
    }
    live = {"labels": {"en": {"value": "Victor Amadeus II of Savoy"}}}
    row = audit_one(
        {"local_id": "QDraft_Person_71", "status": "created", "qid": "Q1"},
        studio,
        {"labels": {"en": {"value": "x"}}, "claims": {"P31": [{}]}},
        live,
    )
    assert any(str(b).startswith("identity_clash:") for b in row["blockers"])
    assert row["ready_for_live"] is False

