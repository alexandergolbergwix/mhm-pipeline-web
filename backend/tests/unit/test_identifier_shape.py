"""Rule W-167 — an authority identifier must look like the register it claims.

Run 48ba6c13 published 105 statements of `P214 = 987007…` — an 18-digit NLI J9U
number asserted as a VIAF ID, which violates P214's own Wikidata format
constraint. The HMO `viaf_id` property was taken at face value by the P/Q mapper.
For 48 of those persons P214 was the ONLY identifier, so it was also what made
them publishable under Rules W-153 / W-154 — dropping it would have removed the
person, so the value is routed to the property it belongs to instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from converter.wikidata.hmo_wikidata_pq_mapper import route_authority_identifier


class TestRouting:
    def test_a_j9u_number_claimed_as_viaf_is_rerouted(self) -> None:
        assert route_authority_identifier("P214", "987007310299505171") == (
            "P8189", "987007310299505171",
        )

    def test_a_real_viaf_id_stays_on_p214(self) -> None:
        assert route_authority_identifier("P214", "61512894") == ("P214", "61512894")

    def test_a_j9u_number_on_p8189_is_left_alone(self) -> None:
        assert route_authority_identifier("P8189", "987007310299505171") == (
            "P8189", "987007310299505171",
        )

    @pytest.mark.parametrize("value", ["abc", "", "   ", "0", "viaf/123"])
    def test_a_value_matching_no_register_is_refused(self, value) -> None:
        """An identifier we cannot attribute is worse than an absent one."""
        assert route_authority_identifier("P214", value) is None

    def test_the_mapper_reroutes_rather_than_dropping(self) -> None:
        from converter.wikidata.hmo_wikidata_pq_mapper import map_hmo_claim_to_wikidata

        mapped = map_hmo_claim_to_wikidata(
            {
                "property_uri": "https://w3id.org/mhm/ontology#viaf_id",
                "value": "987007310299505171",
                "value_type": "string",
            },
            entity_type="person",
        )
        assert mapped is not None
        assert mapped.property_id == "P8189"
        assert mapped.value == "987007310299505171"


@dataclass
class _Stmt:
    property_id: str
    value: str = ""


@dataclass
class _Item:
    local_id: str = "QDraft_Person_119"
    entity_type: str = "person"
    labels: dict = field(default_factory=lambda: {"he": "פלוני"})
    statements: list = field(default_factory=list)
    records: list = field(default_factory=list)


class TestGate:
    def _blocking(self, *statements: _Stmt) -> list[str]:
        from app.pipeline.wikidata_export_quality_gate import (
            wikidata_export_quality_report,
        )

        item = _Item(statements=[_Stmt("P31", "Q5"), *statements])
        return wikidata_export_quality_report([item])["blocking"]

    def test_a_j9u_on_p214_blocks_the_build(self) -> None:
        findings = self._blocking(_Stmt("P214", "987007310299505171"))
        assert any(f.startswith("IDENTIFIER_WRONG_PROPERTY") for f in findings)

    def test_an_unrecognisable_identifier_blocks_the_build(self) -> None:
        findings = self._blocking(_Stmt("P214", "not-an-id"))
        assert any(f.startswith("IDENTIFIER_SHAPE_UNRECOGNISED") for f in findings)

    def test_correctly_shaped_identifiers_pass(self) -> None:
        findings = self._blocking(
            _Stmt("P214", "61512894"), _Stmt("P8189", "987007310299505171"),
        )
        assert not any("IDENTIFIER" in f for f in findings)
