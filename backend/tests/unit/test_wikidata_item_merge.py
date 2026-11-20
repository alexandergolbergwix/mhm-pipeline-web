"""The drawer accept binds an existing QID everywhere the item is judged."""

from __future__ import annotations

import pytest

from app.pipeline.wikidata_item_merge import apply_wikidata_item_override
from app.pipeline.wikidata_studio import _apply_override
from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.item_validator import validate_item


def test_drawer_accept_binds_existing_qid_when_absent() -> None:
    out = apply_wikidata_item_override(
        {"local_id": "QDraft_Person_976", "statements": []},
        {"accepted_foreign_qid": "Q1176777", "accept_foreign_modify": True},
    )
    assert out["existing_qid"] == "Q1176777"


def test_drawer_accept_never_replaces_an_existing_binding() -> None:
    out = apply_wikidata_item_override(
        {"local_id": "QDraft_Person_1", "existing_qid": "Q1", "statements": []},
        {"accepted_foreign_qid": "Q2"},
    )
    assert out["existing_qid"] == "Q1"


@pytest.mark.parametrize(("property_id", "value"), [("P528", "MS 456"), ("P1574", "Q21")])
def test_known_stale_catalogue_edit_preserves_source_and_review_evidence(
    property_id: str, value: str,
) -> None:
    local_id = "QDraft_MS_990000896070205171"
    statements = [WikidataStatement("P31", "Q87167", "item") for _ in range(17)]
    value_type = "string" if property_id == "P528" else "item"
    statements.append(WikidataStatement(property_id, value, value_type))
    native = WikidataItem(local_id=local_id, statements=statements)
    serialized = {"local_id": local_id, "statements": [
        {"property": row.property_id, "value": row.value} for row in statements
    ]}
    override = {"statement_edits": {"17": {"value": "Q141754945"}}}

    _apply_override(native, override)
    result = apply_wikidata_item_override(serialized, override)

    assert native.statements[17].value == value
    assert result["statements"][17] == {"property": property_id, "value": value}
    assert any(issue.code == "STALE_CATALOGUE_EDIT" for issue in validate_item(native))
    assert result["validation_issues"][0]["code"] == "STALE_CATALOGUE_EDIT"
    assert override["statement_edits"]["17"] == {"value": "Q141754945"}


def test_other_catalogue_qid_edits_still_fail_validation() -> None:
    item = WikidataItem(local_id="QDraft_MS_990000896070205171", statements=[
        WikidataStatement("P528", "MS 456", "string"),
    ])
    _apply_override(item, {"statement_edits": {"0": {"value": "Q141754945"}}})
    assert any(issue.code == "CATALOG_CODE_IS_QID" for issue in validate_item(item))
