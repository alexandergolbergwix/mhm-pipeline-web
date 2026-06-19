"""Tests for post-enrich personality cross-link and Wikidata crosscheck passes."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.pipeline.authority_post_enrich import (
    apply_personality_cross_links,
    apply_wikidata_crosscheck_pass,
)


def _row(
    *,
    cn: str,
    text: str,
    role: str,
    kind: str = "person",
    mazal_id: str = "",
    viaf_id: str = "",
    wikidata_qid: str = "",
    payload: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        control_number=cn,
        entity_text=text,
        role=role,
        entity_kind=kind,
        matched_name=text,
        mazal_id=mazal_id,
        viaf_id=viaf_id,
        wikidata_qid=wikidata_qid,
        confidence="high",
        payload=dict(payload or {}),
    )


def test_personality_cross_link_on_subject_row() -> None:
    author = _row(
        cn="99001",
        text="אלוני, נחמיה",
        role="author",
        mazal_id="987007257676505171",
        payload={"main_marc_tag": "100"},
    )
    subject = _row(
        cn="99001",
        text="אלוני, נחמיה",
        role="subject",
        mazal_id="987001234567",
        payload={"main_marc_tag": "150"},
    )
    n = apply_personality_cross_links([author, subject])
    assert n == 1
    assert subject.payload["linked_personality_mazal_id"] == "987007257676505171"


def test_wikidata_crosscheck_clears_bad_qid(monkeypatch) -> None:
    from app.pipeline import authority_hardening

    def _fake_crosscheck(**_kwargs):  # noqa: ANN003
        return authority_hardening.GuardVerdict(
            fired=True,
            new_confidence="medium",
            reason="label mismatch",
            flag="wikidata_crosscheck_fail",
        )

    monkeypatch.setattr(authority_hardening, "guard_wikidata_crosscheck", _fake_crosscheck)
    row = _row(
        cn="99001",
        text="אלוני, נחמיה",
        role="author",
        mazal_id="987007257676505171",
        viaf_id="12345",
        wikidata_qid="Q59530677",
        payload={"preferred_name_lat": "Allony"},
    )
    n = apply_wikidata_crosscheck_pass([row])
    assert n == 1
    assert row.wikidata_qid == ""
    assert row.viaf_id == ""
