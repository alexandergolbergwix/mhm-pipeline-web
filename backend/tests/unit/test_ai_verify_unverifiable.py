"""Authority verify: synthetic abstain for matches without authority ids."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.routers.ai_verify import (
    _match_has_authority_id,
    _synthetic_no_authority_verdict,
)


def _match(**kwargs: object) -> SimpleNamespace:
    defaults = {
        "id": uuid.uuid4(),
        "control_number": "MS-1",
        "entity_text": "Test Entity",
        "matched_name": "Test Entity",
        "role": "author",
        "mazal_id": "",
        "viaf_id": "",
        "wikidata_qid": "",
        "confidence": "low",
        "source": "",
        "payload": {},
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestMatchHasAuthorityId:
    def test_false_when_all_ids_empty(self) -> None:
        m = _match(mazal_id="", viaf_id="", wikidata_qid="")
        assert _match_has_authority_id(m) is False

    def test_true_for_mazal(self) -> None:
        m = _match(mazal_id="123")
        assert _match_has_authority_id(m) is True

    def test_true_for_kima_in_payload(self) -> None:
        m = _match(payload={"kima_id": "K-42"})
        assert _match_has_authority_id(m) is True


class TestSyntheticNoAuthorityVerdict:
    def test_abstain_shape(self) -> None:
        mid = uuid.uuid4()
        m = _match(id=mid, entity_text="Unmatched Name")
        v = _synthetic_no_authority_verdict(m)
        assert v["verdict"]["overall"] == "abstain"
        assert v["candidate"]["_match_id"] == str(mid)
        assert v["synthetic_reason"] == "no_authority_id"
        assert "No Mazal" in v["verdict"]["reasoning"]
