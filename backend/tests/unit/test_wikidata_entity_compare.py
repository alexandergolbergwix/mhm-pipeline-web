"""Unit tests for Wikidata vs Studio compare."""

from __future__ import annotations

from app.pipeline.wikidata_entity_compare import build_compare


def test_build_compare_label_conflict() -> None:
    studio = {
        "local_id": "mazal:9870",
        "labels": {"en": "Simon Dubnov", "he": "דובנוב, שמעון"},
        "descriptions": {},
        "statements": [],
    }
    live = {
        "labels": {
            "en": {"language": "en", "value": "Simon Dubnov"},
            # A live "he" label that DIFFERS from studio's is what makes
            # this a genuine conflict — a live label absent entirely would
            # be "studio_only" (nothing to conflict with).
            "he": {"language": "he", "value": "דובנאוו, שמעון"},
        },
        "descriptions": {},
        "claims": {},
    }
    result = build_compare(studio, live, "Q12345")
    he_row = next(r for r in result.rows if r.kind == "label" and r.key == "he")
    assert he_row.status == "conflict"
    assert he_row.studio_value == "דובנוב, שמעון"


def test_build_compare_wikidata_only_statement() -> None:
    studio = {
        "local_id": "person::x",
        "labels": {"en": "Test"},
        "statements": [{"property_id": "P31", "value": "Q5", "value_id": "Q5"}],
    }
    live = {
        "labels": {"en": {"language": "en", "value": "Test"}},
        "descriptions": {},
        "claims": {
            "P31": [{
                "mainsnak": {
                    "snaktype": "value",
                    "property": "P31",
                    "datavalue": {
                        "type": "wikibase-entityid",
                        "value": {"entity-type": "item", "id": "Q5"},
                    },
                },
            }],
            "P214": [{
                "mainsnak": {
                    "snaktype": "value",
                    "property": "P214",
                    "datavalue": {"type": "string", "value": "123"},
                },
            }],
        },
    }
    result = build_compare(studio, live, "Q99")
    p214 = next(r for r in result.rows if r.key.startswith("P214|"))
    assert p214.status == "wikidata_only"
