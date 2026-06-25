"""VIAF / Mazal cross-source guard tests."""
from __future__ import annotations

from app.pipeline import authority_hardening as ah
from app.pipeline.authority_hardening import (
    apply_hardening_guards,
    guard_viaf_date_mismatch,
)


def test_viaf_date_mismatch_fires_on_conflict() -> None:
    verdict = guard_viaf_date_mismatch(
        marc_dates="1138-1204",
        birth_year=1800,
        death_year=1870,
        viaf_id="12345678",
    )
    assert verdict.fired
    assert verdict.flag == "viaf_date_mismatch"


def test_cross_source_strips_viaf_in_apply() -> None:
    candidate = {
        "matched_name": "Test",
        "entity_text": "Test",
        "entity_kind": "person",
        "confidence": "high",
        "mazal_id": "9870",
        "viaf_id": "12345678",
        "wikidata_qid": "",
        "payload": {
            "main_marc_tag": "100",
            "guard_flags": [],
        },
    }
    hardened = apply_hardening_guards(
        candidate,
        context=ah.HardeningContext(
            marc_dates="1138-1204",
            birth_year=1800,
            death_year=1870,
        ),
    )
    assert hardened["viaf_id"] == ""
    flags = hardened["payload"]["guard_flags"]
    assert "cross_source_conflict" in flags or "viaf_date_mismatch" in flags
