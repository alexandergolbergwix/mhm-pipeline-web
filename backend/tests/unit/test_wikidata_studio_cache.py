"""Regression guards for stale Wikidata Studio cache payloads."""

from app.pipeline.wikidata_studio import studio_cache_has_stale_validation


def test_identifierless_person_cache_is_marked_stale() -> None:
    cached_items = [
        {
            "local_id": "mazal:987007257211705171",
            "entity_type": "person",
            "validation_issues": [
                {
                    "code": "NO_IDENTIFIER",
                    "severity": "error",
                    "message": "Person item has no external identifier",
                },
            ],
        },
    ]

    assert studio_cache_has_stale_validation(cached_items) is True


def test_clean_person_cache_is_not_marked_stale() -> None:
    cached_items = [
        {
            "local_id": "mazal:987007257211705171",
            "entity_type": "person",
            "statements": [{"property": "P8189", "value": "987007257211705171"}],
            "validation_issues": [],
        },
    ]

    assert studio_cache_has_stale_validation(cached_items) is False
